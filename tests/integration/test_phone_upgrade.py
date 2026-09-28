"""旧库升级及重复初始化不得更改已有银行事实。"""

import pytest
from decimal import Decimal
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import Base, AccountORM, TransactionORM, UserORM
from backend.app.models.user_phone_registry import UserPhoneRegistryORM
from backend.app.services.bank.migration_service import (
    upgrade_legacy_user_mfa_columns, upgrade_phone_registry,
)
from backend.app.services.bank import seed_service


@pytest.mark.asyncio
async def test_merge_upgrade_adds_mfa_columns_to_legacy_users(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pre-mfa.db'}")
    async with engine.begin() as connection:
        await connection.execute(text(
            "CREATE TABLE users (user_id VARCHAR(32) PRIMARY KEY, "
            "name VARCHAR(64), phone VARCHAR(20), id_card VARCHAR(32), "
            "password_hash VARCHAR(256), risk_level VARCHAR(20), status VARCHAR(16), "
            "created_at DATETIME, updated_at DATETIME)"
        ))
        await connection.execute(text(
            "INSERT INTO users (user_id, name, phone, id_card, password_hash, risk_level, status) "
            "VALUES ('U1', '甲', '13800000001', 'demo', 'hash', 'balanced', 'active')"
        ))
        await connection.run_sync(Base.metadata.create_all)
        await upgrade_legacy_user_mfa_columns(connection)
        await upgrade_legacy_user_mfa_columns(connection)
        columns = await connection.run_sync(
            lambda sync_connection: {c['name'] for c in inspect(sync_connection).get_columns('users')}
        )
        assert {'totp_secret', 'totp_enabled', 'totp_last_timestep'} <= columns
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await upgrade_phone_registry(factory)
    async with factory() as session:
        user = await session.get(UserORM, 'U1')
        assert user.totp_enabled is False
        assert user.phone == '13800000001'
    await engine.dispose()


@pytest.mark.asyncio
async def test_old_database_phone_upgrade_repeated(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[table for table in Base.metadata.sorted_tables
                        if table.name != "user_phone_registry"],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(UserORM(
            user_id="U1", name="甲", phone="+86 138-0000-0001",
            id_card="demo", password_hash="x", status="active",
        ))
        await session.commit()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await upgrade_phone_registry(factory)
    await upgrade_phone_registry(factory)
    async with factory() as session:
        rows = (await session.execute(select(UserPhoneRegistryORM))).scalars().all()
        assert [(row.canonical_phone, row.user_id) for row in rows] == [
            ("13800000001", "U1")
        ]
        assert (await session.get(UserORM, "U1")).phone == "+86 138-0000-0001"
    await engine.dispose()


@pytest.mark.asyncio
async def test_old_database_conflicting_phones_stop_upgrade(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'conflict.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([
            UserORM(user_id="U1", name="甲", phone="13800000001",
                    id_card="demo", password_hash="x", status="active"),
            UserORM(user_id="U2", name="乙", phone="+86 138-0000-0001",
                    id_card="demo", password_hash="x", status="active"),
        ])
        await session.commit()
    with pytest.raises(RuntimeError, match="映射冲突"):
        await upgrade_phone_registry(factory)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(UserPhoneRegistryORM)) == 0
        assert await session.scalar(select(func.count()).select_from(UserORM)) == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_repeated_seed_preserves_customer_status_balance_and_transactions(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(seed_service, "AsyncSessionLocal", factory)
    await seed_service.seed_bank_data()
    async with factory() as session:
        user = await session.get(UserORM, "U001")
        account = await session.get(AccountORM, "A001")
        user.status = "frozen"
        account.balance = 123.45
        await session.commit()
        original_count = await session.scalar(select(func.count()).select_from(TransactionORM))
    await seed_service.seed_bank_data()
    async with factory() as session:
        assert (await session.get(UserORM, "U001")).status == "frozen"
        assert (await session.get(AccountORM, "A001")).balance == Decimal("123.45")
        assert await session.scalar(select(func.count()).select_from(TransactionORM)) == original_count
    await engine.dispose()
