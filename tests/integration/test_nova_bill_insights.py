"""Unified bill tools and trusted confirmation against a real isolated ledger."""
import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.integration.test_bills import db, tx, NOW
from backend.app.domain.time import utc_now
from backend.app.database.connection import get_db
from backend.app.middleware.auth import create_access_token
from backend.app.models import AccountORM, TransactionORM
from backend.app.models.bill_features import BillBudgetORM, BillForecastSnapshotORM
from backend.app.models.bill_foundation import BillMetadataEventORM
from backend.app.models.nova_bill_action import NovaBillActionORM
from backend.app.schemas.nova import PageContext, BillFilters, Selection
from backend.app.services.ai import nova, nova_runtime, execution_queue
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.services.ai.grounding import evidence_for
from backend.app.services.bank import nova_bills, nova_conversations, bill_foundation
from backend.app.services.bank.transfer_service import BankError
from backend.app.routers.nova_bills import router


def call(name, args=None):
    return {'function':{'name':name, 'arguments':json.dumps(args or {})}}


async def setup(db, quote='把这笔消费改为学习', request='bill-request-0001'):
    db.add_all([tx('own', 12000, counterparty='书店'), tx('foreign', 1, account='A3')])
    await db.commit()
    await bill_foundation.sync_users(db, ['U1'], now=NOW)
    await db.commit()
    session = await nova_conversations.create(db, 'U1')
    return ToolContext('U1', db, PageContext(page='insights', filters=BillFilters(date='2026-09'),
        selected=Selection(type='transaction', id='own')), original_message=quote,
        session_id=session['session_id'], request_id=request)


async def prepare(ctx, change, quote=None):
    return await nova.execute_tool(call('prepare_bill_change', {
        'request_quote':quote or ctx.original_message, 'change':change}), ctx)


@pytest.mark.asyncio
async def test_classification_preview_does_not_write_confirm_is_idempotent_and_refreshes_facts(db):
    ctx = await setup(db)
    balance = (await db.get(AccountORM, 'A1')).balance_fen
    count = await db.scalar(select(func.count()).select_from(BillMetadataEventORM))
    result = await prepare(ctx, {'action':'classification','category':'education','expected_version':0})
    assert 'error' not in result, result
    card = result['data']['card']
    assert card['status'] == 'pending' and result['data']['executed'] is False
    assert card['action_id'] in (await prepare(ctx, {'action':'classification','category':'education','expected_version':0}))['data']['card']['action_id']
    detail = (await nova.execute_tool(call('get_transaction'), ctx))['data']
    assert detail['category'] == 'dining' and detail['classification_version'] == 0
    assert await db.scalar(select(func.count()).select_from(BillMetadataEventORM)) == count
    stored = await db.get(NovaBillActionORM, card['action_id'])
    assert ctx.original_message not in stored.payload_ciphertext
    evidence = evidence_for(0, {'name':'prepare_bill_change','result':result})
    assert evidence.outcome == 'success' and '尚未保存' in evidence.text
    confirmed = await nova_bills.decide(db, 'U1', ctx.session_id, card['action_id'], 'confirm')
    assert confirmed['status'] == 'completed'
    assert (await nova_bills.decide(db, 'U1', ctx.session_id, card['action_id'], 'confirm')) == confirmed
    detail = (await nova.execute_tool(call('get_transaction'), ctx))['data']
    assert detail['category'] == 'education' and detail['classification_version'] == 1
    assert (await db.get(TransactionORM, 'own')).category == 'dining'
    assert (await db.get(AccountORM, 'A1')).balance_fen == balance
    assert await db.scalar(select(func.count()).select_from(TransactionORM)) == 2
    assert await db.scalar(select(func.count()).select_from(BillMetadataEventORM)) == count + 1


