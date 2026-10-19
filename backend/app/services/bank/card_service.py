"""模拟银行卡片查询、预检与受理；额度、状态和上限只来自本模块的银行事实。"""

import secrets
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain import fen_to_yuan
from ...domain.money import ledger_fen
from ...domain.time import BUSINESS_TIMEZONE
from ...models.account import AccountORM
from ...models.card import CardAuthorizationORM, CardORM
from ...models.transaction import TransactionORM
from .audit_service import record_bank_event
from .transfer_service import BankError, _business_day_start

ACTIVE, LOCKED, LOST, CANCELLED = "ACTIVE", "LOCKED", "LOST", "CANCELLED"
CARD_ACTIONS = (
    "card_apply_virtual", "card_set_transaction_limits", "card_adjust_credit_limit",
    "card_unlock", "card_report_lost", "card_lock", "card_cancel_virtual", "card_reissue",
    "card_set_channels",
)
# 新卡由银行签发（虚拟卡、补卡），以 source_operation_id 保证同一操作只发一张。
ISSUING_ACTIONS = ("card_apply_virtual", "card_reissue")

# 模拟银行虚拟借记卡产品规则；通过预览原样返回，调用方不得自行假设。
VIRTUAL_CARD_PRODUCT = {
    "single_txn_limit_fen": 500_000,
    "daily_txn_limit_fen": 2_000_000,
    "single_txn_cap_fen": 5_000_000,
    "daily_txn_cap_fen": 5_000_000,
}
MAX_VIRTUAL_CARDS_PER_ACCOUNT = 3
VIRTUAL_ELIGIBLE_ACCOUNT_TYPES = {"savings", "checking"}
VIRTUAL_VALID_YEARS, PHYSICAL_VALID_YEARS = 3, 5
CARD_NUMBER_PREFIX = {"virtual": "6259", "DEBIT": "6222", "CREDIT": "6225"}

STATUS_LABELS = {ACTIVE: "正常", LOCKED: "已锁定", LOST: "已挂失", CANCELLED: "已注销"}
USER_LOCK_REASON = "本人主动锁定"
CHANNEL_LABELS = {
    "online_enabled": "线上支付", "atm_enabled": "ATM取现",
    "overseas_enabled": "境外交易", "contactless_enabled": "免密/闪付",
}
# 用卡交易渠道 → 需要打开的开关；pos 刷卡始终可用。
CHANNEL_SWITCH = {"online": "online_enabled", "atm": "atm_enabled",
                  "overseas": "overseas_enabled", "contactless": "contactless_enabled"}
DECLINE_MESSAGES = {
    "CARD_LOCKED": "卡片已锁定，交易被拒绝",
    "CARD_LOST": "卡片已挂失，交易被拒绝",
    "CARD_CANCELLED": "卡片已注销，交易被拒绝",
    "CARD_EXPIRED": "卡片已过有效期，交易被拒绝",
    "CHANNEL_DISABLED": "该交易渠道已关闭，交易被拒绝",
    "SINGLE_LIMIT_EXCEEDED": "超出单笔交易限额，交易被拒绝",
    "DAILY_LIMIT_EXCEEDED": "超出单日交易限额，交易被拒绝",
    "INSUFFICIENT_FUNDS": "关联账户可用余额不足，交易被拒绝",
    "CREDIT_LIMIT_EXCEEDED": "信用卡可用额度不足，交易被拒绝",
    "ACCOUNT_INACTIVE": "关联账户不可用，交易被拒绝",
}


def card_view(card: CardORM, account: AccountORM) -> dict:
    used = card.used_credit_fen or 0
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
        "used_credit_fen": used,
        "available_credit_fen": (max(card.credit_limit_fen - used, 0)
                                 if card.credit_limit_fen is not None else None),
        "single_txn_limit_fen": card.single_txn_limit_fen,
        "daily_txn_limit_fen": card.daily_txn_limit_fen,
        "single_txn_cap_fen": card.single_txn_cap_fen,
        "daily_txn_cap_fen": card.daily_txn_cap_fen,
        "state_version": card.state_version,
        "lost_reported_at": card.lost_reported_at,
        "valid_thru": card.valid_thru,
        "online_enabled": card.online_enabled,
        "atm_enabled": card.atm_enabled,
        "overseas_enabled": card.overseas_enabled,
        "contactless_enabled": card.contactless_enabled,
        "replaced_by_card_id": card.replaced_by_card_id,
        "cancelled_at": card.cancelled_at,
    }


