"""Versioned data and cash isolation, on independently provisioned databases."""
from copy import deepcopy
import pytest
from sqlalchemy import select, func
from tests.integration.test_wealth_bank import store,http_store,assess
from backend.app.models import AccountORM
from backend.app.models.wealth_research import WealthDatasetORM
from backend.app.domain.wealth_synthetic import generate
from backend.app.domain.wealth_analytics import metrics, correlation, concentration
from backend.app.services.bank import wealth_research as research, wealth_service as bank
from backend.app.services.bank.transfer_service import BankError


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
    bought,body=await send('第二个买五千','research-message-4')
    assert bought['kind']=='command' and bought['command']['amount_fen']==500000,bought
    replay=await client.post('/api/v1/wealth/messages',json=body)
    assert replay.json()==bought
    changed,_=await send('改成三千','research-message-5')
    assert changed['command']['amount_fen']==300000
    assert changed['command']['operation_id']!=bought['command']['operation_id']
    knowledge,_=await send('为什么A/C同家族不算分散投资','research-message-6')
    assert knowledge['kind']=='knowledge' and knowledge['citations']
    assert all('content_hash' in c and c['excerpt'] for c in knowledge['citations'])
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
