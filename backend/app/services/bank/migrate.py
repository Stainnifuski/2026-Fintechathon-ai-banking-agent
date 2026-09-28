"""停服后使用的银行金额迁移/回退命令；不运行种子数据。"""

import argparse
import asyncio

from ...database.connection import engine
from .migration_service import migrate_bank_money


async def run(rollback: bool) -> None:
    try:
        async with engine.begin() as connection:
            await migrate_bank_money(connection, rollback=rollback)
        print("银行金额迁移完成" if not rollback else "银行金额结构已回退，请启动对应旧版本")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollback-money", action="store_true")
    asyncio.run(run(parser.parse_args().rollback_money))
