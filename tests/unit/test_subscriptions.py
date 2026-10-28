"""订阅查询、归属隔离和取消行为。"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import AccountORM, Base, SubscriptionORM, TransactionORM, UserORM
from backend.app.services.bank import subscription_service
from backend.app.services.bank.transfer_service import BankError


@pytest.mark.asyncio
async def test_restore_for_testing_preserves_money_history_and_allows_cancellation_again(subscription_store):
    from backend.app.models.audit_log import AuditLogORM

    preview = await subscription_service.preview_subscription_cancellation(subscription_id="SUB1", user_id="U1")
    await subscription_service.cancel_subscription(subscription_id="SUB1", user_id="U1", state_version=1,
        confirmation_token=preview["confirmation_token"])
    restored = await subscription_service.restore_subscription_for_testing(
        subscription_id="SUB1", user_id="U1", state_version=2)
    assert restored["status"] == "ACTIVE" and restored["state_version"] == 3
    assert restored["cancelled_at"] is None
    for key in ("amount_fen", "currency", "billing_cycle", "next_charge_at", "source_transaction_id"):
        assert restored[key] == preview["subscription"][key]
    async with subscription_store() as session:
        assert (await session.get(AccountORM, "A1")).balance == 100
        assert list(await session.scalars(select(TransactionORM))) == []
        audit = await session.scalar(select(AuditLogORM).where(AuditLogORM.action == "subscription_restore_test"))
        assert audit.params["test_only"] is True
    with pytest.raises(BankError):
        await subscription_service.cancel_subscription(subscription_id="SUB1", user_id="U1", state_version=1,
            confirmation_token=preview["confirmation_token"])
    fresh = await subscription_service.preview_subscription_cancellation(subscription_id="SUB1", user_id="U1")
    cancelled = await subscription_service.cancel_subscription(subscription_id="SUB1", user_id="U1", state_version=3,
        confirmation_token=fresh["confirmation_token"])
    assert cancelled["status"] == "CANCELLED" and cancelled["state_version"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("user,version,code", [
    ("U2", 2, "SUBSCRIPTION_NOT_FOUND"),
    ("U1", 1, "STALE_STATE_VERSION"),
])
async def test_restore_for_testing_rejects_wrong_owner_and_stale_version(subscription_store, user, version, code):
    preview = await subscription_service.preview_subscription_cancellation(subscription_id="SUB1", user_id="U1")
    await subscription_service.cancel_subscription(subscription_id="SUB1", user_id="U1", state_version=1,
        confirmation_token=preview["confirmation_token"])
    with pytest.raises(BankError) as error:
        await subscription_service.restore_subscription_for_testing(subscription_id="SUB1", user_id=user, state_version=version)
    assert error.value.code == code
    assert (await subscription_service.list_subscriptions(user_id="U1"))[0]["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_restore_test_endpoint_authentication_and_state_checks(subscription_store):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from backend.app.middleware.auth import get_current_user
    from backend.app.routers.subscriptions import router

    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/v1/subscriptions/SUB1/restore-test", json={"state_version": 1})).status_code in (401, 403)
        app.dependency_overrides[get_current_user] = lambda: {"user_id": "U1"}
        response = await client.post("/api/v1/subscriptions/SUB1/restore-test", json={"state_version": 1})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "SUBSCRIPTION_NOT_CANCELLED"
        preview = await subscription_service.preview_subscription_cancellation(subscription_id="SUB1", user_id="U1")
        await subscription_service.cancel_subscription(subscription_id="SUB1", user_id="U1", state_version=1,
            confirmation_token=preview["confirmation_token"])
        response = await client.post("/api/v1/subscriptions/SUB1/restore-test", json={"state_version": 2})
        assert response.status_code == 200 and response.json()["status"] == "ACTIVE"
        assert (await client.post("/api/v1/subscriptions/SUB1/restore-test", json={"state_version": 2})).status_code == 409


@pytest_asyncio.fixture
async def subscription_store(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(subscription_service, "AsyncSessionLocal", sessions)
    async with sessions() as session:
        session.add_all([
            UserORM(user_id="U1", name="张三", phone="13800000001", id_card="demo", password_hash="hash", status="active"),
            UserORM(user_id="U2", name="李四", phone="13800000002", id_card="demo", password_hash="hash", status="active"),
            AccountORM(account_id="A1", user_id="U1", account_type="savings", card_number="6222****0001", balance=100, daily_transfer_used=0, daily_transfer_limit=50000, status="active"),
            AccountORM(account_id="A2", user_id="U2", account_type="savings", card_number="6222****0002", balance=100, daily_transfer_used=0, daily_transfer_limit=50000, status="active"),
            SubscriptionORM(subscription_id="SUB1", user_id="U1", debit_account_id="A1", merchant_name="网易云音乐", amount_fen=1500, currency="CNY", billing_cycle="MONTHLY", next_charge_at=datetime(2026, 10, 12, tzinfo=timezone.utc), status="ACTIVE", state_version=1, source_transaction_id="TX1"),
        ])
        await session.commit()
    yield sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_subscriptions_is_scoped_to_current_user(subscription_store) -> None:
    owner_items = await subscription_service.list_subscriptions(user_id="U1")
    other_items = await subscription_service.list_subscriptions(user_id="U2")

    assert len(owner_items) == 1
    assert owner_items[0]["merchant_name"] == "网易云音乐"
    assert owner_items[0]["amount_fen"] == 1500
    assert owner_items[0]["debit_account_label"] == "6222****0001"
    assert other_items == []


@pytest.mark.asyncio
async def test_cancel_subscription_updates_state_and_is_idempotent(subscription_store) -> None:
    preview = await subscription_service.preview_subscription_cancellation(subscription_id="SUB1", user_id="U1")
    cancelled = await subscription_service.cancel_subscription(
        subscription_id="SUB1", user_id="U1", state_version=1,
        confirmation_token=preview["confirmation_token"]
    )
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["state_version"] == 2
    assert cancelled["cancelled_at"] is not None

    duplicate = await subscription_service.cancel_subscription(
        subscription_id="SUB1", user_id="U1", state_version=1,
        confirmation_token=preview["confirmation_token"]
    )
    assert duplicate["status"] == "CANCELLED"
    assert duplicate["state_version"] == 2

    with pytest.raises(BankError) as not_owner:
        await subscription_service.cancel_subscription(
            subscription_id="SUB1", user_id="U2", state_version=1,
            confirmation_token=preview["confirmation_token"]
        )
    assert not_owner.value.code == "SUBSCRIPTION_NOT_FOUND"


@pytest.mark.asyncio
async def test_simulated_charge_updates_balance_history_and_next_date(subscription_store) -> None:
    result = await subscription_service.simulate_subscription_charge(
        subscription_id="SUB1",
        user_id="U1",
        state_version=1,
        idempotency_key="charge-request-1",
    )

    assert result["charged_amount"] == 15.0
    assert result["duplicate"] is False
    assert result["subscription"]["state_version"] == 2
    assert result["subscription"]["next_charge_at"].date().isoformat() == "2026-11-12"

    async with subscription_store() as session:
        account = await session.get(AccountORM, "A1")
        transaction = (
            await session.execute(
                select(TransactionORM).where(
                    TransactionORM.operation_id == "charge-request-1"
                )
            )
        ).scalar_one()
    assert account.balance == 85.0
    assert account.daily_transfer_used == 0
    assert transaction.type == "subscription"
    assert transaction.counterparty == "网易云音乐"
    assert transaction.amount == 15.0

    duplicate = await subscription_service.simulate_subscription_charge(
        subscription_id="SUB1",
        user_id="U1",
        state_version=1,
        idempotency_key="charge-request-1",
    )
    assert duplicate["duplicate"] is True
    async with subscription_store() as session:
        account = await session.get(AccountORM, "A1")
        transactions = (
            await session.execute(select(TransactionORM))
        ).scalars().all()
    assert account.balance == 85.0
    assert len(transactions) == 1


@pytest.mark.asyncio
async def test_scheduler_charges_only_due_cycle_once(subscription_store) -> None:
    from backend.app.services.orchestration.scheduler import execute_due_subscription_charges

    before = datetime(2026, 10, 11, 23, 59, tzinfo=timezone.utc)
    due = datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc)
    async with subscription_store() as session:
        assert await execute_due_subscription_charges(session, now=before) == 0
    async with subscription_store() as session:
        assert await execute_due_subscription_charges(session, now=due) == 1
        assert await execute_due_subscription_charges(session, now=due) == 0
        account = await session.get(AccountORM, "A1", populate_existing=True)
        subscription = await session.get(SubscriptionORM, "SUB1", populate_existing=True)
        transactions = (await session.scalars(select(TransactionORM))).all()
        assert account.balance == 85
        assert subscription.state_version == 2
        assert subscription.next_charge_at.date().isoformat() == "2026-11-12"
        assert len(transactions) == 1
        assert transactions[0].operation_id.startswith("subscription-renewal-")


@pytest.mark.asyncio
async def test_automatic_charge_rechecks_due_time_under_lock(subscription_store) -> None:
    with pytest.raises(BankError) as error:
        await subscription_service.simulate_subscription_charge(
            subscription_id="SUB1", user_id="U1", state_version=1,
            idempotency_key="premature-renewal", due_only=True,
            execution_time=datetime(2026, 10, 11, tzinfo=timezone.utc))
    assert error.value.code == "SUBSCRIPTION_NOT_DUE"
    async with subscription_store() as session:
        assert (await session.get(AccountORM, "A1")).balance == 100
        assert (await session.scalars(select(TransactionORM))).all() == []


@pytest.mark.asyncio
async def test_due_charge_waits_for_funds_then_retries_same_cycle(subscription_store) -> None:
    from backend.app.services.orchestration.scheduler import execute_due_subscription_charges

    due = datetime(2026, 10, 12, tzinfo=timezone.utc)
    async with subscription_store() as session:
        account = await session.get(AccountORM, "A1")
        account.balance = 10
        await session.commit()
        assert await execute_due_subscription_charges(session, now=due) == 0
        assert (await session.get(SubscriptionORM, "SUB1", populate_existing=True)).state_version == 1
        account = await session.get(AccountORM, "A1", populate_existing=True)
        account.balance = 100
        await session.commit()
        assert await execute_due_subscription_charges(session, now=due) == 1
        assert (await session.get(AccountORM, "A1", populate_existing=True)).balance == 85


@pytest.mark.asyncio
async def test_cancelled_subscription_cannot_be_charged(subscription_store) -> None:
    preview = await subscription_service.preview_subscription_cancellation(subscription_id="SUB1", user_id="U1")
    await subscription_service.cancel_subscription(
        subscription_id="SUB1", user_id="U1", state_version=1,
        confirmation_token=preview["confirmation_token"]
    )

    with pytest.raises(BankError) as cancelled:
        await subscription_service.simulate_subscription_charge(
            subscription_id="SUB1",
            user_id="U1",
            state_version=2,
            idempotency_key="charge-request-2",
        )
    assert cancelled.value.code == "SUBSCRIPTION_NOT_ACTIVE"

    async with subscription_store() as session:
        account = await session.get(AccountORM, "A1")
        transactions = (
            await session.execute(select(TransactionORM))
        ).scalars().all()
    assert account.balance == 100
    assert transactions == []



@pytest.mark.asyncio
async def test_concurrent_charge_with_distinct_keys_uses_one_cycle(tmp_path, monkeypatch) -> None:
    import asyncio
    from sqlalchemy import func

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'subscriptions-race.db'}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(subscription_service, "AsyncSessionLocal", factory)
    async with factory() as session:
        session.add_all([
            UserORM(user_id="U1", name="张三", phone="13800000001", id_card="demo", password_hash="hash", status="active"),
            AccountORM(account_id="A1", user_id="U1", account_type="savings", card_number="6222****0001", balance=100, daily_transfer_used=0, daily_transfer_limit=50000, status="active"),
            SubscriptionORM(subscription_id="SUB1", user_id="U1", debit_account_id="A1", merchant_name="演示订阅", amount_fen=1500, currency="CNY", billing_cycle="MONTHLY", next_charge_at=datetime(2026, 10, 12, tzinfo=timezone.utc), status="ACTIVE", state_version=1),
        ])
        await session.commit()

    async def charge(key: str):
        try:
            return await subscription_service.simulate_subscription_charge(
                subscription_id="SUB1", user_id="U1", state_version=1,
                idempotency_key=key,
            )
        except BankError as error:
            return error.code

    results = await asyncio.gather(charge("charge-one"), charge("charge-two"))
    assert sum(isinstance(item, dict) for item in results) == 1
    assert "STALE_STATE_VERSION" in results
    async with factory() as session:
        assert (await session.get(AccountORM, "A1")).balance == 85
        assert await session.scalar(select(func.count()).select_from(TransactionORM)) == 1
    await engine.dispose()
