"""Actual bank settlements, isolated database only; no live account seeding."""
from datetime import date, datetime, time, timezone

import pytest
from sqlalchemy import select, update, delete, func

from backend.app.models import AccountORM
from backend.app.models.wealth import WealthNavORM, WealthLotORM, WealthSettlementORM, WealthProfileORM, WealthProductVersionORM, WealthProductORM
from backend.app.middleware.auth import get_current_user, create_access_token
from backend.app.schemas.wealth_returns import ReturnsQuery, WealthReturns
from backend.app.services.bank import wealth_service as bank
from backend.app.services.bank.wealth_returns import read
from tests.integration.test_wealth_bank import store, http_store, assess, authorize, command, move


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    from backend.app.services.ai.llm.client import LLMService
    async def forbidden(*args, **kwargs):
        pytest.fail('Actual returns may not invoke LLM')
    monkeypatch.setattr(LLMService, '_generate', forbidden)


async def returns(store, **query):
    async with store() as db:
        result = await read(db, 'U1', ReturnsQuery(**query))
        return WealthReturns.model_validate(result).model_dump(mode='json')


async def buy(store, key='actual-buy', amount=100_000, price='1.00000000'):
    async with store() as db:
        await assess(db)
        await db.execute(update(WealthNavORM).where(WealthNavORM.version_id == 'MF001:1').values(price=price))
        for nav in await db.scalars(select(WealthNavORM).where(WealthNavORM.version_id == 'MF001:1')):
            nav.available_at = datetime.combine(nav.nav_date, time(5), timezone.utc)
        version = await db.get(WealthProductVersionORM, 'MF001:1')
        version.rules = {**version.rules, 'cash_business_days': 2}
        await db.commit()
        cmd = command(key, product='MF001', amount=amount)
        auth = await authorize(db, cmd)
        await bank.accept_many(db, 'U1', [(cmd, auth)])
        await move(db, 12)
        await bank.settle(db, key)


@pytest.mark.asyncio
async def test_f20_principal_and_unsettled_holds_not_profit(store):
    await buy(store)
    result = await returns(store, period='cumulative')
    assert result['status'] == 'complete', result
    assert result['summary']['period_pnl_fen'] == result['summary']['cumulative_pnl_fen'] == 0
    async with store() as db:
        cmd = command('additional-principal', product='MF001', amount=50_000)
        auth = await authorize(db, cmd)
        await bank.accept_many(db, 'U1', [(cmd, auth)])
    assert (await returns(store, period='cumulative'))['summary']['period_pnl_fen'] == 0
    async with store() as db:
        await move(db, 13)
        await bank.settle(db, 'additional-principal')
    assert (await returns(store, period='cumulative'))['summary']['cumulative_pnl_fen'] == 0


@pytest.mark.asyncio
async def test_f21_receivable_arrival_closed_position_and_fee_once(store):
    await buy(store)
    async with store() as db:
        lot = await db.scalar(select(WealthLotORM))
        cmd = command('actual-sell', product='MF001', direction='redeem', amount=0, quantity=bank.quantity(lot.units_scaled))
        auth = await authorize(db, cmd)
        await bank.accept_many(db, 'U1', [(cmd, auth)])
        await db.execute(update(WealthNavORM).where(WealthNavORM.version_id == 'MF001:1', WealthNavORM.nav_date >= date(2026,10,12)).values(price='1.10000000'))
        await db.commit()
        await move(db, 13)
        priced = await bank.settle(db, 'actual-sell')
        assert priced['status'] == 'PENDING_CASH'
        expected = priced['details']['actual']['realized_pnl_fen']
        fee = priced['details']['actual']['fee_fen']
    pending = await returns(store, period='cumulative')
    assert pending['summary']['cumulative_pnl_fen'] == expected, pending
    assert pending['summary']['realized_pnl_fen'] == expected
    assert pending['summary']['unrealized_pnl_fen'] == 0
    assert pending['summary']['fees_fen'] == fee
    async with store() as db:
        await move(db, 14)
        assert (await bank.settle(db, 'actual-sell'))['status'] == 'SETTLED'
        assert (await db.scalar(select(WealthLotORM))).units_scaled == 0
    arrived = await returns(store, period='day')
    assert arrived['summary']['period_pnl_fen'] == 0, arrived
    assert arrived['summary']['cumulative_pnl_fen'] == expected
    assert arrived['products'][0]['product_id'] == 'MF001'


