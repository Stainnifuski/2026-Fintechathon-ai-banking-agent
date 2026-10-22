"""Exercise the current bound, immediate wealth flow with the shared breaker."""
from datetime import datetime, timedelta, timezone
import pyotp
import pytest
from sqlalchemy import select

from backend.app.models import AccountORM, UserORM
from backend.app.models.wealth import WealthAuthorizationORM
from backend.app.security import circuit_breaker as safety
from backend.app.services.bank import wealth_service as bank, data_service as data, BankError
from backend.app.integrations.wealth_authorization import new_preview_authorization, record_confirmation
from backend.app.schemas.wealth import PersonalConfirmation
from tests.integration.test_wealth_bank import store, command, authorize
from tests.integration.test_wealth_instant import setup, bound


async def lock(db):
    await db.rollback()
    for _ in range(5):
        row = await safety.record_failure(db, key=safety.user_key('U1'), user_id='U1',
            scope='trade', category='mfa', reason='TOTP_INVALID')
    return row


@pytest.mark.asyncio
async def test_settled_result_survives_lock_but_new_purchase_is_denied(store, monkeypatch):
    async with store() as db:
        await setup(db, monkeypatch)
        first = await bound(db, command('security-settled'))
        first_auth = await authorize(db, first)
        pending = await bound(db, command('security-pending'))
        pending_auth = await authorize(db, pending)
        settled = (await bank.accept_many(db, 'U1', [(first, first_auth)]))[0]
        assert settled['status'] == 'SETTLED'
        balance = (await db.get(AccountORM, 'A1')).balance_fen
        await lock(db)
        assert (await bank.accept_many(db, 'U1', [(first, first_auth)]))[0] == settled
        with pytest.raises(safety.SecurityError) as error:
            await bank.accept_many(db, 'U1', [(pending, pending_auth)])
        assert error.value.code == 'SECURITY_LOCKED'
        await db.rollback()
        assert (await db.get(AccountORM, 'A1')).balance_fen == balance


@pytest.mark.asyncio
async def test_wealth_mfa_joins_shared_budget_and_rollback_is_safe(store, monkeypatch):
    async with store() as db:
        await setup(db, monkeypatch)
        user = await db.get(UserORM, 'U1')
        secret = pyotp.random_base32()
        user.totp_secret, user.totp_enabled = secret, True
        await db.commit()
        cmd = await bound(db, command('security-verification'))
        row = await new_preview_authorization(db, 'U1', cmd)
        aid, digest = row.authorization_id, row.param_digest
        await db.commit()
        await record_confirmation(db, 'U1', aid, PersonalConfirmation(digest=digest, disclosure_version=1, accepted=True))
        wrong = next(code for code in ['000000', '111111', '222222', '333333'] if not pyotp.TOTP(secret).verify(code, valid_window=1))
        for _ in range(2):
            with pytest.raises(BankError): await data.verify_totp_for_operation(db, 'U1', wrong)
        for index in range(3):
            with pytest.raises(BankError) as error:
                await bank.verify_authorization(db, 'U1', aid, wrong)
            assert error.value.code == ('SECURITY_LOCKED' if index == 2 else 'TOTP_INVALID')
            await db.rollback()
        assert (await safety.status(db, 'U1'))['trade_locked']
        assert (await db.get(WealthAuthorizationORM, aid)).status == 'AWAITING_MFA'


@pytest.mark.asyncio
async def test_expired_trade_lock_still_rejects_old_wealth_grant(store, monkeypatch):
    async with store() as db:
        await setup(db, monkeypatch)
        cmd = await bound(db, command('security-old-grant'))
        aid = await authorize(db, cmd)
        locked = await lock(db)
        later = safety.aware(locked.locked_until) + timedelta(seconds=1)
        monkeypatch.setattr(safety, 'now_utc', lambda: later)
        with pytest.raises(safety.SecurityError) as error:
            await bank.accept_many(db, 'U1', [(cmd, aid)])
        assert error.value.code == 'SECURITY_RECONFIRM_REQUIRED'


@pytest.mark.asyncio
async def test_bank_commit_and_lock_are_serialized_on_same_subject(store, monkeypatch):
    import asyncio
    from backend.app.services.bank.transfer_service import submit_transfer, TrustedExecutionContext
    from tests.security.test_circuit_breaker import command
    monkeypatch.setenv('SECURITY_VERIFICATION_FAILURES', '1')
    entered, release = asyncio.Event(), asyncio.Event()
    original_gate = safety.require_trade
    async with store() as transfer_db, store() as security_db:
        async def gate(db, user_id, **kwargs):
            state = await original_gate(db, user_id, **kwargs)
            if db is transfer_db:
                entered.set()
                await release.wait()
            return state
        monkeypatch.setattr(safety, 'require_trade', gate)
        first = asyncio.create_task(submit_transfer(transfer_db, command('security-inflight-commit'),
                                                    TrustedExecutionContext('U1', 'auth', 1)))
        await asyncio.wait_for(entered.wait(), 5)
        locking = asyncio.create_task(safety.record_failure(security_db, key=safety.user_key('U1'),
            user_id='U1', scope='trade', category='mfa', reason='TOTP_INVALID'))
        try:
            # A bank request that already owns the final gate finishes first.
            await asyncio.sleep(0.03)
            assert not locking.done()
        finally:
            release.set()
        result, locked = await asyncio.wait_for(asyncio.gather(first, locking), 10)
        assert result.status == 'SUCCEEDED' and locked.generation == 1
        with pytest.raises(safety.SecurityError):
            await submit_transfer(transfer_db, command('security-after-lock'), TrustedExecutionContext('U1', 'auth', 1))
        await transfer_db.rollback()
        assert (await transfer_db.get(AccountORM, 'A1')).balance_fen == 1_999_900
