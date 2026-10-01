"""隔离SQLite银行理财事实测试，不访问现有业务库。"""

import asyncio
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.app.models import Base, UserORM, AccountORM, TransactionORM
from backend.app.models.wealth import (WealthCashHoldORM, WealthClockORM, WealthNavORM,
    WealthLotORM, WealthSettlementORM, WealthOrderORM, WealthAuthorizationORM)
from backend.app.domain.wealth_risk import QUESTIONS, QUESTIONNAIRE_VERSION
from backend.app.domain.wealth import CALENDAR_VERSION
from backend.app.schemas.wealth import AssessmentSubmission, InvestmentCommand, PersonalConfirmation
from backend.app.integrations.wealth_authorization import (submit_assessment, confirm_assessment,
    new_preview_authorization, record_confirmation, WealthPolicyError)
from backend.app.services.bank.wealth_catalog import seed_catalog
from backend.app.services.bank import wealth_service as bank
from backend.app.services.bank.transfer_service import BankError, submit_transfer, BankTransferCommand, TrustedExecutionContext, _digest
from dataclasses import replace

NOW = datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def store(tmp_path):
    """每例独立文件，写锁行为不依赖内存连接共享。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'wealth_test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add_all([UserORM(user_id=uid, name=uid, phone=f"1380000000{i}", id_card="mock", status="active")
                    for i, uid in enumerate(("U1", "U2"), 1)])
        await db.flush()
        db.add_all([AccountORM(account_id=f"A{i}", user_id=f"U{i}", account_type="savings",
                               card_number="mock", balance_fen=2_000_000, daily_transfer_limit_fen=5_000_000)
                    for i in (1, 2)])
        db.add(WealthClockORM(clock_id="mock", business_at=NOW, calendar_version=CALENDAR_VERSION))
        await seed_catalog(db)
        await db.commit()
    yield sessions
    await engine.dispose()


def command(key: str, *, direction: str = "purchase", amount: int = 100_000,
            quantity: str = "0.0000", product: str = "MF002") -> InvestmentCommand:
    """创建虚构受测命令。"""
    return InvestmentCommand(operation_id=key, direction=direction, product_id=product,
        product_version=1, disclosure_version=1, account_id="A1", amount_fen=amount, units=quantity)


async def assess(db) -> None:
    """本人回答与确认，测试不替生产环境回答。"""
    row = await submit_assessment(db, "U1", AssessmentSubmission(questionnaire_version=QUESTIONNAIRE_VERSION,
        profile_version=1, adult=True, full_capacity=True, answers={key: 3 for key, _, _ in QUESTIONS}))
    await confirm_assessment(db, "U1", row["assessment_id"])


async def authorize(db, cmd: InvestmentCommand) -> str:
    """只用于银行层夹具模拟3号已完成验证；HTTP验证另行验收。"""
    row = await new_preview_authorization(db, "U1", cmd)
    aid, digest = row.authorization_id, row.param_digest
    await db.commit()
    await record_confirmation(db, "U1", aid, PersonalConfirmation(digest=digest, disclosure_version=1, accepted=True))
    row = await db.get(WealthAuthorizationORM, aid)
    row.verified_at = datetime.now(timezone.utc)
    row.status = "VALID"
    await db.commit()
    return aid


async def move(db, days: int) -> None:
    """测试专用业务推进，不改变安全时间。"""
    row = await db.get(WealthClockORM, "mock")
    row.business_at = NOW + timedelta(days=days)
    await db.commit()


@pytest.mark.asyncio
async def test_requires_personal_assessment_and_bound_authorization(store):
    async with store() as db:
        with pytest.raises(WealthPolicyError, match="本人"):
            await bank.precheck(db, "U1", command("missing-assessment"))
        await assess(db)
        with pytest.raises(WealthPolicyError):
            await bank.accept_many(db, "U1", [(command("missing-auth-001"), "arbitrary-nonempty")])
        await db.rollback()
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM)) == 0


@pytest.mark.asyncio
async def test_purchase_partial_redemption_and_cash_exactly_once(store):
    async with store() as db:
        await assess(db)
        cmd = command("purchase-roundtrip")
        aid = await authorize(db, cmd)
        accepted = (await bank.accept_many(db, "U1", [(cmd, aid)]))[0]
        assert accepted["status"] == "ACCEPTED"
        data = await bank.assets(db, "U1")
        assert data["cash"][0]["available_fen"] == 1_900_000 and data["lots"] == []
        await move(db, 12)
        result = await bank.settle(db, cmd.operation_id)
        assert result["status"] == "SETTLED"
        balance = (await db.get(AccountORM, "A1")).balance_fen
        assert (await bank.settle(db, cmd.operation_id)) == result
        assert (await db.get(AccountORM, "A1")).balance_fen == balance
        sell = command("redeem-roundtrip", direction="redeem", amount=0, quantity="10.0000")
        aid2 = await authorize(db, sell)
        await bank.accept_many(db, "U1", [(sell, aid2)])
        await move(db, 13)
        pending = await bank.settle(db, sell.operation_id)
        assert pending["status"] == "PENDING_CASH"
        assert (await db.get(AccountORM, "A1")).balance_fen == balance
        data = await bank.assets(db, "U1")
        assert data["receivable_fen"] > 0
        await move(db, 14)
        final = await bank.settle(db, sell.operation_id)
        assert final["status"] == "SETTLED"
        assert (await db.get(AccountORM, "A1")).balance_fen == balance+final["details"]["actual"]["cash_fen"]
        assert await db.scalar(select(func.count()).select_from(WealthSettlementORM)) == 3


@pytest.mark.asyncio
async def test_same_operation_parameter_conflict_and_cross_user(store):
    async with store() as db:
        await assess(db)
        cmd = command("retry-operation")
        aid = await authorize(db, cmd)
        first = await bank.accept_many(db, "U1", [(cmd, aid)])
        assert await bank.accept_many(db, "U1", [(cmd, aid)]) == first
        with pytest.raises(BankError, match="参数冲突"):
            await bank.accept_many(db, "U1", [(cmd.model_copy(update={"amount_fen": 100001}), aid)])
        await db.rollback()
        with pytest.raises(BankError):
            await bank.owned_order(db, "U2", cmd.operation_id)


@pytest.mark.asyncio
async def test_cash_hold_blocks_existing_transfer_and_cancellation_releases(store):
    async with store() as db:
        await assess(db)
        cmd = command("hold-transfer", amount=1_900_000)
        aid = await authorize(db, cmd)
        await bank.accept_many(db, "U1", [(cmd, aid)])
        transfer = BankTransferCommand("held-transfer-blocked", "A1", "A2", 200_000, "CNY", "")
        transfer = replace(transfer, param_digest=_digest(transfer))
        with pytest.raises(BankError, match="余额不足"):
            await submit_transfer(db, transfer, TrustedExecutionContext("U1", "existing-transfer-test", 1))
        assert (await db.get(AccountORM, "A1")).balance_fen == 2_000_000
        assert (await bank.cancel(db, "U1", cmd.operation_id))["status"] == "CANCELLED"
        assert (await bank.assets(db, "U1"))["cash"][0]["available_fen"] == 2_000_000


@pytest.mark.asyncio
async def test_batch_budget_atomic_rollback(store):
    async with store() as db:
        await assess(db)
        a, b = command("batch-first", amount=1_500_000), command("batch-second", amount=1_500_000)
        aid1, aid2 = await authorize(db, a), await authorize(db, b)
        with pytest.raises(BankError, match="可用现金不足"):
            await bank.accept_many(db, "U1", [(a, aid1), (b, aid2)])
        await db.rollback()
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM)) == 0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM)) == 0


@pytest.mark.asyncio
async def test_missing_nav_does_not_use_future_or_debit(store):
    async with store() as db:
        await assess(db)
        cmd = command("missing-nav-order")
        aid = await authorize(db, cmd)
        await bank.accept_many(db, "U1", [(cmd, aid)])
        await db.execute(delete(WealthNavORM).where(WealthNavORM.nav_id == "MF002:1:2026-09-30"))
        await db.commit()
        await move(db, 12)
        result = await bank.settle(db, cmd.operation_id)
        assert result["status"] == "PENDING_PRICING" and result["details"]["waiting_reason"] == "NAV_UNAVAILABLE"
        assert (await db.get(AccountORM, "A1")).balance_fen == 2_000_000


@pytest.mark.asyncio
async def test_concurrent_reservation_never_overspends(store):
    async with store() as db:
        await assess(db)
        a, b = command("concurrent-first", amount=1_500_000), command("concurrent-second", amount=1_500_000)
        aid1, aid2 = await authorize(db, a), await authorize(db, b)
    async def submit(cmd, aid):
        async with store() as db:
            try:
                return await bank.accept_many(db, "U1", [(cmd, aid)])
            except BankError as error:
                await db.rollback()
                return error.code
    results = await asyncio.gather(submit(a, aid1), submit(b, aid2))
    assert sum(isinstance(row, list) for row in results) == 1
    assert "INSUFFICIENT_AVAILABLE_FUNDS" in results
