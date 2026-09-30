"""Create a NEW isolated SQLite demo; never append fictional history to a real ledger."""
import argparse
import asyncio
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def create():
    from sqlalchemy import delete
    from backend.app.database.connection import AsyncSessionLocal, init_db, close_db
    from backend.app.database.seed import seed_data
    from backend.app.models.transaction import TransactionORM
    from backend.app.services.bank.bill_demo import demo_transactions
    from backend.app.services.bank.bill_service import analyze, TZ
    from backend.app.schemas.bills import BillQuery
    from datetime import datetime
    await init_db()
    await seed_data()
    async with AsyncSessionLocal() as db:
        # Exclusively created by this script: no user data exists in this file.
        await db.execute(delete(TransactionORM))
        db.add_all(demo_transactions())
        await db.commit()
        result = await analyze(db, "U001", BillQuery(date="2026-09"), now=datetime(2026, 10, 1, tzinfo=TZ))
        print("2026-09 预期统计（分）：", result["totals"])
        print("待核对提示：", len(result["anomalies"]))
    await close_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    destination = args.output.resolve()
    try:
        with destination.open("xb"):
            pass
    except FileExistsError:
        parser.error("目标文件已存在；请选择新的独立演示数据库路径")
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + str(destination)
    asyncio.run(create())
    print(f"已创建虚构演示账单：{destination}；演示余额不是这些历史流水的对账余额。")
