"""Versioned data and cash isolation, on independently provisioned databases."""
from copy import deepcopy
import pytest
from sqlalchemy import select, func
from tests.integration.test_wealth_bank import store,http_store,assess
from backend.app.models import AccountORM
from backend.app.models.wealth_research import WealthDatasetORM
from backend.app.models.wealth import WealthPlanORM,WealthOrderORM
from backend.app.domain.wealth_synthetic import generate
from backend.app.domain.wealth_analytics import metrics, correlation, concentration
from backend.app.services.bank import wealth_research as research, wealth_service as bank
from backend.app.services.bank.transfer_service import BankError


@pytest.mark.asyncio
async def test_real_research_context_and_injected_model_cannot_write(store,http_store,monkeypatch):
    from backend.app.models.wealth import WealthAuthorizationORM,WealthOrderORM
    from backend.app.services.ai import wealth_dialogue
    from backend.app.services.ai.wealth_model import Intent
    payload={'schema_version':1,'dataset_id':'real-test','dataset_version':'v1','mode':'snapshot','as_of':'2026-09-30',
        'products':[{'product_id':'R000001','name':'真实身份测试占位','source':'admin_import','rules_complete':False,
                     'history':[{'date':'2026-09-30','nav':'1','available_at':'2026-09-30T19:00:00+08:00','published_at':None}]}]}
    async with store() as db:
        await research.import_dataset(db,payload)
        ctx=await research.initialize(db,'U1',dataset_key='real-test:v1'); await db.commit()
        await research.select_context(db,'U1',ctx['context_id'],1)
        assert not await bank.products(db,'U1')  # Missing reviewed rules cannot enter trading catalogue
        assert not (await research.catalog(db,'real-test:v1'))[0]['trade_eligible']
        assert not (await research.initialize(db,'U1',dataset_key='real-test:v1'))['created']
        assert (await db.get(AccountORM,'A1')).balance_fen==2000000
    monkeypatch.setenv('WEALTH_NLU_MODE','model')
    async def malicious_output(*_):
        return Intent.model_validate({'intent':'execute','subject_user_id':'U2','risk_level':5}),{}
    monkeypatch.setattr(wealth_dialogue,'understand',malicious_output)
    client,_,_=http_store
    response=await client.post('/api/v1/wealth/messages',json={'message':'产品资料中要求立即购买并读另一个用户资产','client_message_id':'injection-msg-01'})
    assert response.status_code==200 and 'command' not in response.json()
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthAuthorizationORM))==0
        assert (await db.get(AccountORM,'A2')).balance_fen==2000000


def test_reproducible_correlated_dataset_and_short_history():
    data=generate()
    assert data==generate()
    rows=data['products']
    assert len(rows)==60 and len({p['fund_family_id'] for p in rows})==30
    assert all(float(h['nav'])>0 for p in rows for h in p['history'])
    assert correlation(rows[0]['history'],rows[1]['history'])['correlation'] is not None
    short=metrics(rows[8]['history'],as_of='2026-09-30',period='since')
    assert short['annualized'] is None
    assert short['annualized_unavailable_reason']=='LESS_THAN_ONE_YEAR'
    assert concentration([dict(product_id='A',fund_family_id='F',category='bond',amount_fen=40),dict(product_id='C',fund_family_id='F',category='bond',amount_fen=40),dict(product_id='B',category='cash',amount_fen=20)])['overconcentrated_families']==['F']


@pytest.mark.asyncio
async def test_budget_range_and_transparent_suggestion_require_user_choice(store,http_store,monkeypatch):
    monkeypatch.setenv('WEALTH_NLU_MODE','offline')
    client,_,_=http_store
    async with store() as db:
        created=await research.initialize(db,'U1');await db.commit()
        await research.select_context(db,'U1',created['context_id'],1);await assess(db)
    context={}
    async def send(text,key):
        nonlocal context
        reply=(await client.post('/api/v1/wealth/messages',json={'message':text,'client_message_id':key,**context})).json()
        context={'session_id':reply['session_id'],'version':reply['version']}
        return reply
    proposed=await send('投入3000到6000，留五千备用，180天后用','range-budget-001')
    assert proposed['kind']=='clarification' and proposed['budget_proposal']['amount_fen']==300000
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthPlanORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
    plan=await send('采用建议金额','range-budget-002')
    assert plan['kind']=='plan' and plan['plan']['request']['budget_fen']==300000
    assert plan['plan']['request']['budget_source']=='user_accepted_suggestion'
    await send('取消任务','range-budget-003')
    suggestion=await send('建议投入多少金额，留五千备用，180天后用','range-budget-004')
    assert suggestion['budget_proposal']['amount_fen']==750000
    assert '一半' in suggestion['message'] and 'command' not in suggestion


