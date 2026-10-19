"""4号模拟银行的演示数据初始化。"""

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select, update

from ...database.connection import AsyncSessionLocal
from ...models.account import AccountORM
from ...models.transaction import TransactionORM
from ...models.subscription import SubscriptionORM
from ...models.card import CardORM
from ...models.user import UserORM
from ...models.user_phone_registry import UserPhoneRegistryORM
from ...models.bill_foundation import (
    BillRecurringOccurrenceORM, BillTransactionMetaORM, BillTransactionTagORM,
    BillTransferPairORM,
)
from ...models.bill_features import BillClassificationORM
from ...security.passwords import hash_password
from .phone import normalize_phone
from .transfer_service import BankError

logger = logging.getLogger(__name__)


LEGACY_SEED_TRANSACTION_IDS = (
    "TX20260901001", "TX20260905001", "TX20260908001", "TX20260910001",
    "TX20260912001", "TX20260914001", "TX20260918001",
)


async def remove_legacy_seed_transactions(session) -> None:
    """Remove only old fixed-ID sample rows and their dependent bill metadata."""
    seed_ids = (await session.scalars(select(TransactionORM.tx_id).where(
        TransactionORM.tx_id.in_(LEGACY_SEED_TRANSACTION_IDS),
        TransactionORM.operation_id.is_(None),
    ))).all()
    if not seed_ids:
        return
    pair_ids = (await session.scalars(select(BillTransferPairORM.pair_id).where(
        (BillTransferPairORM.debit_tx_id.in_(seed_ids)) |
        (BillTransferPairORM.credit_tx_id.in_(seed_ids)),
    ))).all()
    if pair_ids:
        await session.execute(update(BillTransactionMetaORM).where(
            BillTransactionMetaORM.transfer_pair_id.in_(pair_ids)).values(transfer_pair_id=None))
        await session.execute(delete(BillTransferPairORM).where(
            BillTransferPairORM.pair_id.in_(pair_ids)))
    await session.execute(update(BillTransactionMetaORM).where(
        BillTransactionMetaORM.original_tx_id.in_(seed_ids)).values(original_tx_id=None))
    await session.execute(delete(BillRecurringOccurrenceORM).where(
        BillRecurringOccurrenceORM.tx_id.in_(seed_ids)))
    await session.execute(delete(BillTransactionTagORM).where(
        BillTransactionTagORM.tx_id.in_(seed_ids)))
    await session.execute(delete(BillClassificationORM).where(
        BillClassificationORM.tx_id.in_(seed_ids)))
    await session.execute(delete(BillTransactionMetaORM).where(
        BillTransactionMetaORM.tx_id.in_(seed_ids)))
    await session.execute(update(SubscriptionORM).where(
        SubscriptionORM.source_transaction_id.in_(seed_ids)).values(source_transaction_id=None))
    await session.execute(delete(TransactionORM).where(TransactionORM.tx_id.in_(seed_ids)))
    logger.info("已移除 %d 条旧版默认演示流水", len(seed_ids))


async def seed_bank_data() -> None:
    """通过模拟银行边界初始化客户和账户；默认流水为空。"""
    async with AsyncSessionLocal() as session:
        logger.info("正在同步模拟银行种子客户...")

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

        await remove_legacy_seed_transactions(session)

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
                    source_transaction_id=None,
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
                    source_transaction_id=None,
                )
            )
        card_specs = [
            # 卡号沿用对应账户的脱敏卡号；信用卡锁定用于演示解锁后再调额，已用额度用于演示降额下限。
            ("CARD001", "U001", "A001", "DEBIT", "6222****8842", "ACTIVE", None, None, None, 0,
             1_000_000, 5_000_000, 5_000_000, 10_000_000, "2029-08"),
            ("CARD002", "U001", "A002", "CREDIT", "6225****3301", "LOCKED", "连续输错密码已被风控锁定",
             2_000_000, 5_000_000, 320_000, 500_000, 2_000_000, 2_000_000, 5_000_000, "2028-12"),
        ]
        for (card_id, user_id, account_id, card_type, card_number, card_status, lock_reason,
             credit_limit_fen, credit_cap_fen, used_credit_fen, single_fen, daily_fen, single_cap_fen,
             daily_cap_fen, valid_thru) in card_specs:
            if await session.get(CardORM, card_id) is None:
                session.add(CardORM(
                    card_id=card_id, user_id=user_id, account_id=account_id, card_type=card_type,
                    is_virtual=False, card_number=card_number, status=card_status,
                    lock_reason=lock_reason, credit_limit_fen=credit_limit_fen,
                    credit_limit_cap_fen=credit_cap_fen, single_txn_limit_fen=single_fen,
                    daily_txn_limit_fen=daily_fen, single_txn_cap_fen=single_cap_fen,
                    daily_txn_cap_fen=daily_cap_fen, used_credit_fen=used_credit_fen,
                    valid_thru=valid_thru, state_version=1,
                ))
        await session.commit()
        logger.info("模拟银行种子数据同步完成")
