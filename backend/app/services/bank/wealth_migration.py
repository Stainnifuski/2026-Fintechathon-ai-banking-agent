"""理财增量迁移与安全回退；仅回退空业务表，禁止丢弃事实。"""

import argparse
import asyncio
import os

from sqlalchemy import select, func, update, text, inspect
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from ...models import Base
from ...models.wealth import WealthSchemaVersionORM
from .wealth_catalog import seed_catalog

SCHEMA_VERSION = 1


async def upgrade(session: AsyncSession) -> None:
    """首次或重复升级串行化，种子补缺且绝不重置现金。"""
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
    await session.execute(insert(WealthSchemaVersionORM).values(schema_id="wealth", version=SCHEMA_VERSION)
        .on_conflict_do_nothing(index_elements=["schema_id"]))
    row = (await session.scalars(select(WealthSchemaVersionORM).where(
        WealthSchemaVersionORM.schema_id == "wealth").with_for_update())).one()
    if row.version != SCHEMA_VERSION:
        raise ValueError("理财结构版本不兼容，不能静默降级")
    await session.execute(update(WealthSchemaVersionORM).where(WealthSchemaVersionORM.schema_id == "wealth")
        .values(version=WealthSchemaVersionORM.version))
    if session.get_bind().dialect.name == "postgresql":
        connection = await session.connection()
        columns = await connection.run_sync(lambda conn: inspect(conn).get_columns("transactions"))
        type_column = next(column for column in columns if column["name"] == "type")
        if type_column["type"].length < 32:
            await session.execute(text("ALTER TABLE transactions ALTER COLUMN type TYPE VARCHAR(32)"))
    await seed_catalog(session)


async def rollback(session: AsyncSession) -> None:
    """存在任何理财业务/个人数据时拒绝删表，必须保留新版或恢复备份。"""
    tables = [table for table in Base.metadata.sorted_tables if table.name.startswith("wealth_")]
    catalog_only = {"wealth_products", "wealth_product_versions", "wealth_navs", "wealth_schema_versions"}
    for table in tables:
        count = await session.scalar(select(func.count()).select_from(table))
        if table.name not in catalog_only and count:
            raise ValueError("存在理财业务或个人数据，禁止回退删表；保持新版或使用已核对备份")
    connection = await session.connection()
    for table in reversed(tables):
        await connection.run_sync(lambda conn, target=table: target.drop(conn, checkfirst=True))


async def main(rollback_requested: bool) -> None:
    """命令行迁移，不播种用户、余额或流水。"""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError("请显式设置目标DATABASE_URL，迁移前停止写入并备份")
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            if conn.dialect.name == "sqlite":
                # SQLite驱动默认DDL可提前提交；显式BEGIN让新表与种子共同回滚。
                await conn.exec_driver_sql("BEGIN IMMEDIATE")
            if not rollback_requested:
                tables = [table for table in Base.metadata.sorted_tables if table.name.startswith("wealth_")]
                await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
            async with AsyncSession(bind=conn, expire_on_commit=False) as session:
                await (rollback(session) if rollback_requested else upgrade(session))
                await session.flush()
        print("理财空库回退完成" if rollback_requested else "理财增量升级完成；未改变账户余额")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="模拟理财增量升级／空业务安全回退")
    parser.add_argument("--rollback", action="store_true")
    asyncio.run(main(parser.parse_args().rollback))
