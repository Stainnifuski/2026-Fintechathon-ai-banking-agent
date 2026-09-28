"""4号银行验收：SQLite与真实PostgreSQL共用并发、迁移、审计用例。"""

import asyncio
import os
from datetime import datetime, timezone
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import AccountORM, Base, TransactionORM, UserORM
from backend.app.models.audit_log import AuditLogORM
from backend.app.models.payment_request import PaymentRequestORM
from backend.app.models.subscription import SubscriptionORM
from backend.app.services.bank import payment_request_service as payments
from backend.app.services.bank.migration_service import migrate_bank_money
from backend.app.services.bank.transfer_service import (
    BankError, BankResponseLost, BankTransferCommand, TrustedExecutionContext,
    _digest, query_operation, submit_transfer,
)


@pytest_asyncio.fixture(params=["sqlite", "postgresql"])
async def bank_store(request, tmp_path, monkeypatch):
    admin = None
    schema = "bank_test_" + uuid4().hex
    if request.param == "postgresql":
        url = os.getenv("BANK_TEST_DATABASE_URL")
        if not url:
            pytest.skip("设置BANK_TEST_DATABASE_URL启用真实PostgreSQL验收")
        assert (make_url(url).database or "").startswith("bank_test_"), "禁止使用业务数据库"
        admin = create_async_engine(url)
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bank.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(payments, "AsyncSessionLocal", sessions)
        async with sessions() as session:
            session.add_all([UserORM(user_id=f"U{i}", name=f"用户{i}", phone=f"1380000000{i}",
                id_card="demo", password_hash="hash", status="active") for i in range(1, 4)])
            await session.flush()
            session.add_all([AccountORM(account_id=f"A{i}", user_id=f"U{i}",
                account_type="savings", card_number=str(i), balance=100,
                daily_transfer_used=0, daily_transfer_limit=50000, status="active")
                for i in range(1, 4)])
            await session.commit()
        yield engine, sessions
    finally:
        await engine.dispose()
        if admin:
            async with admin.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()


def command(operation: str, amount: int = 1234) -> BankTransferCommand:
    value = BankTransferCommand(operation, "A1", "A2", amount, "CNY", "", note=None)
    return replace(value, param_digest=_digest(value))


CONTEXT = TrustedExecutionContext("U1", "test-authorization", 1)


@pytest.mark.asyncio
async def test_concurrent_batch_http_and_parameter_conflict(bank_store):
    from fastapi import FastAPI
    from backend.app.middleware.auth import get_current_user
    from backend.app.middleware.errors import install_error_handlers
    from backend.app.routers.payment_requests import router

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "U1"}
    body = dict(payer_user_ids=["U2", "U3"], receive_account_id="A1", amount=20,
                split_equally=True, note=None, idempotency_key="concurrent-batch")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.gather(*[client.post("/api/v1/payment-requests/batch", json=body) for _ in range(4)])
        assert [r.status_code for r in responses] == [201] * 4
        assert all(r.json() == responses[0].json() for r in responses)
        changed = await client.post("/api/v1/payment-requests/batch", json={**body, "payer_user_ids": ["U2"]})
        assert changed.status_code == 409
        assert changed.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    async with bank_store[1]() as session:
        assert await session.scalar(select(func.count()).select_from(PaymentRequestORM)) == 2
        assert await session.scalar(select(func.count()).select_from(AuditLogORM)) == 2


@pytest.mark.asyncio
async def test_money_and_audit_exactly_once_after_lost_response(bank_store):
    sessions = bank_store[1]
    async with sessions() as session:
        with pytest.raises(BankResponseLost):
            await submit_transfer(session, command("lost-response-001"), CONTEXT, simulate_response_loss=True)
    async def retry():
        async with sessions() as session:
            return await submit_transfer(session, command("lost-response-001"), CONTEXT)
    results = await asyncio.gather(*[retry() for _ in range(4)])
    assert len({r.bank_tx_id for r in results}) == 1
    async with sessions() as session:
        assert (await session.get(AccountORM, "A1")).balance_fen == 8766
        assert (await session.get(AccountORM, "A2")).balance_fen == 11234
        assert await session.scalar(select(func.count()).select_from(TransactionORM)) == 2
        audits = (await session.scalars(select(AuditLogORM))).all()
        assert len(audits) == 1
        assert audits[0].params["authorization_id"] == CONTEXT.authorization_id
        assert "note" not in audits[0].params


