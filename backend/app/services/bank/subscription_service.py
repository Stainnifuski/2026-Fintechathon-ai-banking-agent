"""模拟银行订阅查询、取消与演示扣费。"""

import calendar
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from ...database.connection import AsyncSessionLocal
from ...domain import fen_to_yuan
from ...models.account import AccountORM
from ...models.subscription import SubscriptionORM
from ...models.transaction import TransactionORM
from .transfer_service import BankError


ACTIVE = "ACTIVE"
CANCELLED = "CANCELLED"


def _next_month(value: datetime) -> datetime:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _view(subscription: SubscriptionORM, account: AccountORM) -> dict:
    return {
        "subscription_id": subscription.subscription_id,
        "debit_account_id": subscription.debit_account_id,
        "debit_account_label": account.card_number,
        "merchant_name": subscription.merchant_name,
        "amount": fen_to_yuan(subscription.amount_fen),
        "amount_fen": subscription.amount_fen,
        "currency": subscription.currency,
        "billing_cycle": subscription.billing_cycle,
        "next_charge_at": subscription.next_charge_at,
        "status": subscription.status,
        "state_version": subscription.state_version,
        "source_transaction_id": subscription.source_transaction_id,
        "cancelled_at": subscription.cancelled_at,
    }


async def list_subscriptions(*, user_id: str) -> list[dict]:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(SubscriptionORM, AccountORM)
                .join(AccountORM, AccountORM.account_id == SubscriptionORM.debit_account_id)
                .where(SubscriptionORM.user_id == user_id)
                .order_by(SubscriptionORM.created_at, SubscriptionORM.subscription_id)
            )
        ).all()
    return [_view(subscription, account) for subscription, account in rows]


async def cancel_subscription(
    *, subscription_id: str, user_id: str, state_version: int
) -> dict:
    async with AsyncSessionLocal() as session:
        subscription = (
            await session.execute(
                select(SubscriptionORM)
                .where(
                    SubscriptionORM.subscription_id == subscription_id,
                    SubscriptionORM.user_id == user_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if subscription is None:
            raise BankError("订阅不存在", "SUBSCRIPTION_NOT_FOUND", 404)

        if subscription.status == CANCELLED:
            account = await session.get(AccountORM, subscription.debit_account_id)
            return _view(subscription, account)
        if subscription.status != ACTIVE:
            raise BankError("当前订阅状态不能取消", "SUBSCRIPTION_NOT_ACTIVE", 409)
        if subscription.state_version != state_version:
            raise BankError("订阅状态已更新，请刷新后重试", "STALE_STATE_VERSION", 409)

        subscription.status = CANCELLED
        subscription.state_version += 1
        subscription.cancelled_at = datetime.now(timezone.utc)
        account = await session.get(AccountORM, subscription.debit_account_id)
        await session.commit()
        await session.refresh(subscription)
        return _view(subscription, account)


async def simulate_subscription_charge(
    *,
    subscription_id: str,
    user_id: str,
    state_version: int,
    idempotency_key: str,
) -> dict:
    """模拟一个月度扣费周期，并在同一银行事务内更新余额和流水。"""
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(SubscriptionORM, AccountORM)
                .join(AccountORM, AccountORM.account_id == SubscriptionORM.debit_account_id)
                .where(
                    SubscriptionORM.subscription_id == subscription_id,
                    SubscriptionORM.user_id == user_id,
                )
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            raise BankError("订阅不存在", "SUBSCRIPTION_NOT_FOUND", 404)
        subscription, account = row

        existing = (
            await session.execute(
                select(TransactionORM).where(
                    TransactionORM.operation_id == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if (
                existing.account_id != account.account_id
                or existing.type != "subscription"
                or existing.counterparty != subscription.merchant_name
            ):
                raise BankError("幂等编号已用于其他操作", "IDEMPOTENCY_CONFLICT", 409)
            return {
                "subscription": _view(subscription, account),
                "transaction_id": existing.tx_id,
                "charged_amount": existing.amount,
                "duplicate": True,
                "message": f"{subscription.merchant_name} 本次模拟扣费已处理",
            }

        if subscription.status != ACTIVE:
            raise BankError("已取消的订阅不会继续扣费", "SUBSCRIPTION_NOT_ACTIVE", 409)
        if subscription.state_version != state_version:
            raise BankError("订阅状态已更新，请刷新后重试", "STALE_STATE_VERSION", 409)
        if account.status != "active":
            raise BankError("扣费账户当前不可用", "ACCOUNT_INACTIVE", 403)
        if subscription.currency != "CNY" or subscription.amount_fen <= 0:
            raise BankError("订阅金额或币种无效", "INVALID_AMOUNT", 409)

        balance_fen = round(account.balance * 100)
        if balance_fen < subscription.amount_fen:
            raise BankError("扣费账户余额不足", "INSUFFICIENT_FUNDS", 409)

        now = datetime.now(timezone.utc)
        transaction = TransactionORM(
            tx_id=f"STX{uuid4().hex[:18].upper()}",
            operation_id=idempotency_key,
            account_id=account.account_id,
            type="subscription",
            amount=fen_to_yuan(subscription.amount_fen),
            counterparty=subscription.merchant_name,
            category="subscription",
            note="模拟自动续费",
            timestamp=now,
            status="completed",
            channel="app",
        )
        account.balance = fen_to_yuan(balance_fen - subscription.amount_fen)
        subscription.next_charge_at = _next_month(subscription.next_charge_at)
        subscription.state_version += 1
        session.add(transaction)
        await session.commit()
        await session.refresh(subscription)
        await session.refresh(account)
        return {
            "subscription": _view(subscription, account),
            "transaction_id": transaction.tx_id,
            "charged_amount": transaction.amount,
            "duplicate": False,
            "message": (
                f"已模拟扣除 {subscription.merchant_name} "
                f"¥{transaction.amount:.2f}，下次扣费日已更新"
            ),
        }
