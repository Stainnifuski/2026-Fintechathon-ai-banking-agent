"""Full 806 pool, identical SQLite/PG browsing, zero external quote calls."""
import json
import time
from pathlib import Path
import pytest
from sqlalchemy import select,func,event
from backend.app.models import AccountORM
from backend.app.models.wealth import WealthOrderORM,WealthLotORM,WealthCashHoldORM,WealthUnitHoldORM,WealthProductORM
from backend.app.services.bank import wealth_service as bank,wealth_quotes as quotes
from backend.app.services.bank.wealth_compact_upgrade import upgrade,pool
from backend.app.services.orchestration.wealth_portfolio_joint import install
from backend.app.schemas.wealth import InvestmentCommand
from tests.integration.test_wealth_bank import store,http_store,assess

from tests.integration.test_wealth_dynamic_quotes import authorize

ROOT=Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_full_pool_category_rotation_reads_current_bank_without_market_wait(store,http_store,monkeypatch):
    from backend.app.routers.wealth_portfolio import router
    client,local,app=http_store
    app.include_router(router)
    source=install(app,ROOT);await source.preload()
    async with store() as db:
        await upgrade(db);await db.commit();await assess(db)
        facts=await bank.assets(db,'U1');context=facts['context_id']
        # A real isolated holding ensures repeated asset reads would otherwise
        # refresh NAV too; this is test money only.
        cmd=InvestmentCommand(operation_id='offline-browsing-opening',direction='purchase',product_id='000003',
            product_version=pool()['product_version'],disclosure_version=pool()['product_version'],account_id='A1',amount_fen=100000)
        cmd=InvestmentCommand.model_validate((await bank.precheck(db,'U1',cmd))['command'])
        await bank.accept_many(db,'U1',[(cmd,await authorize(db,cmd))])
    calls=[];allow=False
    async def observation(code):
        calls.append(code)
        assert allow,'Candidate browsing must not call the external market provider'
        return {'ok':False,'code':code,'error':'ISOLATED_TEST_NO_NETWORK'}
    monkeypatch.setattr(quotes,'observation',observation)
    async def ledger():
        async with store() as db:
            return {'balances':list((await db.execute(select(AccountORM.account_id,AccountORM.balance_fen).order_by(AccountORM.account_id))).all()),
                'orders':await db.scalar(select(func.count()).select_from(WealthOrderORM)),
                'cash_holds':await db.scalar(select(func.count()).select_from(WealthCashHoldORM)),
                'unit_holds':await db.scalar(select(func.count()).select_from(WealthUnitHoldORM)),
                'lots':list((await db.execute(select(WealthLotORM.lot_id,WealthLotORM.units_scaled,WealthLotORM.cost_fen))).all())}
    before=await ledger()
    engine=store.kw['bind'];counts={'queries':0,'seconds':0.0}
    def started(conn,cursor,stmt,params,ctx,many):ctx.benchmark_started=time.perf_counter()
    def ended(conn,cursor,stmt,params,ctx,many):
        counts['queries']+=1;counts['seconds']+=time.perf_counter()-ctx.benchmark_started
    event.listen(engine.sync_engine,'before_cursor_execute',started)
    event.listen(engine.sync_engine,'after_cursor_execute',ended)
    timings=[]
    try:
        for category in ('bond_fund','mixed_fund'):
            body={'context_id':context,'filter':{'mode':'category','category':category}}
            for n in range(3):
                old=dict(counts);start=time.perf_counter()
                response=await client.post('/api/v1/wealth/portfolio/recommendations',json=body)
                elapsed=time.perf_counter()-start
                assert response.status_code==200,response.text
                group=response.json();assert group['items'],group
                timings.append({'category':category,'stage':'search' if n==0 else 'next_batch','seconds':round(elapsed,4),
                    'sql_queries':counts['queries']-old['queries'],'sql_seconds':round(counts['seconds']-old['seconds'],4)})
                binding=group['session_binding']
                body={**body,'cursor':group['next_cursor'],'candidate_binding':{
                    'session_id':binding['session_id'],'expected_version':binding['version'],'filter':body['filter']}}
        # Cached material must never make a paused product look purchasable.
        async with store() as db:
            (await db.get(WealthProductORM,'000003')).status='paused';await db.commit()
        named=await client.post('/api/v1/wealth/portfolio/recommendations',json={
            'context_id':context,'filter':{'mode':'name','name_query':'000003'}})
        assert named.status_code==200 and not named.json()['items'],named.text
        async with store() as db:
            (await db.get(WealthProductORM,'000003')).status='on_sale';await db.commit()
        assert calls==[]
        assert await ledger()==before
        # The structured action used by the actual page takes the same route.
        action=await client.post('/api/v1/wealth/actions',json={
            'schema_version':'wealth-action-v1','request_id':'offline-browsing-action','context_id':context,
            'action':'recommend_products','params':{'filter':{'mode':'category','category':'equity_fund'}}})
        assert action.status_code==200,action.text
        assert action.json()['status']=='ok',action.text
        assert calls==[]
        allow=True
        await local.precheck('U1',cmd.model_copy(update={'operation_id':'offline-browsing-precheck'}))
        assert calls==['000003'],'Trading precheck must retain its quote refresh'
    finally:
        event.remove(engine.sync_engine,'before_cursor_execute',started)
        event.remove(engine.sync_engine,'after_cursor_execute',ended)
    print('BROWSE_BENCHMARK '+json.dumps({'engine':engine.dialect.name,'pool':806,'transport':'ASGI HTTP',
        'timings':timings,'browse_external_calls':0,'ledger_unchanged':True}))
