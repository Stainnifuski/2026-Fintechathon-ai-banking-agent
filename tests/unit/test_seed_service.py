"""默认演示客户不预存联系人。"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import AccountORM, Base, ContactORM, SubscriptionORM, TransactionORM, UserORM
from backend.app.models.bill_foundation import BillTransactionMetaORM
from backend.app.services.bank import seed_service
from backend.app.security.passwords import verify_password


@pytest.mark.asyncio
async def test_seed_has_contacts_and_shop_merchants(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(seed_service, "AsyncSessionLocal", sessions)

    await seed_service.seed_bank_data()
    await seed_service.seed_bank_data()

    async with sessions() as session:
        users = (
            await session.execute(select(UserORM).order_by(UserORM.user_id))
        ).scalars().all()
        contacts = (await session.execute(select(ContactORM))).scalars().all()
        account_count = await session.scalar(select(func.count()).select_from(AccountORM))
        subscriptions = (
            await session.execute(
                select(SubscriptionORM).order_by(SubscriptionORM.subscription_id)
            )
        ).scalars().all()
        transaction_count = await session.scalar(select(func.count()).select_from(TransactionORM))
    assert [user.phone for user in users] == [
        "13800000001",
        "13800000002",
        "13800000003",
        "13800000004",
        "13800000005",
        "13800000006",
        "13800000007",
        "13800000008",
    ]
    assert contacts == []
    assert account_count == 9
    assert transaction_count == 0
    assert all(subscription.source_transaction_id is None for subscription in subscriptions)
    assert users[-1].name == "甜时光蛋糕店"
    assert len(subscriptions) == 2
    assert [(item.merchant_name, item.status) for item in subscriptions] == [
        ("网易云音乐", "ACTIVE"),
        ("腾讯视频", "ACTIVE"),
    ]
    assert all(verify_password("password123", user.password_hash) for user in users)
    await engine.dispose()


@pytest.mark.asyncio
async def test_seed_removes_only_legacy_sample_transactions(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(seed_service, "AsyncSessionLocal", sessions)
    await seed_service.seed_bank_data()
    async with sessions() as session:
        session.add_all([
            TransactionORM(tx_id="TX20260901001", account_id="A001", type="salary",
                           amount_fen=100, counterparty="旧版演示", category="salary",
                           status="completed", channel="app"),
            TransactionORM(tx_id="USER-TRANSFER", account_id="A001", type="transfer_out",
                           amount_fen=200, counterparty="A003", category="transfer",
                           status="completed", channel="app"),
        ])
        await session.flush()
        session.add_all([BillTransactionMetaORM(tx_id="TX20260901001"),
                         BillTransactionMetaORM(tx_id="USER-TRANSFER")])
        subscription = await session.get(SubscriptionORM, "SUB001")
        subscription.source_transaction_id = "TX20260901001"
        await session.commit()
    await seed_service.seed_bank_data()
    async with sessions() as session:
        assert await session.get(TransactionORM, "TX20260901001") is None
        assert await session.get(BillTransactionMetaORM, "TX20260901001") is None
        assert await session.get(TransactionORM, "USER-TRANSFER") is not None
        assert await session.get(BillTransactionMetaORM, "USER-TRANSFER") is not None
        assert (await session.get(SubscriptionORM, "SUB001")).source_transaction_id is None
    await engine.dispose()
