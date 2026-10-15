"""New isolated fixture for change attribution, subscription review and cashflow scenarios."""
import argparse
import asyncio
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def create():
    from sqlalchemy import delete
    from backend.app.database.connection import init_db, close_db, AsyncSessionLocal
    from backend.app.database.seed import seed_data
    from backend.app.models.transaction import TransactionORM as Tx
    from backend.app.models.subscription import SubscriptionORM
    from backend.app.services.bank.bill_service import TZ
    await init_db()
    await seed_data()
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tx))  # exclusive NEW database only
        def add(key, amount, month, day, kind='purchase', cat='dining', merchant='演示餐厅', second=0):
            db.add(Tx(tx_id=key,account_id='A001',type=kind,category=cat,amount_fen=amount,
                      counterparty=merchant,note='虚构账单创新演示数据',status='completed',channel='app',
                      timestamp=datetime(2026,month,day,12,0,second,tzinfo=TZ).astimezone(timezone.utc)))
        for month in (7,8,9):
            add(f'DEMO-SALARY-{month}',1500000,month,25,'salary','salary','演示科技')
            for day in (2,5,8,10,13,16,20,24):
                add(f'DEMO-FOOD-{month}-{day}',5000,month,day)
            add(f'DEMO-MUSIC-{month}',1500,month,12,'subscription','subscription','网易云音乐')
        add('DEMO-FOOD-OCT1',10000,10,2)
        add('DEMO-FOOD-OCT2',10000,10,5)
        add('DEMO-FOOD-OCT3',10000,10,8)
        add('DEMO-FOOD-OCT4',10000,10,10)
        add('DEMO-LAPTOP',180000,10,11,cat='shopping',merchant='演示数码商城')
        add('TX20260912001',2000,10,12,'subscription','subscription','网易云音乐')
        add('DEMO-MUSIC-REPEAT',2000,10,12,'subscription','subscription','网易云音乐',second=30)
        # Stable seed ID prevents application startup from appending a new seed charge.
        add('TX20260918001',2500,9,18,'subscription','subscription','腾讯视频')
        music=await db.get(SubscriptionORM,'SUB001')
        music.amount_fen=2000
        music.next_charge_at=datetime(2026,10,20,4,tzinfo=timezone.utc)
        await db.commit()
    await close_db()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    target=args.output.resolve()
    try:
        with target.open('xb'): pass
    except FileExistsError:
        parser.error('目标已存在，请指定新的独立数据库')
    os.environ['DATABASE_URL']='sqlite+aiosqlite:///'+str(target)
    asyncio.run(create())
    print(f'已生成独立虚构演示库 {target}；登录张三查看2026-10。余额为演示初始现金，不与合成历史对账。')