async def list_cards(session: AsyncSession, *, user_id: str) -> list[dict]:
    rows = (await session.execute(
        select(CardORM, AccountORM)
        .join(AccountORM, AccountORM.account_id == CardORM.account_id)
        .where(CardORM.user_id == user_id, AccountORM.user_id == user_id)
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


def _business_month(now: datetime) -> str:
    return now.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).strftime("%Y-%m")


def _valid_thru(now: datetime, *, virtual: bool) -> str:
    local = now.astimezone(ZoneInfo(BUSINESS_TIMEZONE))
    return f"{local.year + (VIRTUAL_VALID_YEARS if virtual else PHYSICAL_VALID_YEARS):04d}-{local.month:02d}"


async def _new_card_number(session: AsyncSession, prefix: str) -> str:
    """脱敏卡号只露末四位；避开已存在的卡号，避免按尾号查询时出现歧义。"""
    for _ in range(50):
        number = f"{prefix}****{secrets.randbelow(10_000):04d}"
        taken = await session.scalar(select(func.count()).select_from(CardORM)
                                     .where(CardORM.card_number == number))
        if not taken:
            return number
    raise BankError("卡号资源暂不可用，请稍后重试", "CARD_NUMBER_EXHAUSTED", 503)


async def _virtual_card_count(session: AsyncSession, account_id: str) -> int:
    return (await session.execute(
        select(func.count()).select_from(CardORM).where(
            CardORM.account_id == account_id,
            CardORM.is_virtual.is_(True),
            CardORM.status.not_in((LOST, CANCELLED)),
        )
    )).scalar_one()


async def _check(session: AsyncSession, *, user_id: str, action: str, card_id: str | None,
                 params: dict, lock: bool) -> tuple[CardORM | None, AccountORM, list[dict], bool]:
    """预览与受理共用同一套银行规则，受理时在行锁下重新判定。

    第四个返回值表示本次变更是否扩大风险敞口（调高额度、打开渠道），授权侧据此定级。
    """
    if action not in CARD_ACTIONS:
        raise BankError("不支持的卡片操作", "UNSUPPORTED_CARD_ACTION", 422)

    if action == "card_apply_virtual":
        account = await _owned_account(session, user_id, params.get("account_id"), lock=lock)
        if account.status != "active":
            raise BankError("关联账户当前不可用", "ACCOUNT_INACTIVE", 403)
        if account.account_type not in VIRTUAL_ELIGIBLE_ACCOUNT_TYPES:
            raise BankError("虚拟卡只能关联本人借记账户", "ACCOUNT_TYPE_NOT_ELIGIBLE", 409)
        if await _virtual_card_count(session, account.account_id) >= MAX_VIRTUAL_CARDS_PER_ACCOUNT:
            raise BankError(f"该账户最多持有{MAX_VIRTUAL_CARDS_PER_ACCOUNT}张未挂失、未注销的虚拟卡",
                            "VIRTUAL_CARD_LIMIT_REACHED", 409)
        product = VIRTUAL_CARD_PRODUCT
        return None, account, [
            _change("account_id", "关联账户", None, account.card_number),
            _change("single_txn_limit_fen", "单笔交易限额", None, product["single_txn_limit_fen"], "fen"),
            _change("daily_txn_limit_fen", "单日交易限额", None, product["daily_txn_limit_fen"], "fen"),
        ], True

    card, account = await _owned_card(session, user_id, card_id, lock=lock)
    if card.status == CANCELLED:
        raise BankError("卡片已注销，不能继续操作", "CARD_CANCELLED", 409)

    if action == "card_reissue":
        if card.status != LOST:
            raise BankError("只有已挂失的卡片可以补卡", "CARD_NOT_LOST", 409)
        if card.replaced_by_card_id is not None:
            raise BankError("该卡已补发新卡", "CARD_ALREADY_REISSUED", 409)
        if account.status != "active":
            raise BankError("关联账户当前不可用", "ACCOUNT_INACTIVE", 403)
        return card, account, [
            _change("replaced_by_card_id", "补发新卡", None, "银行签发新卡号"),
            _change("single_txn_limit_fen", "单笔交易限额", None, card.single_txn_limit_fen, "fen"),
            _change("daily_txn_limit_fen", "单日交易限额", None, card.daily_txn_limit_fen, "fen"),
        ], False

    if card.status == LOST:
        raise BankError("卡片已挂失，不能继续操作", "CARD_LOST", 409)

    if action == "card_report_lost":
        return card, account, [_change("status", "卡片状态", card.status, LOST)], False

    if action == "card_cancel_virtual":
        if not card.is_virtual:
            raise BankError("只有虚拟卡可以在线注销", "NOT_VIRTUAL_CARD", 409)
        return card, account, [_change("status", "卡片状态", card.status, CANCELLED)], False

    if action == "card_unlock":
        if card.status != LOCKED:
            raise BankError("卡片未处于锁定状态", "CARD_NOT_LOCKED", 409)
        return card, account, [_change("status", "卡片状态", LOCKED, ACTIVE)], True

    if card.status != ACTIVE:
        raise BankError("卡片已锁定，请先解锁", "CARD_NOT_ACTIVE", 409)

    if action == "card_lock":
        return card, account, [_change("status", "卡片状态", ACTIVE, LOCKED)], False

    if action == "card_set_channels":
        changes = [_change(name, label, getattr(card, name), params[name])
                   for name, label in CHANNEL_LABELS.items() if getattr(card, name) != params[name]]
        if not changes:
            raise BankError("渠道开关与当前设置相同", "CHANNELS_UNCHANGED", 422)
        return card, account, changes, any(change["after"] for change in changes)

    if action == "card_adjust_credit_limit":
        if card.card_type != "CREDIT" or card.credit_limit_cap_fen is None:
            raise BankError("只有信用卡可以调整信用额度", "NOT_CREDIT_CARD", 409)
        target = params["credit_limit_fen"]
        if not 0 < target <= card.credit_limit_cap_fen:
            raise BankError(f"信用额度须在0至{fen_to_yuan(card.credit_limit_cap_fen):.2f}元之间",
                            "CREDIT_LIMIT_OUT_OF_RANGE", 422)
        if target == card.credit_limit_fen:
            raise BankError("新额度与当前额度相同", "CREDIT_LIMIT_UNCHANGED", 422)
        if target < (card.used_credit_fen or 0):
            raise BankError(f"新额度不能低于已用额度{fen_to_yuan(card.used_credit_fen):.2f}元",
                            "CREDIT_LIMIT_BELOW_USED", 422)
        return card, account, [
            _change("credit_limit_fen", "信用额度", card.credit_limit_fen, target, "fen")
        ], target > (card.credit_limit_fen or 0)

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
    ], single > card.single_txn_limit_fen or daily > card.daily_txn_limit_fen