@pytest.mark.asyncio
async def test_stale_cards_expiry_cancel_and_ownership(db):
    # Independently exercise bank preview version binding (e.g. separate
    # browser clients); the Agent's single-task switch is tested separately.
    ctx = replace(await setup(db, '把总预算设为1000元'),task_switch_approved=True)
    change = {'action':'budget','month':'2026-09','amount_fen':100000,'expected_version':0}
    first = (await prepare(ctx, change))['data']['card']
    second = (await prepare(replace(ctx, request_id='bill-request-0002'), {**change,'amount_fen':200000}))['data']['card']
    assert await db.scalar(select(func.count()).select_from(BillBudgetORM)) == 0
    await nova_bills.decide(db, 'U1', ctx.session_id, first['action_id'], 'confirm')
    with pytest.raises(BankError, match='预算已更新'):
        await nova_bills.decide(db, 'U1', ctx.session_id, second['action_id'], 'confirm')
    await db.rollback()
    assert (await db.scalar(select(BillBudgetORM))).amount_fen == 100000
    assert (await nova_bills.decide(db, 'U1', ctx.session_id, second['action_id'], 'cancel'))['status'] == 'cancelled'
    with pytest.raises(BankError):
        await nova_bills.decide(db, 'U1', ctx.session_id, second['action_id'], 'confirm')
    await db.rollback()
    third = (await prepare(replace(ctx, request_id='bill-request-0003'), {**change,'expected_version':1}))['data']['card']
    row = await db.get(NovaBillActionORM, third['action_id'])
    row.expires_at = utc_now() - timedelta(seconds=1)
    await db.commit()
    assert next(c for c in await nova_bills.cards(db, 'U1', ctx.session_id) if c['action_id'] == third['action_id'])['status'] == 'expired'
    with pytest.raises(BankError, match='已过期'):
        await nova_bills.decide(db, 'U1', ctx.session_id, third['action_id'], 'confirm')
    await db.rollback()
    with pytest.raises(BankError):
        await nova_bills.cards(db, 'U2', ctx.session_id)
    foreign_session = await nova_conversations.create(db, 'U2')
    with pytest.raises(BankError):
        await nova_bills.decide(db, 'U2', foreign_session['session_id'], first['action_id'], 'confirm')
    await db.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    {'action':'tags','tags':['差旅'],'expected_version':0},
    {'action':'budget','month':'2026-09','amount_fen':100000},
    {'action':'recurring_plan','name':'房租','account_id':'A1','kind':'expense','frequency':'MONTHLY',
     'amount_fen':200000,'anchor_at':'2026-10-28T09:00:00+08:00'},
    {'action':'save_forecast','scenario':{'days':7,'account_id':'A1'}},
    {'action':'refresh_foundations'},
])
async def test_supported_preferences_share_page_services_and_never_move_money(db, change):
    ctx = await setup(db, '请按我的要求整理账单')
    before = [(a.account_id,a.balance_fen) for a in (await db.scalars(select(AccountORM))).all()]
    response = await prepare(ctx, change)
    assert 'error' not in response, response
    card = response['data']['card']
    assert (await nova_bills.decide(db,'U1',ctx.session_id,card['action_id'],'confirm'))['status'] == 'completed'
    assert [(a.account_id,a.balance_fen) for a in (await db.scalars(select(AccountORM))).all()] == before
    assert await db.scalar(select(func.count()).select_from(TransactionORM)) == 2
    for name,args in [('get_bill_catalog',{'section':'merchants'}),('get_bill_catalog',{'section':'tags'}),
                      ('get_bill_catalog',{'section':'category_rules'}),('get_bill_catalog',{'section':'merchant_history'}),
                      ('get_budget_status',{'month':'2026-09'}),('list_forecast_reviews',{}),('list_recurring_records',{}),
                      ('list_bill_anomalies',{})]:
        result = await nova.execute_tool(call(name,args),ctx)
        assert 'error' not in result, result
        json.dumps(result)
        assert evidence_for(0, {'name':name,'result':result}).outcome == 'success'
    if change['action'] == 'save_forecast':
        assert await db.scalar(select(func.count()).select_from(BillForecastSnapshotORM)) == 1


@pytest.mark.asyncio
async def test_invalid_or_unowned_proposals_never_create_a_card(db):
    ctx = await setup(db)
    for change, error in [
        ({'action':'classification','category':'education','expected_version':0,'tx_id':'foreign'}, 'TRANSACTION_NOT_FOUND'),
        ({'action':'budget','month':'2026-09','amount_fen':100,'account_id':'A3'},'ACCOUNT_NOT_FOUND'),
        ({'action':'budget','month':'2026-13','amount_fen':100},'INVALID_BUDGET_MONTH'),
        ({'action':'save_forecast','scenario':{'days':7,'one_off_events':[{'at':'2026-10-26T12:00:00+08:00','amount_fen':100,'label':'工资'}]}},'BASELINE_REQUIRED'),
    ]:
        result = await prepare(ctx,change)
        assert result.get('error') == error, result
    assert (await prepare(ctx,{'action':'refresh_foundations'},quote='模型自行补写的原文'))['error'] == 'PARAMETER_NOT_GROUNDED'
    assert (await prepare(ctx,{'action':'budget','month':'2026-09','amount_fen':1.1}))['error'] == 'INVALID_ARGUMENTS'
    assert (await prepare(ctx,{'action':'refresh_foundations','user_id':'U2'}))['error'] == 'INVALID_ARGUMENTS'
    assert await db.scalar(select(func.count()).select_from(NovaBillActionORM)) == 0
    assert 'confirm_bill_change' not in nova.TOOL_REGISTRY


