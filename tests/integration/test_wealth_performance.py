from dataclasses import replace
from datetime import datetime,date,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select,func,delete
from tests.integration.test_wealth_bank import store,http_store,command,authorize,NOW
from tests.integration.test_wealth_instant import setup,bound
from tests.integration.test_nova_wealth import conversation,context
from backend.app.models.wealth import WealthLotORM,WealthOrderORM,WealthSettlementORM,WealthNavORM,WealthProductVersionORM,WealthConversationORM
from backend.app.services.bank import wealth_service as bank
from backend.app.services.bank.wealth_returns import Replay
from backend.app.services.bank.wealth_performance import overview,measure
from backend.app.schemas.wealth_returns import ReturnsQuery,WealthReturns
from backend.app.services.ai import wealth_agent as agent,task_flow


async def replay(db,now):
    return Replay(list(await db.scalars(select(WealthOrderORM))),list(await db.scalars(select(WealthLotORM))),
        list(await db.scalars(select(WealthSettlementORM))),list(await db.scalars(select(WealthProductVersionORM))),
        list(await db.scalars(select(WealthNavORM))),now)


@pytest.mark.asyncio
async def test_new_and_legacy_instant_multi_lot_redemption_replay_exactly(store,monkeypatch):
    async with store() as db:
        await setup(db,monkeypatch)
        for i in range(2):
            cmd=await bound(db,command('perf-buy-'+str(i)))
            await bank.accept_many(db,'U1',[(cmd,await authorize(db,cmd))])
        lots=list(await db.scalars(select(WealthLotORM)))
        quantity=bank.quantity(lots[0].units_scaled+10000)
        sell=await bound(db,command('perf-sell',direction='redeem',amount=0,quantity=quantity))
        actual=(await bank.accept_many(db,'U1',[(sell,await authorize(db,sell))]))[0]['details']['actual']
        assert all('units_scaled' in x and 'units' in x for x in actual['lots'])
        assert sum(x['units_scaled'] for x in actual['lots'])==bank.scaled(quantity)
        fresh=await replay(db,NOW+timedelta(days=1))
        assert not any(fresh.errors.values()),fresh.errors
        before=overview(fresh,ReturnsQuery(overview=True,period='cumulative'),'test')['performance']
        # Emulate immutable older event format; the reader recovers without a migration.
        for row in await db.scalars(select(WealthSettlementORM).where(WealthSettlementORM.operation_id=='perf-sell')):
            row.data={**row.data,'lots':[{k:v for k,v in x.items() if k not in {'units','units_scaled'}} for x in row.data['lots']]}
        await db.commit()
        old=await replay(db,NOW+timedelta(days=1))
        assert not any(old.errors.values()),old.errors
        result=overview(old,ReturnsQuery(overview=True,period='cumulative'),'test')
        WealthReturns.model_validate(result)
        assert result['performance']['amount_fen']==before['amount_fen']
        assert result['performance']['rate_percent']==before['rate_percent']
        assert all('units' not in x for row in await db.scalars(select(WealthSettlementORM).where(WealthSettlementORM.operation_id=='perf-sell')) for x in row.data['lots'])


@pytest.mark.asyncio
async def test_weekend_zero_missing_trading_nav_unknown_and_no_future_price(store,monkeypatch):
    async with store() as db:
        await setup(db,monkeypatch)
        cmd=await bound(db,command('weekend-buy'))
        await bank.accept_many(db,'U1',[(cmd,await authorize(db,cmd))])
        # Friday 10/9 -> Saturday 10/10 has no new trading NAV.
        friday=await db.scalar(select(WealthNavORM).where(WealthNavORM.version_id=='MF002:1',WealthNavORM.nav_date==date(2026,10,9)))
        friday.price='1.00000000';friday.available_at=datetime(2026,10,9,13,tzinfo=timezone.utc)
        await db.execute(delete(WealthNavORM).where(WealthNavORM.version_id=='MF002:1',WealthNavORM.nav_date>date(2026,10,9)))
        await db.commit()
        r=await replay(db,datetime(2026,10,11,6,tzinfo=timezone.utc))
        result=overview(r,ReturnsQuery(overview=True),'test')['performance']
        assert result['start_date']==result['end_date']=='2026-10-10'
        assert result['amount_fen']==0 and Decimal(result['rate_percent'])==0
        assert result['positions'][0]['yesterday_pnl_fen']==0
        # Missing Monday's actual NAV is unknown, not weekend-style zero.
        r=await replay(db,datetime(2026,10,13,6,tzinfo=timezone.utc))
        missing=overview(r,ReturnsQuery(overview=True),'test')['performance']
        assert missing['amount_fen'] is None and missing['rate_percent'] is None


def test_modified_dietz_accounts_for_cashflow_not_current_balance():
    from backend.app.domain.time import BEIJING
    start=date(2026,10,1);end=date(2026,10,2)
    fake=SimpleNamespace(events=[{'pid':'p','at':datetime(2026,10,2,tzinfo=BEIJING),'input':10000,'output':0}],
        snapshot=lambda pid,day,opening=False:(10000 if opening else 22000,None,None,None))
    amount,rate=measure(fake,['p'],start,end)
    assert amount==2000 and Decimal(rate)==Decimal('13.333333') # 2000 / (10000 + 0.5*10000)


