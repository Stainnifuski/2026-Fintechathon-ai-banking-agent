"""模拟银行卡片查询、预检与受理；额度、状态和上限只来自本模块的银行事实。"""

import secrets
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain import fen_to_yuan
from ...models.account import AccountORM
from ...models.card import CardORM
from .audit_service import record_bank_event
from .transfer_service import BankError

ACTIVE, LOCKED, LOST = "ACTIVE", "LOCKED", "LOST"
CARD_ACTIONS = (
    "card_apply_virtual", "card_set_transaction_limits", "card_adjust_credit_limit",
    "card_unlock", "card_report_lost",
)

# 模拟银行虚拟借记卡产品规则；通过预览原样返回，调用方不得自行假设。
VIRTUAL_CARD_PRODUCT = {
    "single_txn_limit_fen": 500_000,
    "daily_txn_limit_fen": 2_000_000,
    "single_txn_cap_fen": 5_000_000,
    "daily_txn_cap_fen": 5_000_000,
}
MAX_VIRTUAL_CARDS_PER_ACCOUNT = 3
VIRTUAL_ELIGIBLE_ACCOUNT_TYPES = {"savings", "checking"}

STATUS_LABELS = {ACTIVE: "正常", LOCKED: "已锁定", LOST: "已挂失"}


def card_view(card: CardORM, account: AccountORM) -> dict:
    return {
        "card_id": card.card_id,
        "account_id": card.account_id,
        "account_label": account.card_number,
        "card_type": card.card_type,
        "is_virtual": card.is_virtual,
        "card_number": card.card_number,
        "status": card.status,
        "lock_reason": card.lock_reason,
        "credit_limit_fen": card.credit_limit_fen,
        "credit_limit_cap_fen": card.credit_limit_cap_fen,
        "single_txn_limit_fen": card.single_txn_limit_fen,
        "daily_txn_limit_fen": card.daily_txn_limit_fen,
        "single_txn_cap_fen": card.single_txn_cap_fen,
        "daily_txn_cap_fen": card.daily_txn_cap_fen,
        "state_version": card.state_version,
        "lost_reported_at": card.lost_reported_at,
    }


async def list_cards(session: AsyncSession, *, user_id: str) -> list[dict]:
    rows = (await session.execute(
        select(CardORM, AccountORM)
        .join(AccountORM, AccountORM.account_id == CardORM.account_id)
        .where(CardORM.user_id == user_id)
        .order_by(CardORM.created_at, CardORM.card_id)
    )).all()
    return [card_view(card, account) for card, account in rows]


async def _owned_card(session: AsyncSession, user_id: str, card_id: str | None,
                      *, lock: bool) -> tuple[CardORM, AccountORM]:
    query = select(CardORM).where(CardORM.card_id == card_id, CardORM.user_id == user_id)
    card = (await session.execute(query.with_for_update() if lock else query)).scalar_one_or_none()
    if card is None:
        raise BankError("卡片不存在", "CARD_NOT_FOUND", 404)
    return card, await session.get(AccountORM, card.account_id)


async def _owned_account(session: AsyncSession, user_id: str, account_id: str | None,
                         *, lock: bool) -> AccountORM:
    query = select(AccountORM).where(AccountORM.account_id == account_id, AccountORM.user_id == user_id)
    account = (await session.execute(query.with_for_update() if lock else query)).scalar_one_or_none()
    if account is None:
        raise BankError("账户不存在", "ACCOUNT_NOT_FOUND", 404)
    return account


def _change(field: str, label: str, before, after, unit: str | None = None) -> dict:
    return {"field": field, "label": label, "before": before, "after": after, "unit": unit}