async def preview_card_operation(session: AsyncSession, *, user_id: str, action: str,
                                 card_id: str | None, params: dict) -> dict:
    """只读预检；返回银行当前值与变更后值，供本人核对。"""
    card, account, changes, raises_risk = await _check(
        session, user_id=user_id, action=action, card_id=card_id, params=params, lock=False)
    return {
        "card_id": card.card_id if card else None,
        "card_state_version": card.state_version if card else None,
        "card": card_view(card, account) if card else None,
        "changes": changes,
        "raises_risk": raises_risk,
    }


async def execute_card_operation(session: AsyncSession, *, user_id: str, operation_id: str,
                                 action: str, card_id: str | None, card_state_version: int | None,
                                 params: dict) -> dict:
    """在调用方事务内受理已授权操作；不提交，由调用方与授权消费一起提交。"""
    if action in ISSUING_ACTIONS:
        issued = (await session.execute(
            select(CardORM).where(CardORM.source_operation_id == operation_id)
        )).scalar_one_or_none()
        if issued is not None:
            if issued.user_id != user_id:
                raise BankError("幂等编号已用于其他操作", "IDEMPOTENCY_CONFLICT", 409)
            return card_view(issued, await session.get(AccountORM, issued.account_id))

    card, account, changes, _ = await _check(
        session, user_id=user_id, action=action, card_id=card_id, params=params, lock=True)
    if card is not None and card.state_version != card_state_version:
        raise BankError("卡片状态已更新，请重新预览", "STALE_STATE_VERSION", 409)

    now = datetime.now(timezone.utc)
    result_card = card
    if action == "card_apply_virtual":
        product = VIRTUAL_CARD_PRODUCT
        result_card = CardORM(
            card_id=f"CARD{uuid4().hex[:12].upper()}", user_id=user_id, account_id=account.account_id,
            card_type="DEBIT", is_virtual=True,
            card_number=await _new_card_number(session, CARD_NUMBER_PREFIX["virtual"]), status=ACTIVE,
            single_txn_limit_fen=product["single_txn_limit_fen"],
            daily_txn_limit_fen=product["daily_txn_limit_fen"],
            single_txn_cap_fen=product["single_txn_cap_fen"],
            daily_txn_cap_fen=product["daily_txn_cap_fen"],
            valid_thru=_valid_thru(now, virtual=True),
            state_version=1, source_operation_id=operation_id,
        )
        session.add(result_card)
    elif action == "card_reissue":
        # 新卡继承限额、上限、渠道与信用额度；信用卡欠款随卡转移，旧卡保持挂失终态。
        prefix = CARD_NUMBER_PREFIX["virtual" if card.is_virtual else card.card_type]
        result_card = CardORM(
            card_id=f"CARD{uuid4().hex[:12].upper()}", user_id=user_id, account_id=card.account_id,
            card_type=card.card_type, is_virtual=card.is_virtual,
            card_number=await _new_card_number(session, prefix), status=ACTIVE,
            credit_limit_fen=card.credit_limit_fen, credit_limit_cap_fen=card.credit_limit_cap_fen,
            used_credit_fen=card.used_credit_fen or 0,
            single_txn_limit_fen=card.single_txn_limit_fen, daily_txn_limit_fen=card.daily_txn_limit_fen,
            single_txn_cap_fen=card.single_txn_cap_fen, daily_txn_cap_fen=card.daily_txn_cap_fen,
            online_enabled=card.online_enabled, atm_enabled=card.atm_enabled,
            overseas_enabled=card.overseas_enabled, contactless_enabled=card.contactless_enabled,
            valid_thru=_valid_thru(now, virtual=card.is_virtual),
            state_version=1, source_operation_id=operation_id,
        )
        session.add(result_card)
        card.replaced_by_card_id, card.used_credit_fen = result_card.card_id, 0
    elif action == "card_set_transaction_limits":
        card.single_txn_limit_fen = params["single_txn_limit_fen"]
        card.daily_txn_limit_fen = params["daily_txn_limit_fen"]
    elif action == "card_adjust_credit_limit":
        card.credit_limit_fen = params["credit_limit_fen"]
    elif action == "card_set_channels":
        for name in CHANNEL_LABELS:
            setattr(card, name, params[name])
    elif action == "card_lock":
        card.status, card.lock_reason = LOCKED, USER_LOCK_REASON
    elif action == "card_unlock":
        card.status, card.lock_reason = ACTIVE, None
    elif action == "card_report_lost":
        card.status, card.lost_reported_at = LOST, now
    elif action == "card_cancel_virtual":
        card.status, card.cancelled_at = CANCELLED, now
    if card is not None:
        card.state_version += 1

    record_bank_event(session, event_key=operation_id, subject_user_id=user_id,
        action=action, result="success",
        facts={"operation_id": operation_id, "card_id": result_card.card_id,
               "source_card_id": card.card_id if card is not None and card is not result_card else None,
               "state_version": result_card.state_version,
               "changes": [{k: c[k] for k in ("field", "before", "after")} for c in changes
                           if c["field"] != "account_id"]},
        decision_chain=["ownership_checked", "card_rules_checked", "version_checked", f"{action}_committed"])
    await session.flush()
    return card_view(result_card, account)