@pytest.mark.asyncio
async def test_f24_missing_nav_and_cost_are_unknown_not_zero(store):
    await buy(store)
    async with store() as db:
        await db.execute(delete(WealthNavORM).where(WealthNavORM.version_id == 'MF001:1', WealthNavORM.nav_date == date(2026,10,12)))
        await db.commit()
    missing = await returns(store)
    assert missing['summary']['period_pnl_fen'] is None and missing['status'] == 'partial'
    assert missing['points'][0]['pnl_fen'] is None
    async with store() as db:
        await move(db,14)
    middle_gap=await returns(store,period='month')
    assert middle_gap['status']=='partial'
    assert middle_gap['summary']['period_pnl_fen'] is None
    assert middle_gap['points'][11]['pnl_fen'] is None
    async with store() as db:
        await db.execute(delete(WealthSettlementORM))
        await db.commit()
    missing_cost = await returns(store, period='cumulative')
    assert missing_cost['coverage_start'] is None
    assert missing_cost['summary']['cumulative_pnl_fen'] is None
    assert missing_cost['missing_fields']


@pytest.mark.asyncio
async def test_unpublished_nav_calendar_and_partial_period(store):
    await buy(store)
    async with store() as db:
        await db.execute(update(WealthNavORM).where(WealthNavORM.version_id == 'MF001:1', WealthNavORM.nav_date == date(2026,10,12)).values(available_at=datetime(2026,10,12,12,tzinfo=timezone.utc)))
        await db.commit()
    result = await returns(store)
    assert result['summary']['period_pnl_fen'] is None
    month = await returns(store, period='month')
    assert len(month['points']) == 31
    assert month['points'][0]['status'] == 'unavailable'
    assert month['points'][-1]['pnl_fen'] is None
    assert (await returns(store, period='year'))['status'] == 'partial'
    assert (await returns(store, anchor_date=date(2027,1,1)))['summary']['period_pnl_fen'] is None


@pytest.mark.asyncio
async def test_public_boundary_scope_validation_and_read_only(store, http_store):
    await buy(store)
    client, internal, app = http_store
    assert (await client.post('/api/v1/internal/bank/wealth/returns',json={'subject_user_id':'U1'})).status_code==403
    result = await client.get('/api/v1/wealth/returns?period=cumulative')
    assert result.status_code == 200, result.text
    assert result.json()['data_source'] == 'simulated_bank_actual_settlements'
    assert (await client.get('/api/v1/wealth/returns?scope=product&product_id=MF001&period=cumulative')).json()['summary'] == result.json()['summary']
    assert (await client.get('/api/v1/wealth/returns?scope=product&product_id=MF002')).status_code == 404
    for suffix in ('?scope=product', '?period=cumulative&anchor_date=2026-10-12', '?user_id=U2', '?account_id=A2'):
        assert (await client.get('/api/v1/wealth/returns'+suffix)).status_code == 422
    app.dependency_overrides.pop(get_current_user)
    assert (await client.get('/api/v1/wealth/returns')).status_code == 401
    client.headers['Authorization'] = 'Bearer '+create_access_token({'sub':'U2'})
    assert (await client.get('/api/v1/wealth/returns')).json()['products'] == []
    assert (await client.get('/api/v1/wealth/returns?scope=product&product_id=MF001')).status_code == 404
    async with store() as db:
        assert (await db.get(AccountORM, 'A1')).balance_fen == 1_900_000
        assert await db.scalar(select(func.count()).select_from(WealthSettlementORM)) == 1
        assert await db.scalar(select(func.count()).select_from(WealthProfileORM)) == 1


@pytest.mark.asyncio
async def test_f22_purchase_fee_already_in_actual_input_not_deducted_twice(store):
    async with store() as db:
        version = await db.get(WealthProductVersionORM, 'MF001:1')
        version.rules = {**version.rules, 'purchase_fee_rate': '0.003'}
        await db.commit()
    await buy(store)
    result = await returns(store, period='cumulative')
    fee = result['summary']['fees_fen']
    assert fee > 0
    assert result['summary']['cumulative_pnl_fen'] == -fee
    assert result['summary']['period_pnl_fen'] == result['products'][0]['period_pnl_fen'] == -fee