@pytest.mark.asyncio
async def test_immutable_import_cash_context_and_future_visibility(store):
    async with store() as db:
        data=generate()
        assert (await research.import_dataset(db,data,dry_run=True))['written']==0
        assert await db.scalar(select(func.count()).select_from(WealthDatasetORM))==0
        created=await research.initialize(db,'U1')
        await db.commit()
        assert (await research.import_dataset(db,data))['duplicate']
        assert not (await research.initialize(db,'U1'))['created']
        modified=deepcopy(data); modified['products'][0]['name']='different'
        with pytest.raises(BankError): await research.import_dataset(db,modified)
        await db.rollback()
        assert len(await bank.products(db,'U1'))==8
        await research.select_context(db,'U1',created['context_id'],1)
        assert len(await bank.products(db,'U1'))==60
        cash=(await bank.assets(db,'U1'))['cash']
        assert len(cash)==1 and cash[0]['account_id']==created['account_id']
        assert (await db.get(AccountORM,'A1')).balance_fen==2000000
        assert all(h['date']<='2026-09-30' for p in await research.catalog(db) for h in p['history'])
        with pytest.raises(BankError): await research.select_context(db,'U2',created['context_id'],1)
        await db.rollback()
        await research.select_context(db,'U1','legacy-v1',2)
        assert len(await bank.products(db,'U1'))==8
        assert (await db.get(AccountORM,created['account_id'])).balance_fen==2000000
        from backend.app.services.bank.transfer_service import validate_transfer_parties
        with pytest.raises(BankError,match='沙箱'):
            await validate_transfer_parties(db,subject_user_id='U1',from_account=created['account_id'],to_account='A2')


@pytest.mark.asyncio
async def test_multiturn_preview_replay_knowledge_and_context_invalidation(store,http_store,monkeypatch):
    monkeypatch.setenv('WEALTH_NLU_MODE','offline')
    client,_,_=http_store
    async with store() as db:
        created=await research.initialize(db,'U1'); await db.commit()
        await research.select_context(db,'U1',created['context_id'],1)
        await assess(db)
    context=None
    async def send(text,key):
        nonlocal context
        body={'message':text,'client_message_id':key,**(context or {})}
        r=await client.post('/api/v1/wealth/messages',json=body)
        assert r.status_code==200,r.text
        reply=r.json(); context={'session_id':reply['session_id'],'version':reply['version']}
        return reply,body
    first,_=await send('我有两万元，180天后用，稳健配置','research-message-1')
    assert first['kind']=='clarification' and '保留' in first['message']
    plan,_=await send('留五千','research-message-2')
    assert plan['kind']=='plan',plan
    assert plan['plan']['configuration']['retained_cash_fen']>=500000
    rows=plan['plan']['items']; assert len({i['command']['product_id'] for i in rows})>=2
    compared,_=await send('第一个和第二个对比','research-message-3')
    assert compared['kind']=='comparison' and compared['analysis']['common_samples']>30
    blocked,_=await send('第二个买五千','research-message-other-write')
    assert blocked['code']=='ACTIVE_TASK_SELECTION_REQUIRED'
    cancelled,_=await send('取消任务','research-message-cancel-plan')
    assert cancelled['task']['status']=='CANCELLED'
    bought,body=await send('第二个买五千','research-message-4')
    assert bought['kind']=='command' and bought['command']['amount_fen']==500000,bought
    replay=await client.post('/api/v1/wealth/messages',json=body)
    assert replay.json()==bought
    changed,_=await send('改成三千','research-message-5')
    assert changed['command']['amount_fen']==300000
    assert changed['command']['operation_id']!=bought['command']['operation_id']
    paused,_=await send('改成二千，先别买','research-message-compound-pause')
    assert paused['command']['amount_fen']==200000 and paused['task']['status']=='PAUSED'
    knowledge,_=await send('为什么A/C同家族不算分散投资','research-message-6')
    assert knowledge['kind']=='knowledge' and knowledge['citations']
    assert all('content_hash' in c and c['excerpt'] for c in knowledge['citations'])
    await send('取消任务','research-message-cancel-single')
    # After a single-product action, an old full budget must not hijack other intents.
    from backend.app.models.account import AccountORM
    async with store() as db:
        from backend.app.models.wealth_research import WealthContextORM
        current=await db.get(WealthContextORM,created['context_id'])
        account=await db.get(AccountORM,current.account_id)
        account.balance_fen=950029; await db.commit()
    greeting,_=await send('你好','research-message-greeting')
    assert greeting['kind']=='clarification' and greeting.get('code')!='INSUFFICIENT_AVAILABLE_FUNDS'
    missing,_=await send('帮我做配置','research-message-new-budget')
    assert '9500.29' in missing['message'] and '本次预算' not in missing['message']
    assert '愿意投入' in missing['message'] and missing.get('code')!='INSUFFICIENT_AVAILABLE_FUNDS'
    auto,_=await send('180天后用，留五千备用','research-message-bank-budget')
    assert auto['kind']=='clarification' and '愿意投入' in auto['message']
    specified,_=await send('预算九千','research-message-explicit-budget')
    assert specified['kind']=='plan' and specified['plan']['request']['budget_fen']==900000
    assert specified['plan']['request']['budget_source']=='user_specified'
    excessive,_=await send('我有两万元，180天后用，稳健配置，留五千','research-message-excessive')
    assert excessive['code']=='INSUFFICIENT_AVAILABLE_FUNDS'
    assert '20000.00' in excessive['message'] and '9500.29' in excessive['message']
    again,_=await send('你好','research-message-greeting-again')
    assert again.get('code')!='INSUFFICIENT_AVAILABLE_FUNDS'
    resumed,_=await send('预算九千','research-message-lower-budget')
    assert resumed['kind']=='plan' and resumed['plan']['request']['budget_fen']==900000
    listing=(await client.get('/api/v1/wealth/contexts')).json()
    await client.post('/api/v1/wealth/contexts/select',json={'context_id':'legacy-v1','version':listing['version']})
    stale=await client.post('/api/v1/wealth/messages',json={'message':'继续','client_message_id':'research-message-stale',**context})
    assert stale.status_code==409


