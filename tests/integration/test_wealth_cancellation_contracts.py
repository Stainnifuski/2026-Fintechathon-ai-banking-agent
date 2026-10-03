"""E24–E29 contract endpoints and financial effects in independent SQLite/PG databases."""
from datetime import datetime, timezone
import pytest
from sqlalchemy import select, func
from tests.integration.test_wealth_bank import store, http_store, assess, authorize, command, move
from backend.app.models import AccountORM, TransactionORM
from backend.app.models.wealth import WealthClockORM, WealthCashHoldORM, WealthUnitHoldORM, WealthProductVersionORM
from backend.app.services.bank import wealth_service as bank
from backend.app.services.bank.transfer_service import BankError


async def clock(db, stamp):
    row=await db.get(WealthClockORM,'mock');row.business_at=datetime.fromisoformat(stamp).astimezone(timezone.utc);await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize('direction',['purchase','redeem'])
async def test_m1_deadline_exclusive_full_cancel_release_without_refund(store,http_store,direction):
    client,_,_=http_store
    async with store() as db:
        await assess(db)
        if direction=='redeem':
            seed=command('cancel-m1-seed',product='MF001')
            await bank.accept_many(db,'U1',[(seed,await authorize(db,seed))]);await move(db,12)
            assert (await bank.settle(db,seed.operation_id))['status']=='SETTLED'
        starting=(await db.get(AccountORM,'A1')).balance_fen
        transactions=await db.scalar(select(func.count()).select_from(TransactionORM))
        for label,instant,allowed in [
            ('before','2026-10-12T14:59:59+08:00',True),
            ('at','2026-10-12T15:00:00+08:00',False),
            ('after','2026-10-12T15:00:01+08:00',False)]:
            await clock(db,'2026-10-12T14:00:00+08:00')
            cmd=command('cancel-m1-'+direction+'-'+label,direction=direction,
                product='MF001',amount=10000 if direction=='purchase' else 0,quantity='1.0000' if direction=='redeem' else '0.0000')
            accepted=(await bank.accept_many(db,'U1',[(cmd,await authorize(db,cmd))]))[0]
            original=await bank.cancellation_eligibility(db,'U1',cmd.operation_id)
            assert original['eligibility']=='ALLOWED' and original['full_only']
            assert original['deadline']=='2026-10-12T07:00:00+00:00'
            await clock(db,instant)
            eligibility=await bank.cancellation_eligibility(db,'U1',cmd.operation_id)
            assert eligibility['eligibility']==('ALLOWED' if allowed else 'DENIED')
            assert eligibility['action']==('cancel_purchase' if direction=='purchase' else 'withdraw_standard_redemption')
            body={'digest':accepted['digest'],'disclosure_version':1,'accepted':True}
            # Client cannot request a partial cancel or submit stale digest.
            partial=await client.post(f'/api/v1/wealth/orders/{cmd.operation_id}/cancel',json={**body,'units':'0.5000'})
            assert partial.status_code==422
            stale=await client.post(f'/api/v1/wealth/orders/{cmd.operation_id}/cancel',json={**body,'digest':'0'*64})
            assert stale.status_code==409
            result=await client.post(f'/api/v1/wealth/orders/{cmd.operation_id}/cancel',json=body)
            assert result.status_code==(200 if allowed else 409),result.text
            if allowed:
                repeat=await client.post(f'/api/v1/wealth/orders/{cmd.operation_id}/cancel',json=body)
                assert repeat.json()==result.json()
                holds=list(await db.scalars(select(WealthCashHoldORM).where(WealthCashHoldORM.operation_id==cmd.operation_id))) if direction=='purchase' else list(await db.scalars(select(WealthUnitHoldORM).where(WealthUnitHoldORM.operation_id==cmd.operation_id)))
                for hold in holds:await db.refresh(hold)
                assert holds and all(hold.status=='RELEASED' for hold in holds)
            await db.refresh(await db.get(AccountORM,'A1'))
            assert (await db.get(AccountORM,'A1')).balance_fen==starting
            assert await db.scalar(select(func.count()).select_from(TransactionORM))==transactions


@pytest.mark.asyncio
async def test_m2_pending_denied_m3_missing_unknown_and_weekend_queue(store):
    async with store() as db:
        await assess(db)
        # M2 is an explicit non-cancellable fixture rule, before any order exists.
        version=await db.get(WealthProductVersionORM,'MF002:1')
        version.rules={**version.rules,'cancel_before_pricing':False};await db.commit()
        await clock(db,'2026-10-03T10:00:00+08:00')
        cmd=command('m2-weekend-pending')
        order=(await bank.accept_many(db,'U1',[(cmd,await authorize(db,cmd))]))[0]
        assert order['pricing_date']=='2026-10-08' # published mock holiday calendar, not real trading promise
        assert (await bank.cancellation_eligibility(db,'U1',cmd.operation_id))['eligibility']=='DENIED'
        with pytest.raises(BankError) as denied:await bank.cancel(db,'U1',cmd.operation_id)
        assert denied.value.code=='CANCELLATION_NOT_ALLOWED';await db.rollback()
        # Reproduce an existing legacy contract lacking the cancellation field (not a new valid product).
        version=await db.get(WealthProductVersionORM,'MF002:1')
        version.rules={key:value for key,value in version.rules.items() if key!='cancel_before_pricing'};await db.commit()
        assert (await bank.cancellation_eligibility(db,'U1',cmd.operation_id))['eligibility']=='UNKNOWN'
        with pytest.raises(BankError) as unknown:await bank.cancel(db,'U1',cmd.operation_id)
        assert unknown.value.code=='CANCELLATION_RULES_UNKNOWN';await db.rollback()
        assert (await db.get(AccountORM,'A1')).balance_fen==2000000
