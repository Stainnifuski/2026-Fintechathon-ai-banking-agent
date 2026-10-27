"""Snapshot fidelity, failure visibility and ownership use real isolated bank tools."""
from copy import deepcopy
import httpx
import pytest
from sqlalchemy import func,select
from tests.integration.test_bills import db
from tests.integration.test_nova_bill_insights import setup,call
from backend.app.services.ai import nova,grounding
from backend.app.services.bank import nova_bill_results as results,nova_conversations,bill_budgets,bill_schedules
from backend.app.schemas.bill_features import BudgetUpdate,RecurringPlan
from backend.app.models.nova_bill_result import NovaBillResultORM
from backend.app.models import AccountORM,TransactionORM
from backend.app.domain.time import utc_now


def reply(trace):
    return grounding.ground_reply({'reply':'{"evidence_ids":[],"clarification":"none","layout":"sections"}',
        'mode':'model','tool_calls':trace},'查询账单')


@pytest.mark.asyncio
@pytest.mark.parametrize(('tool','args','kind'),[
    ('get_bill_analysis',{},'analysis'),('list_transactions',{},'transactions'),
    ('get_transaction',{},'transaction'),('get_budget_status',{'month':'2026-09'},'budgets'),
    ('list_bill_anomalies',{},'anomalies'),('get_subscription_insights',{},'subscriptions'),
    ('project_cashflow',{'days':7},'cashflow'),('list_recurring_records',{},'recurring'),
    ('list_forecast_reviews',{},'forecasts'),('get_bill_catalog',{'section':'merchants'},'catalog'),
])
async def test_real_queries_build_typed_cards_without_changing_ledger(db,tool,args,kind):
    ctx=await setup(db)
    ledger=[(r.tx_id,r.amount_fen) for r in (await db.scalars(select(TransactionORM))).all()]
    balances=[(r.account_id,r.balance_fen) for r in (await db.scalars(select(AccountORM))).all()]
    trace=await nova.execute_tool(call(tool,args),ctx)
    assert not trace.get('error'),trace
    response=reply([{'name':tool,'result':trace}])
    cards=await results.save(db,'U1',ctx.session_id,ctx.request_id,response)
    assert len(cards)==1,(tool,response)
    card=cards[0]
    assert card['kind']==kind and card['as_of']==trace['as_of'] and card['compact_reply']
    assert card['content_hash']==response['grounding']['sources'][0]['content_hash']
    if kind=='analysis':
        assert card['metrics'][0]['amount_fen']==12000
        assert sum(p['value_fen'] for p in card['charts'][0]['series'][0]['points'])==12000
        assert sum(p['value_fen'] for p in card['charts'][1]['series'][0]['points'])==12000
        assert any('理财' in n for n in card['notices'])
    if kind=='transaction':assert card['target']['query']['date']=='2026-09'
    if kind=='cashflow':
        assert [s['name'] for s in card['charts'][0]['series']]==['仅已知计划','基准估计','假设情景']
        assert card['charts'][0]['series'][1]['points'][-1]['value_fen']==trace['data']['timeline'][-1]['baseline_balance_fen']
    assert ledger==[(r.tx_id,r.amount_fen) for r in (await db.scalars(select(TransactionORM))).all()]
    assert balances==[(r.account_id,r.balance_fen) for r in (await db.scalars(select(AccountORM))).all()]


