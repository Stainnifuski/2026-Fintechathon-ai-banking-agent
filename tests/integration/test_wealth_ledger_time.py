"""E19/E20/E21：当前双库时钟、范围和历史证据修复。"""
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import select, func

from tests.integration.test_wealth_bank import store, REAL_BUSINESS_NOW, assess, authorize, command, move
from backend.app.domain.time import BEIJING, utc_iso, as_utc, utc_now
from backend.app.models import AccountORM, TransactionORM
from backend.app.models.wealth import WealthClockORM, WealthLedgerReviewORM
from backend.app.services.bank import wealth_service as bank, wealth_research as research
from backend.app.services.bank.wealth_reconciliation import reconcile, require_reconciled
from backend.app.services.bank.transfer_service import BankError


@pytest.mark.asyncio
async def test_ordinary_clock_ignores_mock_but_disposable_context_uses_business_clock(store):
    async with store() as db:
        clock = await db.get(WealthClockORM, "mock")
        clock.business_at = utc_now() + timedelta(days=100)
        await db.commit()
        real = await REAL_BUSINESS_NOW(db, "A1")
        assert abs((real-utc_now()).total_seconds()) < 2
        ctx = await research.initialize(db, "U1")
        await db.commit()
        simulated = await REAL_BUSINESS_NOW(db, ctx["account_id"])
        assert simulated.astimezone(BEIJING).date().isoformat() == "2026-09-30"


@pytest.mark.asyncio
async def test_reconciliation_preserves_money_and_restricts_ordinary_future_pricing(store):
    async with store() as db:
        await assess(db)
        cmd = command("historic-future-pricing")
        await bank.accept_many(db, "U1", [(cmd, await authorize(db, cmd))])
        await move(db, 30)
        # 强制结算测试合同未来净值，复现旧普通账户污染（仅隔离库）。
        result = await bank.settle(db, cmd.operation_id)
        assert result["status"] == "SETTLED"
        tx = await db.get(TransactionORM, bank.stable_id("WPT", cmd.operation_id))
        tx.timestamp = utc_now() + timedelta(days=100)
        await db.commit()
        before = (await db.get(AccountORM, "A1")).balance_fen
        preview = await reconcile(db)
        assert preview["rows"][0]["status"] == "RECONCILIATION_REQUIRED"
        assert await db.scalar(select(func.count()).select_from(WealthLedgerReviewORM)) == 0
        await reconcile(db, apply=True)
        assert (await db.get(AccountORM, "A1")).balance_fen == before
        with pytest.raises(BankError, match="核对"):
            await require_reconciled(db, "A1")
        repeated = await reconcile(db, apply=True)
        assert repeated["rows"][0]["already_recorded"]
        assert await db.scalar(select(func.count()).select_from(WealthLedgerReviewORM)) == 1