async def _check(session: AsyncSession, *, user_id: str, action: str, card_id: str | None,
                 params: dict, lock: bool) -> tuple[CardORM | None, AccountORM, list[dict]]:
    """预览与受理共用同一套银行规则，受理时在行锁下重新判定。"""
    if action not in CARD_ACTIONS:
        raise BankError("不支持的卡片操作", "UNSUPPORTED_CARD_ACTION", 422)

    if action == "card_apply_virtual":
        account = await _owned_account(session, user_id, params.get("account_id"), lock=lock)
        if account.status != "active":
            raise BankError("关联账户当前不可用", "ACCOUNT_INACTIVE", 403)
        if account.account_type not in VIRTUAL_ELIGIBLE_ACCOUNT_TYPES:
            raise BankError("虚拟卡只能关联本人借记账户", "ACCOUNT_TYPE_NOT_ELIGIBLE", 409)
        existing = (await session.execute(
            select(func.count()).select_from(CardORM).where(
                CardORM.account_id == account.account_id,
                CardORM.is_virtual.is_(True),
                CardORM.status != LOST,
            )
        )).scalar_one()
        if existing >= MAX_VIRTUAL_CARDS_PER_ACCOUNT:
            raise BankError(f"该账户最多持有{MAX_VIRTUAL_CARDS_PER_ACCOUNT}张未挂失的虚拟卡",
                            "VIRTUAL_CARD_LIMIT_REACHED", 409)
        product = VIRTUAL_CARD_PRODUCT
        return None, account, [
            _change("account_id", "关联账户", None, account.card_number),
            _change("single_txn_limit_fen", "单笔交易限额", None, product["single_txn_limit_fen"], "fen"),
            _change("daily_txn_limit_fen", "单日交易限额", None, product["daily_txn_limit_fen"], "fen"),
        ]

    card, account = await _owned_card(session, user_id, card_id, lock=lock)
    if card.status == LOST:
        raise BankError("卡片已挂失，不能继续操作", "CARD_LOST", 409)

    if action == "card_report_lost":
        return card, account, [_change("status", "卡片状态", card.status, LOST)]

    if action == "card_unlock":
        if card.status != LOCKED:
            raise BankError("卡片未处于锁定状态", "CARD_NOT_LOCKED", 409)
        return card, account, [_change("status", "卡片状态", LOCKED, ACTIVE)]

    if card.status != ACTIVE:
        raise BankError("卡片已锁定，请先解锁", "CARD_NOT_ACTIVE", 409)

    if action == "card_adjust_credit_limit":
        if card.card_type != "CREDIT" or card.credit_limit_cap_fen is None:
            raise BankError("只有信用卡可以调整信用额度", "NOT_CREDIT_CARD", 409)
        target = params["credit_limit_fen"]
        if not 0 < target <= card.credit_limit_cap_fen:
            raise BankError(f"信用额度须在0至{fen_to_yuan(card.credit_limit_cap_fen):.2f}元之间",
                            "CREDIT_LIMIT_OUT_OF_RANGE", 422)
        if target == card.credit_limit_fen:
            raise BankError("新额度与当前额度相同", "CREDIT_LIMIT_UNCHANGED", 422)
        return card, account, [
            _change("credit_limit_fen", "信用额度", card.credit_limit_fen, target, "fen")]

    single, daily = params["single_txn_limit_fen"], params["daily_txn_limit_fen"]
    if not 0 < single <= card.single_txn_cap_fen or not 0 < daily <= card.daily_txn_cap_fen:
        raise BankError(
            f"单笔限额上限{fen_to_yuan(card.single_txn_cap_fen):.2f}元，"
            f"单日限额上限{fen_to_yuan(card.daily_txn_cap_fen):.2f}元",
            "TXN_LIMIT_OUT_OF_RANGE", 422)
    if single > daily:
        raise BankError("单笔限额不能高于单日限额", "TXN_LIMIT_INCONSISTENT", 422)
    if (single, daily) == (card.single_txn_limit_fen, card.daily_txn_limit_fen):
        raise BankError("新限额与当前限额相同", "TXN_LIMIT_UNCHANGED", 422)
    return card, account, [
        _change("single_txn_limit_fen", "单笔交易限额", card.single_txn_limit_fen, single, "fen"),
        _change("daily_txn_limit_fen", "单日交易限额", card.daily_txn_limit_fen, daily, "fen"),
    ]


async def preview_card_operation(session: AsyncSession, *, user_id: str, action: str,
                                 card_id: str | None, params: dict) -> dict:
    """只读预检；返回银行当前值与变更后值，供本人核对。"""
    card, account, changes = await _check(
        session, user_id=user_id, action=action, card_id=card_id, params=params, lock=False)
    return {
        "card_id": card.card_id if card else None,
        "card_state_version": card.state_version if card else None,
        "card": card_view(card, account) if card else None,
        "changes": changes,
    }


async def execute_card_operation(session: AsyncSession, *, user_id: str, operation_id: str,
                                 action: str, card_id: str | None, card_state_version: int | None,
                                 params: dict) -> dict:
    """在调用方事务内受理已授权操作；不提交，由调用方与授权消费一起提交。"""
    if action == "card_apply_virtual":
        issued = (await session.execute(
            select(CardORM).where(CardORM.source_operation_id == operation_id)
        )).scalar_one_or_none()
        if issued is not None:
            if issued.user_id != user_id:
                raise BankError("幂等编号已用于其他操作", "IDEMPOTENCY_CONFLICT", 409)
            return card_view(issued, await session.get(AccountORM, issued.account_id))

    card, account, changes = await _check(
        session, user_id=user_id, action=action, card_id=card_id, params=params, lock=True)
    if card is not None and card.state_version != card_state_version:
        raise BankError("卡片状态已更新，请重新预览", "STALE_STATE_VERSION", 409)

    now = datetime.now(timezone.utc)
    if action == "card_apply_virtual":
        product = VIRTUAL_CARD_PRODUCT
        card = CardORM(
            card_id=f"CARD{uuid4().hex[:12].upper()}", user_id=user_id, account_id=account.account_id,
            card_type="DEBIT", is_virtual=True,
            card_number=f"6259****{secrets.randbelow(10_000):04d}", status=ACTIVE,
            single_txn_limit_fen=product["single_txn_limit_fen"],
            daily_txn_limit_fen=product["daily_txn_limit_fen"],
            single_txn_cap_fen=product["single_txn_cap_fen"],
            daily_txn_cap_fen=product["daily_txn_cap_fen"],
            state_version=1, source_operation_id=operation_id,
        )
        session.add(card)
    elif action == "card_set_transaction_limits":
        card.single_txn_limit_fen = params["single_txn_limit_fen"]
        card.daily_txn_limit_fen = params["daily_txn_limit_fen"]
    elif action == "card_adjust_credit_limit":
        card.credit_limit_fen = params["credit_limit_fen"]
    elif action == "card_unlock":
        card.status, card.lock_reason = ACTIVE, None
    elif action == "card_report_lost":
        card.status, card.lost_reported_at = LOST, now
    if action != "card_apply_virtual":
        card.state_version += 1

    record_bank_event(session, event_key=operation_id, subject_user_id=user_id,
        action=action, result="success",
        facts={"operation_id": operation_id, "card_id": card.card_id,
               "state_version": card.state_version,
               "changes": [{k: c[k] for k in ("field", "before", "after")} for c in changes
                           if c["field"] != "account_id"]},
        decision_chain=["ownership_checked", "card_rules_checked", "version_checked", f"{action}_committed"])
    await session.flush()
    return card_view(card, account)