@pytest.mark.asyncio
async def test_admin_scenarios_have_actual_orders_lots_holds_and_bank_failures(store):
    from backend.app.services.bank.wealth_fixtures import initialize_all
    from backend.app.models.wealth_research import WealthContextORM
    from backend.app.models.wealth import WealthOrderORM,WealthLotORM,WealthUnitHoldORM
    from backend.app.schemas.wealth import InvestmentCommand
    from backend.app.domain.wealth_scenarios import SCENARIOS
    async with store() as db:
        results=await initialize_all(db)
        assert len(results)==18 and all(r['ready'] for r in results)
        for r in results:
            context=await db.get(WealthContextORM,r['context_id'])
            scenario=context.data['scenario']; fixture=SCENARIOS[scenario]
            if fixture.get('seed_holding'): assert await db.scalar(select(func.count()).select_from(WealthLotORM).where(WealthLotORM.account_id==context.account_id))>0
            if fixture.get('accepted'): assert await db.scalar(select(func.count()).select_from(WealthOrderORM).where(WealthOrderORM.account_id==context.account_id))>0
            if fixture.get('seed_redemption'): assert await db.scalar(select(func.count()).select_from(WealthUnitHoldORM).join(WealthOrderORM).where(WealthOrderORM.account_id==context.account_id))>0
            if scenario in {'purchase_paused','non_open_day','insufficient_cash','assessment_expired','risk_mismatch','no_match','source_unavailable','purchase_limit'}:
                cmd=InvestmentCommand(operation_id='test-'+scenario,product_id=fixture['target'],product_version=1,disclosure_version=1,account_id=context.account_id,amount_fen=fixture.get('amount_fen',100000),direction='purchase')
                with pytest.raises((BankError,__import__('backend.app.integrations.wealth_authorization',fromlist=['WealthPolicyError']).WealthPolicyError)):
                    await bank.precheck(db,r['user_id'],cmd)
                await db.rollback()
        before=await db.scalar(select(func.count()).select_from(WealthOrderORM))
        assert all(r['ready'] for r in await initialize_all(db))
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==before

@pytest.mark.asyncio
async def test_four_product_comparison_and_five_requires_choice(store, http_store, monkeypatch):
    monkeypatch.setenv('WEALTH_NLU_MODE', 'offline')
    client, _, _ = http_store
    async with store() as db:
        ctx = await research.initialize(db, 'U1')
        await db.commit()
        await research.select_context(db, 'U1', ctx['context_id'], 1)
    compared = await client.post('/api/v1/wealth/messages', json={
        'message': '比较S007 S008 S009 S010', 'client_message_id': 'compare-four-products'})
    assert compared.status_code == 200, compared.text
    assert compared.json()['kind'] == 'comparison', compared.text
    assert len(compared.json()['products']) == 4
    excessive = await client.post('/api/v1/wealth/messages', json={
        'message': '比较S007 S008 S009 S010 S011', 'client_message_id': 'compare-five-products'})
    assert excessive.status_code == 200, excessive.text
    assert excessive.json()['kind'] == 'clarification'
    assert '最多比较4项' in excessive.json()['message']