# ──────────────────── 模拟用卡交易 ────────────────────


def authorization_view(row: CardAuthorizationORM) -> dict:
    return {
        "auth_id": row.auth_id, "operation_id": row.operation_id, "card_id": row.card_id,
        "amount_fen": row.amount_fen, "merchant": row.merchant, "channel": row.channel,
        "approved": row.approved, "decline_code": row.decline_code, "tx_id": row.tx_id,
        "authorized_at": row.authorized_at,
        "message": "交易已批准" if row.approved else DECLINE_MESSAGES.get(row.decline_code, "交易被拒绝"),
    }


async def list_card_authorizations(session: AsyncSession, *, user_id: str, card_id: str,
                                   limit: int = 20) -> list[dict]:
    await _owned_card(session, user_id, card_id, lock=False)
    rows = (await session.execute(
        select(CardAuthorizationORM)
        .where(CardAuthorizationORM.card_id == card_id, CardAuthorizationORM.user_id == user_id)
        .order_by(CardAuthorizationORM.authorized_at.desc(), CardAuthorizationORM.auth_id)
        .limit(limit)
    )).scalars().all()
    return [authorization_view(row) for row in rows]


async def _daily_card_used_fen(session: AsyncSession, card_id: str, now: datetime) -> int:
    return int(await session.scalar(
        select(func.coalesce(func.sum(CardAuthorizationORM.amount_fen), 0)).where(
            CardAuthorizationORM.card_id == card_id,
            CardAuthorizationORM.approved.is_(True),
            CardAuthorizationORM.authorized_at >= _business_day_start(now),
        )) or 0)