@pytest.mark.asyncio
async def test_f23_all_debit_accounts_exact_codes_and_unknown_evidence(store):
    await buy(store)
    async with store() as db:
        db.add(AccountORM(account_id='A3',user_id='U1',account_type='checking',card_number='mock3',balance_fen=200_000))
        version = await db.get(WealthProductVersionORM, 'MF001:1')
        db.add(WealthProductORM(product_id='MF001-C',current_version=1,status='on_sale'))
        await db.flush()
        db.add(WealthProductVersionORM(version_id='MF001-C:1',product_id='MF001-C',version=1,
            name='隔离C类',kind=version.kind,category=version.category,risk_level=version.risk_level,
            rules=version.rules,disclosure=version.disclosure,created_at=version.created_at))
        await db.flush()
        for nav in await db.scalars(select(WealthNavORM).where(WealthNavORM.version_id=='MF001:1')):
            db.add(WealthNavORM(nav_id='MF001-C:1:'+nav.nav_date.isoformat(),version_id='MF001-C:1',
                nav_date=nav.nav_date,price=nav.price,available_at=nav.available_at))
        await db.commit()
        cmd=command('second-account-C',product='MF001-C',amount=50_000).model_copy(update={'account_id':'A3'})
        auth=await authorize(db,cmd)
        await bank.accept_many(db,'U1',[(cmd,auth)])
        await move(db,13)
        await bank.settle(db,cmd.operation_id)
    result=await returns(store,period='cumulative')
    assert [p['product_id'] for p in result['products']]==['MF001','MF001-C']
    assert result['summary']['cumulative_pnl_fen']==sum(p['cumulative_pnl_fen'] for p in result['products'])==0
    single=await returns(store,scope='product',product_id='MF001-C',period='cumulative')
    assert len(single['products'])==1 and single['asset_scope_id']==result['asset_scope_id']
    async with store() as db:
        event=await db.get(WealthSettlementORM,'second-account-C:pricing')
        event.kind='unsupported_distribution'
        await db.commit()
    missing=await returns(store,period='cumulative')
    assert missing['summary']['cumulative_pnl_fen'] is None
    assert missing['products'][0]['cumulative_pnl_fen']==0
    assert missing['products'][1]['cumulative_pnl_fen'] is None


@pytest.mark.asyncio
async def test_quick_redemption_effective_timestamp_and_context_isolation(store):
    from backend.app.services.bank import wealth_research as research
    from backend.app.services.bank.wealth_preload import bank_products
    from backend.app.models.wealth_research import WealthContextORM
    from backend.app.schemas.wealth import InvestmentCommand
    async with store() as db:
        payload={'schema_version':1,'dataset_id':'mixed-preload-v12','dataset_version':'returns-quick',
                 'mode':'snapshot','products':bank_products()}
        for product in payload['products']:
            product['rules']['dataset_version']='returns-quick'
        assert (await research.import_dataset(db,payload))['ok']
        context=await research.initialize(db,'U1',dataset_key='mixed-preload-v12:returns-quick')
        ctx=await db.get(WealthContextORM,context['context_id'])
        ctx.data={**ctx.data,'business_at':'2026-09-29T14:00:00+08:00'}
        await db.commit()
        await research.select_context(db,'U1',ctx.context_id,1)
        await assess(db)
        cmd=InvestmentCommand(operation_id='returns-quick-buy',direction='purchase',product_id='B017',
            product_version=1,disclosure_version=1,account_id=ctx.account_id,amount_fen=200_000)
        await bank.accept_many(db,'U1',[(cmd,await authorize(db,cmd))])
        ctx=await db.get(WealthContextORM,ctx.context_id)
        ctx.data={**ctx.data,'business_at':'2026-09-30T10:00:00+08:00'}
        await db.commit()
        assert (await bank.settle(db,cmd.operation_id))['status']=='SETTLED'
        lot=await db.scalar(select(WealthLotORM).where(WealthLotORM.account_id==ctx.account_id))
        sell=InvestmentCommand(operation_id='returns-quick-sell',direction='redeem',product_id='B017',
            product_version=1,disclosure_version=1,account_id=ctx.account_id,units=bank.quantity(lot.units_scaled),
            command_version=2,redemption_mode='quick')
        bound=InvestmentCommand.model_validate((await bank.precheck(db,'U1',sell))['command'])
        done=(await bank.accept_many(db,'U1',[(bound,await authorize(db,bound))]))[0]
        assert done['status']=='SETTLED'
        expected=done['details']['actual']['realized_pnl_fen']
    result=await returns(store,period='cumulative')
    assert result['summary']['cumulative_pnl_fen']==result['summary']['realized_pnl_fen']==expected, result
    assert result['summary']['unrealized_pnl_fen']==0
    assert [p['product_id'] for p in result['products']]==['B017']
    async with store() as db:
        event=await db.get(WealthSettlementORM,'returns-quick-buy:pricing')
        event.data={k:v for k,v in event.data.items() if k!='business_at'}
        await db.commit()
    assert (await returns(store,period='cumulative'))['summary']['cumulative_pnl_fen'] is None