@pytest.mark.asyncio
async def test_budget_snapshot_is_immutable_encrypted_and_same_result_is_idempotent(db):
    ctx=await setup(db)
    await bill_budgets.save(db,'U1',BudgetUpdate(month='2026-09',amount_fen=10000));await db.commit()
    trace=await nova.execute_tool(call('get_budget_status',{'month':'2026-09'}),ctx)
    response=reply([{'name':'get_budget_status','result':trace}])
    original=await results.save(db,'U1',ctx.session_id,ctx.request_id,response);await db.commit()
    assert original[0]['rows'][0]['progress']==120.0
    assert original[0]['rows'][0]['metrics'][2]['amount_fen']==-2000
    assert await results.save(db,'U1',ctx.session_id,ctx.request_id,response)==original
    assert await db.scalar(select(func.count()).select_from(NovaBillResultORM))==1
    stored=await db.get(NovaBillResultORM,original[0]['result_id'])
    assert '预算' not in stored.payload_ciphertext and 'amount_fen' not in stored.payload_ciphertext
    await bill_budgets.save(db,'U1',BudgetUpdate(month='2026-09',amount_fen=20000,expected_version=1));await db.commit()
    assert await results.cards(db,'U1',ctx.session_id)==original
    with pytest.raises(nova_conversations.ConversationError):await results.cards(db,'U2',ctx.session_id)
    with pytest.raises(nova_conversations.ConversationError):await results.save(db,'U2',ctx.session_id,ctx.request_id,response)
    other=await nova_conversations.create(db,'U2')
    assert await results.cards(db,'U2',other['session_id'])==[]
    await nova_conversations.remove(db,'U1',ctx.session_id)
    assert await db.scalar(select(func.count()).select_from(NovaBillResultORM))==0
    assert await db.scalar(select(func.count()).select_from(TransactionORM))==2


@pytest.mark.asyncio
async def test_partial_mixed_failed_and_invalid_results_never_hide_original_answer(db):
    ctx=await setup(db)
    good={'name':'get_bill_analysis','result':await nova.execute_tool(call('get_bill_analysis'),ctx)}
    failed={'name':'list_transactions','result':{'error':'ACCOUNT_NOT_FOUND'}}
    cards=results.build_cards(reply([good,failed]),ctx.request_id)
    assert len(cards)==1 and not cards[0]['compact_reply']
    other={'name':'get_financial_overview','result':await nova.execute_tool(call('get_financial_overview'),ctx)}
    assert not results.build_cards(reply([good,other]),ctx.request_id)[0]['compact_reply']
    malformed=deepcopy(good);malformed['result']['data']['totals']['net_consumption_fen']=1.23
    assert not results.build_cards(reply([malformed]),ctx.request_id)
    fake=deepcopy(good);fake['result']['source']='assistant_prose'
    assert not results.build_cards(reply([fake]),ctx.request_id)
    repeated=[{**good,'arguments':'{}'},{'name':'get_bill_analysis','arguments':'{}','result':{'error':'BANK_UNAVAILABLE'}}]
    assert results.build_cards(reply(repeated),ctx.request_id)==[]
    assert not results.build_cards({'reply':'编造金额','tool_calls':[]},ctx.request_id)


@pytest.mark.asyncio
async def test_recurring_amounts_and_forecast_incomplete_errors_are_not_invented(db):
    ctx=await setup(db)
    await bill_schedules.save_plan(db,'U1',RecurringPlan(name='<b>房租</b>',account_id='A1',kind='expense',frequency='MONTHLY',amount_fen=12345,anchor_at=utc_now()))
    await db.commit()
    trace=await nova.execute_tool(call('list_recurring_records'),ctx)
    cards=results.build_cards(reply([{'name':'list_recurring_records','result':trace}]),ctx.request_id)
    assert cards[0]['rows'][0]['title']=='<b>房租</b>' # React displays this as text.
    assert cards[0]['rows'][0]['metrics'][0]['amount_fen']==12345


