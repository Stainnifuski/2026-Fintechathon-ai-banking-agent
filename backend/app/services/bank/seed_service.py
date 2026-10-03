"""4号模拟银行的演示数据初始化。"""

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from ...database.connection import AsyncSessionLocal
from ...models.account import AccountORM
from ...models.contact import ContactORM
from ...models.transaction import TransactionORM
from ...models.subscription import SubscriptionORM
from ...models.card import CardORM
from ...models.user import UserORM
from ...models.user_phone_registry import UserPhoneRegistryORM
from ...security.passwords import hash_password
from .phone import normalize_phone
from .transfer_service import BankError

logger = logging.getLogger(__name__)


async def seed_bank_data() -> None:
    """通过模拟银行边界初始化客户、账户、联系人和流水。"""
    async with AsyncSessionLocal() as session:
        has_seed_data = (
            await session.execute(select(UserORM).limit(1))
        ).scalar_one_or_none() is not None
        logger.info("正在同步模拟银行种子客户与联系人...")

        user_specs = [
            ("U001", "张三", "13800000001", "balanced"),
            ("U002", "李四", "13800000002", "stable"),
            ("U003", "王五", "13800000003", "balanced"),
            ("U004", "赵六", "13800000004", "balanced"),
            ("U005", "孙七", "13800000005", "stable"),
            ("U006", "周八", "13800000006", "balanced"),
            ("U007", "花语鲜花店", "13800000007", "stable"),
            ("U008", "甜时光蛋糕店", "13800000008", "stable"),
        ]
        existing_users = (await session.execute(select(UserORM))).scalars().all()
        mapped_phones = {}
        for existing_user in existing_users:
            try:
                canonical = normalize_phone(existing_user.phone)
            except ValueError:
                continue
            owner = mapped_phones.setdefault(canonical, existing_user.user_id)
            if owner != existing_user.user_id:
                raise BankError("历史手机号存在等价冲突，种子初始化已停止", "PHONE_CONFLICT", 409)
        for user_id, name, phone, risk_level in user_specs:
            owner = mapped_phones.get(phone)
            if owner is not None and owner != user_id:
                raise BankError("演示手机号已被其他客户占用", "PHONE_CONFLICT", 409)
            user = await session.get(UserORM, user_id)
            if user is None:
                session.add(
                    UserORM(
                        user_id=user_id,
                        name=name,
                        phone=phone,
                        id_card=f"310***********{user_id[-3:]}0",
                        password_hash=hash_password("password123"),
                        risk_level=risk_level,
                        status="active",
                    )
                )
        await session.flush()
        for user_id, _, phone, _ in user_specs:
            registry = await session.get(UserPhoneRegistryORM, phone)
            if registry is None:
                session.add(UserPhoneRegistryORM(canonical_phone=phone, user_id=user_id))
            elif registry.user_id != user_id:
                raise BankError("演示手机号映射冲突", "PHONE_CONFLICT", 409)
        await session.flush()

        account_specs = [
            ("A001", "U001", "savings", "6222****8842", 50000.0),
            ("A002", "U001", "credit", "6225****3301", 20000.0),
            ("A003", "U002", "savings", "6222****7756", 30000.0),
            ("A004", "U003", "savings", "6222****7757", 30000.0),
            ("A005", "U004", "savings", "6222****7758", 30000.0),
            ("A006", "U005", "savings", "6222****7759", 30000.0),
            ("A007", "U006", "savings", "6222****7760", 30000.0),
            ("A008", "U007", "savings", "6222****7788", 10000.0),
            ("A009", "U008", "savings", "6222****7799", 10000.0),
        ]
        for account_id, user_id, kind, card_number, balance in account_specs:
            if await session.get(AccountORM, account_id) is None:
                session.add(
                    AccountORM(
                        account_id=account_id,
                        user_id=user_id,
                        account_type=kind,
                        card_number=card_number,
                        balance=balance,
                        daily_transfer_used=0.0,
                        daily_transfer_limit=50000.0,
                        status="active",
                    )
                )
        await session.flush()

        contact_specs = [
            ("C001", "李四", "老李", "13800000002", "6222****7756", 15),
            ("C002", "王五", "老王", "13800000003", "6222****7757", 8),
            ("C003", "赵六", None, "13800000004", "6222****7758", 3),
            ("C004", "孙七", None, "13800000005", "6222****7759", 0),
            ("C005", "周八", None, "13800000006", "6222****7760", 0),
        ]
        for contact_id, name, nickname, phone, account_number, transfer_count in contact_specs:
            contact = await session.get(ContactORM, contact_id)
            if contact is None:
                contact = ContactORM(contact_id=contact_id, user_id="U001")
                session.add(contact)
                contact.name = name
                contact.nickname = nickname
                contact.phone = phone
                contact.account_number = account_number
                contact.bank = "AI 模拟银行"
                contact.transfer_count = transfer_count
                contact.last_transfer = date(2026, 9, 10) if transfer_count else None

        now = datetime.now()
        if not has_seed_data:
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

        if await session.get(TransactionORM, "TX20260918001") is None:
            session.add(
                TransactionORM(
                    tx_id="TX20260918001",
                    account_id="A001",
                    type="subscription",
                    amount=25.0,
                    counterparty="腾讯视频",
                    category="subscription",
                    note="VIP 自动续费",
                    timestamp=now - timedelta(days=2),
                    status="completed",
                    channel="app",
                )
            )

        if await session.get(SubscriptionORM, "SUB001") is None:
            session.add(
                SubscriptionORM(
                    subscription_id="SUB001",
                    user_id="U001",
                    debit_account_id="A001",
                    merchant_name="网易云音乐",
                    amount_fen=1500,
                    currency="CNY",
                    billing_cycle="MONTHLY",
                    next_charge_at=datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc),
                    status="ACTIVE",
                    state_version=1,
                    source_transaction_id="TX20260912001",
                )
            )
        if await session.get(SubscriptionORM, "SUB002") is None:
            session.add(
                SubscriptionORM(
                    subscription_id="SUB002",
                    user_id="U001",
                    debit_account_id="A001",
                    merchant_name="腾讯视频",
                    amount_fen=2500,
                    currency="CNY",
                    billing_cycle="MONTHLY",
                    next_charge_at=datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc),
                    status="ACTIVE",
                    state_version=1,
                    source_transaction_id="TX20260918001",
                )
            )
        card_specs = [
            # 卡号沿用对应账户的脱敏卡号；信用卡锁定用于演示解锁后再调额。
            ("CARD001", "U001", "A001", "DEBIT", "6222****8842", "ACTIVE", None, None, None,
             1_000_000, 5_000_000, 5_000_000, 10_000_000),
            ("CARD002", "U001", "A002", "CREDIT", "6225****3301", "LOCKED", "连续输错密码已被风控锁定",
             2_000_000, 5_000_000, 500_000, 2_000_000, 2_000_000, 5_000_000),
        ]
        for (card_id, user_id, account_id, card_type, card_number, card_status, lock_reason,
             credit_limit_fen, credit_cap_fen, single_fen, daily_fen, single_cap_fen, daily_cap_fen) in card_specs:
            if await session.get(CardORM, card_id) is None:
                session.add(CardORM(
                    card_id=card_id, user_id=user_id, account_id=account_id, card_type=card_type,
                    is_virtual=False, card_number=card_number, status=card_status,
                    lock_reason=lock_reason, credit_limit_fen=credit_limit_fen,
                    credit_limit_cap_fen=credit_cap_fen, single_txn_limit_fen=single_fen,
                    daily_txn_limit_fen=daily_fen, single_txn_cap_fen=single_cap_fen,
                    daily_txn_cap_fen=daily_cap_fen, state_version=1,
                ))
        await session.commit()
        logger.info("模拟银行种子数据同步完成")
