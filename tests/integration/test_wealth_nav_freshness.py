"""Synthetic evidence for stale execution, weekends and publication visibility."""
from datetime import date, datetime, timezone
import pytest
from sqlalchemy import select, func
from tests.integration.test_wealth_bank import store, http_store
from tests.integration.test_wealth_dynamic_quotes import setup
from backend.app.models.wealth import WealthNavORM, WealthOrderORM, WealthCashHoldORM
from backend.app.services.bank import wealth_service as bank, wealth_quotes as quotes
from backend.app.services.bank.transfer_service import BankError


async def clocked(db, monkeypatch):
    context, _, command = await setup(db)
    current = [datetime(2026, 10, 18, 6, tzinfo=timezone.utc)]
    async def now(*args, **kwargs): return current[0]
    monkeypatch.setattr(bank, 'business_now', now)
    return context, command, current


async def add_nav(db, command, day, price, observed):
    vid = f'{command.product_id}:{command.product_version}'
    key = f'{vid}:{day}'
    row = await db.get(WealthNavORM, key)
    if row is None:
        db.add(WealthNavORM(nav_id=key, version_id=vid, nav_date=date.fromisoformat(day),
            price=price, available_at=datetime.fromisoformat(observed)))
    else:
        row.price, row.available_at = price, datetime.fromisoformat(observed)
    await db.commit()


@pytest.mark.asyncio
async def test_stale_offline_anchor_and_failed_provider_cannot_create_trade(store, http_store, monkeypatch):
    client, _, _ = http_store
    async with store() as db:
        context, command, _ = await clocked(db, monkeypatch)
        with pytest.raises(BankError) as failed:
            await bank.precheck(db, 'U1', command)
        assert failed.value.code == 'NAV_UPDATE_REQUIRED'
        assert '2026-10-16' in failed.value.message
    async def unavailable(code):
        return {'ok': False, 'code': code, 'error': 'SYNTHETIC_NO_NETWORK'}
    monkeypatch.setattr(quotes, 'observation', unavailable)
    response = await client.post('/api/v1/wealth/target-purchases/prepare', json={
        'request_id': 'stale-quote-request', 'context_id': context['context_id'], 'items': [{
            'product_id': command.product_id, 'product_version': command.product_version,
            'disclosure_version': command.disclosure_version, 'amount_fen': 100000}]})
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'NAV_UPDATE_REQUIRED'
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM)) == 0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM)) == 0


@pytest.mark.asyncio
async def test_weekend_uses_same_friday_quote_and_does_not_invent_weekend_price(store, monkeypatch):
    async with store() as db:
        _, cmd, current = await clocked(db, monkeypatch)
        await add_nav(db, cmd, '2026-10-16', '1.23450000', '2026-10-16T12:00:00+00:00')
        current[0] = datetime(2026, 10, 17, 3, tzinfo=timezone.utc)
        saturday = await bank.precheck(db, 'U1', cmd)
        current[0] = datetime(2026, 10, 18, 14, tzinfo=timezone.utc)
        sunday = await bank.precheck(db, 'U1', cmd)
        assert saturday['estimated_nav_date'] == sunday['estimated_nav_date'] == '2026-10-16'
        assert saturday['command']['nav_quote_id'] == sunday['command']['nav_quote_id']
        assert saturday['estimate'] == sunday['estimate']


@pytest.mark.asyncio
async def test_unpublished_future_nav_is_not_used_and_new_quote_is_required_after_cutoff(store, monkeypatch):
    async with store() as db:
        _, cmd, current = await clocked(db, monkeypatch)
        await add_nav(db, cmd, '2026-10-15', '1.00000000', '2026-10-15T12:00:00+00:00')
        await add_nav(db, cmd, '2026-10-16', '1.01000000', '2026-10-16T11:00:00+00:00')
        current[0] = datetime(2026, 10, 16, 9, tzinfo=timezone.utc)  # Beijing 17:00
        assert (await bank.precheck(db, 'U1', cmd))['estimated_nav_date'] == '2026-10-15'
        current[0] = datetime(2026, 10, 16, 10, 30, tzinfo=timezone.utc)
        with pytest.raises(BankError) as failed:
            await bank.precheck(db, 'U1', cmd)
        assert failed.value.code == 'NAV_UPDATE_REQUIRED'
        current[0] = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
        assert (await bank.precheck(db, 'U1', cmd))['estimated_nav_date'] == '2026-10-16'


@pytest.mark.asyncio
async def test_multiple_buys_and_partial_fifo_redemption_keep_correct_remaining_cost(store, monkeypatch):
    from tests.integration.test_wealth_dynamic_quotes import authorize
    from backend.app.models.wealth import WealthLotORM
    async with store() as db:
        _, cmd, current = await clocked(db, monkeypatch)
        await add_nav(db, cmd, '2026-10-15', '1.00000000', '2026-10-15T12:00:00+00:00')
        current[0] = datetime(2026,10,16,9,tzinfo=timezone.utc)
        async def execute(command):
            from backend.app.schemas.wealth import InvestmentCommand
            bound = InvestmentCommand.model_validate((await bank.precheck(db,'U1',command))['command'])
            await bank.accept_many(db,'U1',[(bound,await authorize(db,bound))])
        first, second = sorted(['cost-first-purchase','cost-second-purchase'],
            key=lambda op: bank.stable_id('LOT',op), reverse=True)
        # Same-day FIFO must follow acceptance time, not hash/lot-id ordering.
        await execute(cmd.model_copy(update={'operation_id':first,'amount_fen':100000}))
        await add_nav(db, cmd, '2026-10-16', '2.00000000', '2026-10-16T12:00:00+00:00')
        current[0] = datetime(2026,10,16,13,tzinfo=timezone.utc)
        await execute(cmd.model_copy(update={'operation_id':second,'amount_fen':100000}))
        lots = list(await db.scalars(select(WealthLotORM)))
        assert sum(l.cost_fen for l in lots) == 200000
        assert sum(l.units_scaled for l in lots) == 1500*10000
        await execute(cmd.model_copy(update={'operation_id':'cost-fifo-redemption','direction':'redeem',
            'amount_fen':0,'units':'750.0000'}))
        lots = list(await db.scalars(select(WealthLotORM)))
        assert sum(l.cost_fen for l in lots) == 125000
        assert sum(l.units_scaled for l in lots) == 750*10000