async def _decline_reason(session: AsyncSession, card: CardORM, account: AccountORM,
                          amount_fen: int, channel: str, now: datetime) -> str | None:
    """按银行授权顺序校验：卡状态 → 有效期 → 渠道 → 单笔/单日限额 → 资金或信用额度。"""
    if card.status != ACTIVE:
        return {LOCKED: "CARD_LOCKED", LOST: "CARD_LOST", CANCELLED: "CARD_CANCELLED"}[card.status]
    if card.valid_thru is not None and _business_month(now) > card.valid_thru:
        return "CARD_EXPIRED"
    switch = CHANNEL_SWITCH.get(channel)
    if switch is not None and not getattr(card, switch):
        return "CHANNEL_DISABLED"
    if amount_fen > card.single_txn_limit_fen:
        return "SINGLE_LIMIT_EXCEEDED"
    if await _daily_card_used_fen(session, card.card_id, now) + amount_fen > card.daily_txn_limit_fen:
        return "DAILY_LIMIT_EXCEEDED"
    if account.status != "active":
        return "ACCOUNT_INACTIVE"
    if card.card_type == "CREDIT":
        if (card.credit_limit_fen or 0) - (card.used_credit_fen or 0) < amount_fen:
            return "CREDIT_LIMIT_EXCEEDED"
        return None
    from .available_funds import available_cash
    from .wealth_reconciliation import require_reconciled
    await require_reconciled(session, account.account_id)
    try:
        balance_fen = ledger_fen(account.balance_fen)
    except ValueError as error:
        raise BankError("银行账户金额异常", "BANK_LEDGER_INVALID", 503) from error
    if await available_cash(session, account.account_id, balance_fen) < amount_fen:
        return "INSUFFICIENT_FUNDS"
    return None


async def simulate_card_purchase(session: AsyncSession, *, user_id: str, card_id: str, operation_id: str,
                                 amount_fen: int, merchant: str, channel: str) -> dict:
    """模拟商户发起的用卡交易；批准时扣款或占用信用额度并记流水，拒绝也留授权记录。不提交。"""
    existing = (await session.execute(
        select(CardAuthorizationORM).where(CardAuthorizationORM.operation_id == operation_id)
    )).scalar_one_or_none()
    if existing is not None:
        if (existing.user_id, existing.card_id, existing.amount_fen, existing.merchant, existing.channel) != (
                user_id, card_id, amount_fen, merchant, channel):
            raise BankError("幂等编号已用于其他交易", "IDEMPOTENCY_CONFLICT", 409)
        card, account = await _owned_card(session, user_id, card_id, lock=False)
        return {"authorization": authorization_view(existing), "card": card_view(card, account)}

    card, _ = await _owned_card(session, user_id, card_id, lock=True)
    account = await _owned_account(session, user_id, card.account_id, lock=True)
    now = datetime.now(timezone.utc)
    decline = await _decline_reason(session, card, account, amount_fen, channel, now)
    row = CardAuthorizationORM(
        auth_id=f"AUTH{uuid4().hex[:16].upper()}", operation_id=operation_id, user_id=user_id,
        card_id=card.card_id, amount_fen=amount_fen, merchant=merchant, channel=channel,
        approved=decline is None, decline_code=decline, authorized_at=now,
    )
    if decline is None:
        row.tx_id = f"CTX{uuid4().hex[:18].upper()}"
        session.add(TransactionORM(
            # 操作号只记在授权记录上，避免与转账等主流水的幂等号空间冲突。
            tx_id=row.tx_id, account_id=account.account_id,
            type="purchase", amount_fen=amount_fen, counterparty=merchant,
            category="other" if channel == "atm" else "shopping",
            note=f"{'ATM取现' if channel == 'atm' else '卡片消费'} · 尾号{card.card_number[-4:]}",
            timestamp=now, status="completed", channel="atm" if channel == "atm" else "pos",
        ))
        if card.card_type == "CREDIT":
            card.used_credit_fen = (card.used_credit_fen or 0) + amount_fen
        else:
            account.balance_fen = ledger_fen(account.balance_fen) - amount_fen
    session.add(row)
    record_bank_event(session, event_key=operation_id, subject_user_id=user_id,
        action="card_purchase", result="success" if decline is None else "blocked",
        facts={"operation_id": operation_id, "card_id": card.card_id, "amount_fen": amount_fen,
               "channel": channel, "decline_code": decline, "tx_id": row.tx_id},
        decision_chain=["ownership_checked", "card_status_checked", "channel_checked",
                        "txn_limits_checked", "funds_checked",
                        "purchase_approved" if decline is None else f"declined_{decline.lower()}"])
    await session.flush()
    if decline is None:
        from .bill_foundation import sync_users
        await sync_users(session, [user_id], now=now, changed_tx_ids=[row.tx_id])
    return {"authorization": authorization_view(row), "card": card_view(card, account)}