@pytest.mark.asyncio
async def test_distinct_concurrent_operations_never_overdraw(bank_store):
    async def send(i):
        async with bank_store[1]() as session:
            try:
                await submit_transfer(session, command(f"concurrent-debit-{i}", 6000), CONTEXT)
                return "ok"
            except BankError as error:
                return error.code
    assert sorted(await asyncio.gather(send(1), send(2))) == ["INSUFFICIENT_FUNDS", "ok"]
    async with bank_store[1]() as session:
        assert (await session.get(AccountORM, "A1")).balance_fen == 4000
        assert await session.scalar(select(func.count()).select_from(AuditLogORM)) == 2


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_ledger(bank_store, monkeypatch):
    from backend.app.services.bank import transfer_service
    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(transfer_service, "record_bank_event", failing_audit)
    async with bank_store[1]() as session:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await submit_transfer(session, command("audit-failure-001"), CONTEXT)
    async with bank_store[1]() as session:
        assert (await session.get(AccountORM, "A1")).balance_fen == 10000
        assert await session.scalar(select(func.count()).select_from(TransactionORM)) == 0


@pytest.mark.asyncio
async def test_money_migration_roundtrip_and_invalid_history_atomic(bank_store):
    engine, sessions = bank_store
    async with sessions() as session:
        await submit_transfer(session, command("migration-seed-001"), CONTEXT)
    async with engine.begin() as conn:
        await migrate_bank_money(conn, rollback=True)
        await migrate_bank_money(conn, rollback=True)
        assert (await conn.execute(text("SELECT balance FROM accounts WHERE account_id='A1'"))).scalar_one() == 87.66
        await conn.execute(text("UPDATE transactions SET amount=0.001"))
    with pytest.raises(RuntimeError, match="金额异常"):
        async with engine.begin() as conn:
            await migrate_bank_money(conn)
    async with engine.begin() as conn:
        columns = await conn.run_sync(lambda c: {x["name"] for x in inspect(c).get_columns("accounts")})
        assert "balance" in columns and "balance_fen" not in columns
        await conn.execute(text("UPDATE transactions SET amount=12.34"))
        await migrate_bank_money(conn)
        await migrate_bank_money(conn)
    async with sessions() as session:
        assert (await session.get(AccountORM, "A1")).balance_fen == 8766
        assert set((await session.scalars(select(TransactionORM.amount_fen))).all()) == {1234}


@pytest.mark.asyncio
async def test_subscription_exact_cents_and_duplicate_audit(bank_store, monkeypatch):
    from backend.app.services.bank import subscription_service
    sessions = bank_store[1]
    monkeypatch.setattr(subscription_service, "AsyncSessionLocal", sessions)
    async with sessions() as session:
        session.add(SubscriptionORM(subscription_id="SUB1", user_id="U1", debit_account_id="A1",
            merchant_name="测试订阅", amount_fen=1501, currency="CNY", billing_cycle="MONTHLY",
            next_charge_at=datetime(2026, 10, 12, tzinfo=timezone.utc), status="ACTIVE",
            state_version=1, source_transaction_id="seed"))
        await session.commit()
    kwargs = dict(subscription_id="SUB1", user_id="U1", state_version=1, idempotency_key="subscription-cents")
    responses = await asyncio.gather(*[subscription_service.simulate_subscription_charge(**kwargs) for _ in range(3)])
    assert len({r["transaction_id"] for r in responses}) == 1
    async with sessions() as session:
        assert (await session.get(AccountORM, "A1")).balance_fen == 8499
        assert await session.scalar(select(func.count()).select_from(AuditLogORM)) == 1
        assert await session.scalar(select(TransactionORM.amount_fen)) == 1501
    with pytest.raises(ValueError):
        AccountORM(balance=100.001)


@pytest.mark.asyncio
async def test_validation_error_contract_hides_input():
    from fastapi import FastAPI
    from backend.app.middleware.errors import install_error_handlers
    from backend.app.routers import bank_operations
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(bank_operations.router)
    app.dependency_overrides[bank_operations.require_internal_token] = lambda: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/api/v1/internal/bank/transfers/directory", json={"subject_user_id": {"secret": "do-not-echo"}})
    assert result.status_code == 422
    assert result.json()["detail"]["code"] == "VALIDATION_ERROR"
    assert "do-not-echo" not in result.text
    schema = app.openapi()
    assert schema["paths"]["/api/v1/internal/bank/transfers/directory"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("TransferDirectoryView")
