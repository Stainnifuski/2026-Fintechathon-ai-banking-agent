"""数据库连接管理"""

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..models.base import Base

# ──────────────────── 配置 ────────────────────

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./banking_agent.db",
)

# ──────────────────── 引擎 & 会话 ────────────────────

engine_options: dict = {"echo": False}
if not DATABASE_URL.startswith("sqlite"):
    engine_options.update({"pool_size": 10, "max_overflow": 20, "pool_pre_ping": True})
engine = create_async_engine(DATABASE_URL, **engine_options)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ──────────────────── 依赖注入 ────────────────────


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话（FastAPI Depends 使用）。

    Yields:
        AsyncSession: 异步数据库会话。
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# ──────────────────── 生命周期 ────────────────────


async def init_db() -> None:
    """初始化数据库，创建所有表。"""
    from ..services.bank.migration_service import (
        upgrade_card_columns,
        upgrade_flower_purchase_transactions,
        upgrade_legacy_user_mfa_columns,
        upgrade_phone_registry,
        migrate_bank_money,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await upgrade_legacy_user_mfa_columns(conn)
        await upgrade_card_columns(conn)
        await migrate_bank_money(conn)
    await upgrade_phone_registry(AsyncSessionLocal)
    await upgrade_flower_purchase_transactions(AsyncSessionLocal)
    from ..services.bank.wealth_migration import upgrade
    async with AsyncSessionLocal() as session:
        await upgrade(session)
        await session.commit()


async def close_db() -> None:
    """关闭数据库连接池。"""
    await engine.dispose()