@pytest.mark.asyncio
async def test_proven_legacy_transaction_backlink_is_metadata_only_and_idempotent(store):
    async with store() as db:
        await assess(db)
        cmd = command('legacy-backlink')
        await bank.accept_many(db, 'U1', [(cmd, await authorize(db, cmd))])
        await move(db, 12)
        assert (await bank.settle(db, cmd.operation_id))['status'] == 'SETTLED'
        tx = await db.get(TransactionORM, bank.stable_id('WPT', cmd.operation_id))
        tx.operation_id = None
        await db.commit()
        before = (tx.amount_fen, (await db.get(AccountORM, 'A1')).balance_fen)
        preview = await reconcile(db)
        assert preview['ledger_links'][0]['evidence_verified']
        assert tx.operation_id is None
        await reconcile(db, apply=True)
        assert tx.operation_id == cmd.operation_id
        assert (tx.amount_fen, (await db.get(AccountORM, 'A1')).balance_fen) == before
        assert (await reconcile(db, apply=True))['ledger_links'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('evidence',[True,False])
async def test_legacy_acceptance_time_requires_bound_audit_and_preserves_business_dates(store,evidence):
    from backend.app.models.wealth import WealthOrderORM
    async with store() as db:
        await assess(db)
        cmd=command('legacy-acceptance-clock')
        await bank.accept_many(db,'U1',[(cmd,await authorize(db,cmd))])
        row=await db.get(WealthOrderORM,cmd.operation_id)
        dates=(row.pricing_date,row.confirm_date,row.cash_date)
        row.accepted_at=utc_now()+timedelta(days=100)
        if not evidence:
            from sqlalchemy import delete
            from backend.app.models.audit_log import AuditLogORM
            await db.execute(delete(AuditLogORM).where(AuditLogORM.action=='investment_accept'))
        await db.commit()
        preview=await reconcile(db)
        assert preview['acceptance_times'][0]['evidence_verified']==evidence
        assert as_utc(row.accepted_at,legacy_utc=True)>utc_now()+timedelta(days=99)
        await reconcile(db,apply=True)
        if evidence:
            assert abs((as_utc(row.accepted_at,legacy_utc=True)-utc_now()).total_seconds())<10
        else:
            assert as_utc(row.accepted_at,legacy_utc=True)>utc_now()+timedelta(days=99)
        assert (row.pricing_date,row.confirm_date,row.cash_date)==dates
        assert (await db.get(AccountORM,'A1')).balance_fen==2000000
        assert (await reconcile(db,apply=True))['acceptance_times'][0]['already_recorded']
        with pytest.raises(BankError):await require_reconciled(db,'A1')


def test_beijing_instant_and_unknown_naive():
    event = datetime(2026, 10, 2, 16, tzinfo=timezone.utc)
    assert event.astimezone(BEIJING).isoformat() == "2026-10-03T00:00:00+08:00"
    assert utc_iso(event.astimezone(BEIJING)) == "2026-10-02T16:00:00Z"
    with pytest.raises(ValueError):
        as_utc(event.replace(tzinfo=None))


@pytest.mark.asyncio
async def test_bank_dto_keeps_utc_and_bill_scope_excludes_sandbox(store):
    """SQLite丢失tzinfo也不能让浏览器把同一事件理解成本地时间。"""
    from backend.app.services.bank.data_service import _transaction_dict
    from backend.app.services.bank.bill_service import scope
    from backend.app.schemas.bills import BillQuery
    async with store() as db:
        ctx = await research.initialize(db, 'U1')
        db.add(TransactionORM(tx_id='timezone-fact', account_id='A1', type='salary',
            amount_fen=100, counterparty='虚构', category='other', note=None,
            timestamp=datetime(2026, 10, 2, 16, tzinfo=timezone.utc), status='completed', channel='app'))
        await db.commit()
        db.expire_all()
        row = await db.get(TransactionORM, 'timezone-fact')
        event = _transaction_dict(row)['timestamp']
        assert event.isoformat() == '2026-10-02T16:00:00+00:00'
        assert event.astimezone(BEIJING).isoformat() == '2026-10-03T00:00:00+08:00'
        predicates = await scope(db, 'U1', BillQuery())
        accounts = list(await db.scalars(select(TransactionORM.account_id).where(*predicates)))
        assert accounts == ['A1']
        with pytest.raises(BankError):
            await scope(db, 'U1', BillQuery(account_id=ctx['account_id']))


@pytest.mark.asyncio
async def test_nav_publication_instant_and_beijing_date_prevent_future_leak(store):
    from backend.app.models.wealth import WealthNavORM
    from backend.app.services.bank.wealth_quick import quote_key
    from datetime import date
    async with store() as db:
        row=await db.scalar(select(WealthNavORM).where(WealthNavORM.version_id=='MF002:1',WealthNavORM.nav_date==date(2026,10,12)))
        row.available_at=datetime(2026,10,11,16,tzinfo=timezone.utc)
        await db.commit()
        instant=datetime(2026,10,11,16,0,1,tzinfo=timezone.utc)
        visible=await bank.latest_nav(db,row.version_id,instant)
        assert visible.nav_date==date(2026,10,12)
        same=await bank.latest_nav(db,row.version_id,instant.astimezone(BEIJING))
        assert same.nav_id==visible.nav_id
        quote=quote_key(visible)
        row.available_at=instant+timedelta(hours=1);await db.commit()
        assert quote_key(row)!=quote
        older=await bank.latest_nav(db,row.version_id,instant)
        assert older.nav_date<date(2026,10,12)
