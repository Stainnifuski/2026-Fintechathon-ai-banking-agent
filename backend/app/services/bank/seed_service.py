"""4号模拟银行的演示数据初始化。"""

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select

from ...database.connection import AsyncSessionLocal
from ...models.account import AccountORM
from ...models.contact import ContactORM
from ...models.transaction import TransactionORM
from ...models.user import UserORM
from ...security.passwords import hash_password

logger = logging.getLogger(__name__)


async def seed_bank_data() -> None:
    """通过模拟银行边界初始化客户、账户、联系人和流水。"""
    async with AsyncSessionLocal() as session:
        if (await session.execute(select(UserORM).limit(1))).scalar_one_or_none():
            logger.info("种子数据已存在，跳过初始化")
            return

        logger.info("开始写入模拟银行种子数据...")
        session.add_all([
            UserORM(user_id="U001", name="张三", phone="13800000001",
                    id_card="310***********1234", password_hash=hash_password("password123"),
                    risk_level="balanced", status="active"),
            UserORM(user_id="U002", name="李四", phone="13900000002",
                    id_card="310***********5678", password_hash=hash_password("password123"),
                    risk_level="stable", status="active"),
        ])
        await session.flush()
        session.add_all([
            AccountORM(account_id="A001", user_id="U001", account_type="savings",
                       card_number="6222****8842", balance=50000.0, daily_transfer_used=0.0,
                       daily_transfer_limit=50000.0, status="active"),
            AccountORM(account_id="A002", user_id="U001", account_type="credit",
                       card_number="6225****3301", balance=20000.0, daily_transfer_used=0.0,
                       daily_transfer_limit=50000.0, status="active"),
            AccountORM(account_id="A003", user_id="U002", account_type="savings",
                       card_number="6222****7756", balance=30000.0, daily_transfer_used=0.0,
                       daily_transfer_limit=50000.0, status="active"),
        ])
        await session.flush()
        session.add_all([
            ContactORM(contact_id="C001", user_id="U001", name="李四", nickname="老李",
                       phone="13900000002", account_number="6222****7756", bank="中国工商银行",
                       transfer_count=15, last_transfer=date(2026, 9, 10)),
            ContactORM(contact_id="C002", user_id="U001", name="王五", nickname="老王",
                       phone="137****4321", account_number="6228****9903", bank="中国建设银行",
                       transfer_count=8, last_transfer=date(2026, 8, 25)),
            ContactORM(contact_id="C003", user_id="U001", name="赵六", nickname=None,
                       phone="136****6543", account_number="6217****5520", bank="中国农业银行",
                       transfer_count=3, last_transfer=date(2026, 7, 15)),
        ])

        now = datetime.now()
        specs = [
            ("TX20260901001", "A001", "salary", 15000.0, "XX科技有限公司", "salary", "9月工资", 15, "app"),
            ("TX20260905001", "A001", "transfer_out", 500.0, "李四", "transfer", "还上次吃饭的钱", 11, "agent"),
            ("TX20260908001", "A001", "purchase", 128.0, "美团外卖", "dining", None, 8, "app"),
            ("TX20260910001", "A001", "purchase", 299.0, "京东商城", "shopping", "买耳机", 6, "app"),
            ("TX20260912001", "A001", "subscription", 15.0, "网易云音乐", "subscription", "会员自动续费", 4, "app"),
            ("TX20260914001", "A002", "purchase", 66.0, "滴滴出行", "transport", None, 2, "pos"),
        ]
        session.add_all([
            TransactionORM(tx_id=tx_id, account_id=account_id, type=kind, amount=amount,
                           counterparty=counterparty, category=category, note=note,
                           timestamp=now - timedelta(days=days_ago), status="completed", channel=channel)
            for tx_id, account_id, kind, amount, counterparty, category, note, days_ago, channel in specs
        ])
        await session.commit()
        logger.info("模拟银行种子数据写入完成")