@pytest.mark.asyncio
async def test_authenticated_results_endpoint_and_legacy_history(db):
    from fastapi import FastAPI
    from backend.app.routers.nova_bills import router
    from backend.app.database.connection import get_db
    from backend.app.middleware.auth import create_access_token
    ctx=await setup(db)
    trace=await nova.execute_tool(call('get_bill_analysis'),ctx)
    response=reply([{'name':'get_bill_analysis','result':trace}])
    await results.save(db,'U1',ctx.session_id,ctx.request_id,response)
    await nova_conversations.append_turn(db,'U1',ctx.session_id,ctx.request_id,'看账单',response['reply'])
    app=FastAPI();app.include_router(router)
    async def session():yield db
    app.dependency_overrides[get_db]=session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        path=f'/api/v1/nova/conversations/{ctx.session_id}/bill-results'
        assert (await client.get(path)).status_code==401
        assert (await client.get(path,headers={'Authorization':'Bearer '+create_access_token({'sub':'U2'})})).status_code==404
        own=await client.get(path,headers={'Authorization':'Bearer '+create_access_token({'sub':'U1'})})
        assert own.status_code==200 and own.json()[0]['kind']=='analysis'
    legacy=await nova_conversations.create(db,'U1')
    await nova_conversations.append_turn(db,'U1',legacy['session_id'],'legacy-request','旧问题','旧文字回答')
    assert await results.cards(db,'U1',legacy['session_id'])==[]
    assert (await nova_conversations.get(db,'U1',legacy['session_id']))['messages'][-1]['content']=='旧文字回答'


@pytest.mark.asyncio
async def test_chat_finish_attaches_and_persists_the_same_snapshot(db):
    from backend.app.routers.nova import _finish,ChatRequest
    ctx=await setup(db)
    trace=await nova.execute_tool(call('get_bill_analysis'),ctx)
    response=reply([{'name':'get_bill_analysis','result':trace}])
    finished=await _finish(ChatRequest(message='查询账单',session_id=ctx.session_id,request_id=ctx.request_id),'U1',db,response)
    assert finished['bill_result_cards']==await results.cards(db,'U1',ctx.session_id)
    assert len(finished['bill_result_cards'])==1
    history=await nova_conversations.get(db,'U1',ctx.session_id)
    assert history['messages'][-1]['content']==finished['reply']


@pytest.mark.asyncio
async def test_merchant_preview_and_incomplete_forecast_are_read_only(db):
    from backend.app.services.bank import bill_foundation,bill_forecasts
    from backend.app.schemas.bill_insights import CashflowScenario
    ctx=await setup(db)
    merchant=(await bill_foundation.catalog(db,'U1'))['merchants'][0]['merchant_id']
    trace=await nova.execute_tool(call('preview_bill_merchant_change',{'mode':'split','merchant_id':merchant,'split':{'name':'独立新商户','tx_ids':['own'],'aliases':[]}}),ctx)
    assert not trace.get('error'),trace
    card=results.build_cards(reply([{'name':'preview_bill_merchant_change','result':trace}]),ctx.request_id)[0]
    assert card['kind']=='merchant_preview' and card['rows'][0]['metrics'][0]['text']=='1'
    assert card['rows'][0]['metrics'][0]['amount_fen'] is None
    await bill_forecasts.save(db,'U1',CashflowScenario(days=7));await db.commit()
    trace=await nova.execute_tool(call('list_forecast_reviews'),ctx)
    card=results.build_cards(reply([{'name':'list_forecast_reviews','result':trace}]),ctx.request_id)[0]
    assert card['rows'][0]['badge']=='观察中'
    assert '不计算最终偏差' in card['rows'][0]['description']
    assert not any(m['label']=='最终偏差' for m in card['rows'][0]['metrics'])


@pytest.mark.asyncio
async def test_multi_result_order_survives_conversation_restore(db):
    ctx=await setup(db)
    trace=[]
    for tool in ['get_bill_analysis','list_transactions','get_budget_status']:
        trace.append({'name':tool,'result':await nova.execute_tool(call(tool,{'month':'2026-09'} if tool=='get_budget_status' else {}),ctx)})
    current=await results.save(db,'U1',ctx.session_id,ctx.request_id,reply(trace));await db.commit()
    restored=await results.cards(db,'U1',ctx.session_id)
    assert [c['kind'] for c in current]==['analysis','transactions','budgets']
    assert restored==current