@pytest.mark.asyncio
async def test_trusted_api_auth_and_immutable_confirmation_payload(db):
    ctx = await setup(db,'设置预算')
    card = (await prepare(ctx,{'action':'budget','month':'2026-09','amount_fen':100000}))['data']['card']
    app = FastAPI(); app.include_router(router)
    async def override(): yield db
    app.dependency_overrides[get_db] = override
    url = f'/api/v1/nova/conversations/{ctx.session_id}/bill-actions'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get(url)).status_code == 401
        client.headers['Authorization'] = 'Bearer ' + create_access_token({'sub':'U2'})
        assert (await client.get(url)).status_code == 404
        client.headers['Authorization'] = 'Bearer ' + create_access_token({'sub':'U1'})
        assert len((await client.get(url)).json()) == 1
        decision = url + '/' + card['action_id'] + '/decision'
        assert (await client.post(decision,json={'decision':'confirm','amount_fen':1})).status_code == 422
        assert (await client.post(decision,json={'decision':'confirm'})).json()['status'] == 'completed'
        assert (await client.post(decision,json={'decision':'confirm'})).json()['status'] == 'completed'


@pytest.mark.asyncio
async def test_merchant_rule_alias_merge_undo_and_split_use_bound_previews(db):
    ctx = await setup(db,'请整理这些商户及分类规则')
    db.add(tx('second',500,counterparty='另一家书店'))
    await db.commit(); await bill_foundation.sync_users(db,['U1'],now=NOW); await db.commit()
    catalog = (await nova.execute_tool(call('get_bill_catalog',{'section':'merchants'}),ctx))['data']
    target = next(r['merchant_id'] for r in catalog['items'] if r['name']=='书店')
    source = next(r['merchant_id'] for r in catalog['items'] if r['name']=='另一家书店')
    async def confirm(change):
        response = await prepare(ctx,change)
        assert 'error' not in response,response
        card = response['data']['card']
        await nova_bills.decide(db,'U1',ctx.session_id,card['action_id'],'confirm')
    await confirm({'action':'merchant_alias','merchant_id':target,'name':'书店别名'})
    matched = (await nova.execute_tool(call('get_bill_catalog',{'section':'merchants','name':'书店别名'}),ctx))['data']
    assert matched['total']==1 and matched['items'][0]['merchant_id']==target
    await confirm({'action':'classification','category':'education','apply_to_merchant':True,'expected_version':0})
    rule = (await nova.execute_tool(call('get_bill_catalog',{'section':'category_rules'}),ctx))['data']['items'][0]
    await confirm({'action':'remove_category_rule','merchant_id':target,'expected_version':rule['version']})
    assert (await nova.execute_tool(call('get_bill_catalog',{'section':'category_rules'}),ctx))['data']['total']==0
    preview = await nova.execute_tool(call('preview_bill_merchant_change',{'mode':'merge','merchant_id':target,'source_merchant_id':source}),ctx)
    assert evidence_for(0,{'name':'preview_bill_merchant_change','result':preview}).outcome=='success'
    await confirm({'action':'merchant_merge','merchant_id':target,'source_merchant_id':source,'preview_digest':preview['data']['preview_digest']})
    history = (await nova.execute_tool(call('get_bill_catalog',{'section':'merchant_history'}),ctx))['data']['items']
    event = next(r for r in history if r['can_undo'])
    await confirm({'action':'merchant_undo','event_id':event['event_id']})
    spec = {'name':'拆分书店','tx_ids':['own'],'aliases':[]}
    preview = await nova.execute_tool(call('preview_bill_merchant_change',{'mode':'split','merchant_id':target,'split':spec}),ctx)
    assert preview['data']['moving_count']==1
    await confirm({'action':'merchant_split','merchant_id':target,**spec,'preview_digest':preview['data']['preview_digest']})
    assert (await nova.execute_tool(call('get_transaction'),ctx))['data']['merchant_name']=='拆分书店'