@pytest.mark.asyncio
async def test_agent_compare_is_read_child_and_keeps_parent_selection(store,monkeypatch):
    async with store() as db:
        await conversation(db)
        selected=[{'product_id':'P1','product_version':1,'name':'甲'},{'product_id':'P2','product_version':1,'name':'乙'}]
        original={'nova_session_id':'NOVA-test-session','context_id':'test','command':{'kind':'recommend','products':selected},
            'closed':False,'view_context':{'selected_products':selected,'completed_stages':['match','candidates','selected'],'amounts_yuan':{'P1':'100'}}}
        db.add(WealthConversationORM(session_id='NW-parent',user_id='U1',version=1,data=original));await db.commit()
        monkeypatch.setattr(agent.llm,'generate_structured',AsyncMock(return_value=SimpleNamespace(data=agent.IntentBatch(intents=[agent.WealthIntent(kind='compare')]))))
        monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(assets=AsyncMock(return_value={'context_id':'test'}),
            call=AsyncMock(return_value=[{'product_id':p['product_id'],'version':p['product_version'],'name':p['name']} for p in selected])))
        ctx=replace(context(db),wealth_task_id='NW-parent',wealth_stage='amounts',original_message='比较已选产品')
        result=await agent.manage(agent.EmptyArguments(),ctx)
        parent=await db.get(WealthConversationORM,'NW-parent')
        assert parent.version==1 and parent.data==original
        child=next(c for c in result['wealth_cards'] if c['task_id']!='NW-parent')
        assert child['parent_task_id']=='NW-parent' and child['command']['products']==selected
        await task_flow.observe(ctx,result)
        assert (await task_flow.active(ctx))['id']=='NW-parent'


@pytest.mark.asyncio
async def test_agent_returns_fetches_actual_overall_facts_before_reply(store,monkeypatch):
    async with store() as db:
        await conversation(db)
        monkeypatch.setattr(agent.llm,'generate_structured',AsyncMock(return_value=SimpleNamespace(data=agent.IntentBatch(intents=[agent.WealthIntent(kind='returns',period='month')]))))
        call=AsyncMock(return_value={'performance':{'start_date':'2026-09-24','end_date':'2026-10-23','amount_fen':1234,'rate_percent':'1.2','notice':''}})
        monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(assets=AsyncMock(return_value={'context_id':'test'}),call=call))
        result=await agent.manage(agent.EmptyArguments(),replace(context(db),original_message='近一个月理财赚了多少'))
        assert '12.34' in result['reply'] and '+1.20%' in result['reply']
        assert call.await_args.args[2]['overview'] is True and call.await_args.args[2]['scope']=='all'
        assert result['wealth_cards'][0]['read_result']['amount_fen']==1234


@pytest.mark.asyncio
async def test_conflicting_redemption_evidence_is_unknown_never_repaired(store,monkeypatch):
    async with store() as db:
        await setup(db,monkeypatch)
        cmd=await bound(db,command('bad-evidence-buy'))
        await bank.accept_many(db,'U1',[(cmd,await authorize(db,cmd))])
        sell=await bound(db,command('bad-evidence-sell',direction='redeem',amount=0,quantity='1.0000'))
        await bank.accept_many(db,'U1',[(sell,await authorize(db,sell))])
        row=await db.get(WealthSettlementORM,'bad-evidence-sell:pricing')
        row.data={**row.data,'lots':[{**x,'units':'2.0000'} for x in row.data['lots']]}
        await db.commit()
        result=overview(await replay(db,NOW+timedelta(days=1)),ReturnsQuery(overview=True),'test')['performance']
        assert result['amount_fen'] is None and result['rate_percent'] is None
        assert '暂时无法核对' in result['notice'] and 'bad-evidence' not in result['notice']
        assert (await db.get(WealthSettlementORM,row.event_id)).data['lots'][0]['units']=='2.0000'


def test_cashflow_rate_no_invested_capital_is_not_fabricated_zero():
    fake=SimpleNamespace(events=[],snapshot=lambda *args,**kwargs:(0,None,None,None))
    assert measure(fake,[],date(2026,10,1),date(2026,10,1))==(0,None)


@pytest.mark.asyncio
async def test_public_overview_schema_scope_and_beijing_yesterday(store,http_store,monkeypatch):
    async with store() as db:
        await setup(db,monkeypatch)
        cmd=await bound(db,command('http-overview-buy'))
        await bank.accept_many(db,'U1',[(cmd,await authorize(db,cmd))])
    client,_,app=http_store
    result=await client.get('/api/v1/wealth/returns?scope=all&period=day&overview=true')
    assert result.status_code==200,result.text
    performance=result.json()['performance']
    assert performance['start_date']==performance['end_date']=='2026-09-29'
    assert performance['amount_fen']==0 and performance['rate_percent'] is None
    assert performance['rate_method']=='modified-dietz-nonannualized-v1'
    assets=(await client.get('/api/v1/wealth/assets')).json()
    positions=await client.post('/api/v1/wealth/actions',json={'schema_version':'wealth-action-v1',
        'request_id':'performance-positions-read','context_id':assets['context_id'],'action':'list_positions',
        'params':{'page':1,'page_size':5}})
    assert positions.status_code==200,positions.text
    assert positions.json()['data']['table_data']['business_date']=='2026-09-30'
    for suffix in ['scope=product&product_id=MF002','scope=all&anchor_date=2026-09-29']:
        blocked=await client.get('/api/v1/wealth/returns?overview=true&period=day&'+suffix)
        assert blocked.status_code==422
