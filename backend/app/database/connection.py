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
    "postgresql+asyncpg://bankuser:bankpass@localhost:5432/banking_agent",
)

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ──────────────────── 引擎 & 会话 ────────────────────

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """关闭数据库连接池。"""
    await engine.dispose()
