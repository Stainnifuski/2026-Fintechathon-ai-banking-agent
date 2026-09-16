"""种子数据 - 演示用转账场景"""

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select

from .connection import AsyncSessionLocal
from ..models.user import UserORM
from ..models.account import AccountORM
from ..models.contact import ContactORM
from ..models.transaction import TransactionORM

logger = logging.getLogger(__name__)


async def seed_data() -> None:
    """初始化种子数据。

    包含：
    - 2 个用户：张三(U001)、李四(U002)
    - 3 个账户：张三储蓄(A001)、张三信用卡(A002)、李四储蓄(A003)
    - 3 个联系人：李四、王五、赵六
    - 若干历史交易记录
    """
    async with AsyncSessionLocal() as session:
        # 检查是否已有数据
        result = await session.execute(select(UserORM).limit(1))
        if result.scalar_one_or_none() is not None:
            logger.info("种子数据已存在，跳过初始化")
            return

        logger.info("开始写入种子数据...")

        # ──────────── 用户 ────────────

        user_zhangsan = UserORM(
            user_id="U001",
            name="张三",
            phone="138****5678",
            id_card="310***********1234",
            risk_level="balanced",
            status="active",
        )
        user_lisi = UserORM(
            user_id="U002",
            name="李四",
            phone="139****8765",
            id_card="310***********5678",
            risk_level="stable",
            status="active",
        )
        session.add_all([user_zhangsan, user_lisi])

        # ──────────── 账户 ────────────

        account_a001 = AccountORM(
            account_id="A001",
            user_id="U001",
            account_type="savings",
            card_number="6222****8842",
            balance=50000.00,
            daily_transfer_used=0.0,
            daily_transfer_limit=50000.0,
            status="active",
        )
        account_a002 = AccountORM(
            account_id="A002",
            user_id="U001",
            account_type="credit",
            card_number="6225****3301",
            balance=20000.00,
            daily_transfer_used=0.0,
            daily_transfer_limit=50000.0,
            status="active",
        )
        account_a003 = AccountORM(
            account_id="A003",
            user_id="U002",
            account_type="savings",
            card_number="6222****7756",
            balance=30000.00,
            daily_transfer_used=0.0,
            daily_transfer_limit=50000.0,
            status="active",
        )
        session.add_all([account_a001, account_a002, account_a003])

        # ──────────── 联系人（张三的） ────────────

        contact_lisi = ContactORM(
            contact_id="C001",
            user_id="U001",
            name="李四",
            nickname="老李",
            phone="139****8765",
            account_number="6222****7756",
            bank="中国工商银行",
            transfer_count=15,
            last_transfer=date(2026, 9, 10),
        )
        contact_wangwu = ContactORM(
            contact_id="C002",
            user_id="U001",
            name="王五",
            nickname="老王",
            phone="137****4321",
            account_number="6228****9903",
            bank="中国建设银行",
            transfer_count=8,
            last_transfer=date(2026, 8, 25),
        )
        contact_zhaoliu = ContactORM(
            contact_id="C003",
            user_id="U001",
            name="赵六",
            nickname=None,
            phone="136****6543",
            account_number="6217****5520",
            bank="中国农业银行",
            transfer_count=3,
            last_transfer=date(2026, 7, 15),
        )
        session.add_all([contact_lisi, contact_wangwu, contact_zhaoliu])

        # ──────────── 历史交易记录 ────────────

        now = datetime.now()

        transactions = [
            TransactionORM(
                tx_id="TX20260901001",
                account_id="A001",
                type="salary",
                amount=15000.00,
                counterparty="XX科技有限公司",
                category="salary",
                note="9月工资",
                timestamp=now - timedelta(days=15),
                status="completed",
                channel="app",
            ),
            TransactionORM(
                tx_id="TX20260905001",
                account_id="A001",
                type="transfer_out",
                amount=500.00,
                counterparty="李四",
                category="transfer",
                note="还上次吃饭的钱",
                timestamp=now - timedelta(days=11),
                status="completed",
                channel="agent",
            ),
            TransactionORM(
                tx_id="TX20260908001",
                account_id="A001",
                type="purchase",
                amount=128.00,
                counterparty="美团外卖",
                category="dining",
                note=None,
                timestamp=now - timedelta(days=8),
                status="completed",
                channel="app",
            ),
            TransactionORM(
                tx_id="TX20260910001",
                account_id="A001",
                type="purchase",
                amount=299.00,
                counterparty="京东商城",
                category="shopping",
                note="买耳机",
                timestamp=now - timedelta(days=6),
                status="completed",
                channel="app",
            ),
            TransactionORM(
                tx_id="TX20260912001",
                account_id="A001",
                type="subscription",
                amount=15.00,
                counterparty="网易云音乐",
                category="subscription",
                note="会员自动续费",
                timestamp=now - timedelta(days=4),
                status="completed",
                channel="app",
            ),
            TransactionORM(
                tx_id="TX20260914001",
                account_id="A002",
                type="purchase",
                amount=66.00,
                counterparty="滴滴出行",
                category="transport",
                note=None,
                timestamp=now - timedelta(days=2),
                status="completed",
                channel="pos",
            ),
        ]
        session.add_all(transactions)

        await session.commit()
        logger.info("种子数据写入完成：2个用户, 3个账户, 3个联系人, %d条交易记录", len(transactions))