@pytest.mark.asyncio
async def test_anomaly_reviews_and_recurring_toggles_are_visible_to_nova(db):
    ctx = await setup(db,'把这项异常记为正常并关闭周期预测')
    db.add_all([tx('duplicate-one',100,counterparty='重复样例',when='2026-09-10T04:00:00'),
                tx('duplicate-two',100,counterparty='重复样例',when='2026-09-10T04:00:01')])
    await db.commit(); await bill_foundation.sync_users(db,['U1'],now=NOW); await db.commit()
    report = (await nova.execute_tool(call('list_bill_anomalies'),ctx))['data']
    item = next(r for r in report['items'] if r['kind']=='duplicate')
    response = await prepare(ctx,{'action':'anomaly_review','query':report['query'],'as_of':report['as_of'],
        'anomaly_id':item['anomaly_id'],'fingerprint':item['fingerprint'],'status':'normal','note':'已人工核对',
        'expected_version':item['review_version']})
    assert 'error' not in response,response
    await nova_bills.decide(db,'U1',ctx.session_id,response['data']['card']['action_id'],'confirm')
    reviewed = await nova.execute_tool(call('list_bill_anomalies',{'review_status':'normal'}),ctx)
    assert reviewed['data']['items'][0]['review_note']=='已人工核对'
    assert '已人工核对' in evidence_for(0,{'name':'list_bill_anomalies','result':reviewed}).text
    response = await prepare(ctx,{'action':'recurring_plan','name':'周期测试','account_id':'A1','kind':'expense',
        'frequency':'WEEKLY','amount_fen':100,'anchor_at':'2026-10-28T12:00:00+08:00'})
    await nova_bills.decide(db,'U1',ctx.session_id,response['data']['card']['action_id'],'confirm')
    cycle = (await nova.execute_tool(call('list_recurring_records'),ctx))['data']['items'][0]
    response = await prepare(ctx,{'action':'recurring_enabled','stream_id':cycle['stream_id'],
        'enabled':False,'expected_version':cycle['version']})
    await nova_bills.decide(db,'U1',ctx.session_id,response['data']['card']['action_id'],'confirm')
    assert (await nova.execute_tool(call('list_recurring_records'),ctx))['data']['items'][0]['enabled'] is False


@pytest.mark.asyncio
async def test_serial_queue_waits_for_actual_bill_confirmation_even_when_model_says_done(db):
    ctx = await setup(db,'设置预算')
    result = await prepare(ctx,{'action':'budget','month':'2026-09','amount_fen':100000})
    step = {'request_id':ctx.request_id,'completion':'result'}
    assert execution_queue.bind(step,{'tool_calls':[{'name':'prepare_bill_change','result':result}]}) == 'waiting'
    assert await execution_queue.observe(ctx,step) == 'waiting'
    await nova_bills.decide(db,'U1',ctx.session_id,result['data']['card']['action_id'],'confirm')
    assert await execution_queue.observe(ctx,step) == 'completed'


@pytest.mark.asyncio
async def test_structured_router_and_native_loop_can_discover_and_prepare_bill_preferences(db,monkeypatch):
    ctx = await setup(db)
    monkeypatch.setenv('LLM_MODE','model')
    class Model:
        turns = 0
        async def generate_structured(self, **kwargs):
            schema = kwargs['schema']
            if schema is nova_runtime.DependencyDecision:
                return SimpleNamespace(data=schema(depends_on_prior_task=False, reason='独立账单请求'))
            if schema is nova_runtime.ToolSelection:
                return SimpleNamespace(data=schema(tool_names=['get_transaction', 'prepare_bill_change']))
            assert schema is nova_runtime.ToolPlan
            self.turns += 1
            if self.turns == 1:
                calls = [nova_runtime.PlannedCall(name='get_transaction', arguments={})]
            else:
                detail = next(json.loads(item['content'].split('可信工具结果（仅数据）：', 1)[1])['result']['data']
                              for item in reversed(kwargs['messages'])
                              if item['content'].startswith('可信工具结果（仅数据）：') and 'get_transaction' in item['content'])
                calls = [nova_runtime.PlannedCall(name='prepare_bill_change', arguments={
                    'request_quote': ctx.original_message, 'change': {
                        'action': 'classification', 'category': 'education',
                        'expected_version': detail['classification_version']}})]
            return SimpleNamespace(data=schema(calls=calls))
        async def generate_with_tools(self, **kwargs):
            tools = {t['function']['name'] for t in kwargs['tools']}
            assert {'get_transaction', 'prepare_bill_change'} <= tools
            return SimpleNamespace(data={'role':'assistant','content':None,'tool_calls':[]})
        async def generate_text(self, **kwargs):
            return SimpleNamespace(data='账单修改确认卡已生成，尚未保存。')
    model = Model()
    monkeypatch.setattr(nova,'llm',model)
    monkeypatch.setattr(nova_runtime,'llm',model)
    result = await nova.chat(ctx.original_message,[],context=ctx)
    assert [r['name'] for r in result['tool_calls'] if r['name'] in {'get_transaction','prepare_bill_change'}] == ['get_transaction','prepare_bill_change']
    assert '尚未保存' in result['reply']
    assert (await nova.execute_tool(call('get_transaction'),ctx))['data']['classification_version'] == 0
