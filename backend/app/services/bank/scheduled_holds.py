"""Cash locked for scheduled work.

A lock removes its remaining amount from available cash, so every debit path that uses
``available_cash`` refuses to spend it. Only operations or payment requests linked to the
lock may draw on it; ending the lock returns whatever remains.
"""
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import AccountORM
from ...models.scheduled_cash_hold import ScheduledCashHoldLinkORM, ScheduledCashHoldORM
from .transfer_service import BankError

HELD, CONSUMED, RELEASED = "HELD", "CONSUMED", "RELEASED"
PURPOSES = {"scheduled_transfer", "custom_task"}


def hold_view(hold: ScheduledCashHoldORM) -> dict:
    return {"hold_id": hold.hold_id, "account_id": hold.account_id, "amount_fen": hold.amount_fen,
            "used_fen": hold.used_fen, "remaining_fen": hold.amount_fen - hold.used_fen
            if hold.status == HELD else 0, "returned_fen": hold.amount_fen - hold.used_fen
            if hold.status != HELD else 0, "status": hold.status, "purpose": hold.purpose,
            "reference_id": hold.reference_id}


async def held_by_schedules(db: AsyncSession, account_id: str) -> int:
    return int(await db.scalar(select(func.coalesce(func.sum(
        ScheduledCashHoldORM.amount_fen - ScheduledCashHoldORM.used_fen), 0)).where(
        ScheduledCashHoldORM.account_id == account_id, ScheduledCashHoldORM.status == HELD)) or 0)


async def _lock_account(db: AsyncSession, account_id: str) -> None:
    # Same write-lock pattern as submit_transfer so a lock and a debit cannot interleave.
    if db.get_bind().dialect.name == "sqlite":
        await db.execute(update(AccountORM).where(AccountORM.account_id == account_id)
                         .values(balance_fen=AccountORM.balance_fen))
    else:
        await db.execute(select(AccountORM.account_id).where(AccountORM.account_id == account_id).with_for_update())


async def _owned(db: AsyncSession, hold_id: str, subject_user_id: str) -> ScheduledCashHoldORM:
    hold = await db.get(ScheduledCashHoldORM, hold_id, populate_existing=True)
    if hold is None or hold.user_id != subject_user_id:
        raise BankError("锁定资金记录不存在", "HOLD_NOT_FOUND", 404)
    return hold


async def place_hold(db: AsyncSession, *, hold_id: str, subject_user_id: str, account_id: str,
                     amount_fen: int, purpose: str, reference_id: str, link_keys: list[str] = ()) -> dict:
    """Lock cash in the owner's current account; fails when available cash is short."""
    if purpose not in PURPOSES or amount_fen <= 0:
        raise BankError("锁定金额必须大于0", "HOLD_INVALID", 422)
    await _lock_account(db, account_id)
    existing = await db.get(ScheduledCashHoldORM, hold_id, populate_existing=True)
    if existing is not None:
        if (existing.user_id, existing.account_id, existing.amount_fen, existing.reference_id) != (
                subject_user_id, account_id, amount_fen, reference_id):
            raise BankError("锁定编号参数冲突", "IDEMPOTENCY_CONFLICT", 409)
        await db.commit()
        return hold_view(existing)
    account = await db.get(AccountORM, account_id, populate_existing=True)
    if account is None or account.user_id != subject_user_id:
        raise BankError("付款账户不存在", "ACCOUNT_NOT_FOUND", 404)
    if account.status != "active" or account.account_type not in {"savings", "checking"}:
        raise BankError("只能锁定本人可用的活期账户资金", "ACCOUNT_NOT_ELIGIBLE", 409)
    from .available_funds import available_cash
    if await available_cash(db, account_id, account.balance_fen) < amount_fen:
        raise BankError("活期账户可自由支配的余额不足，无法锁定这笔资金", "INSUFFICIENT_FUNDS", 409)
    hold = ScheduledCashHoldORM(hold_id=hold_id, user_id=subject_user_id, account_id=account_id,
                                amount_fen=amount_fen, used_fen=0, status=HELD, purpose=purpose,
                                reference_id=reference_id)
    db.add(hold)
    await db.flush()
    for key in link_keys:
        db.add(ScheduledCashHoldLinkORM(link_key=key, hold_id=hold_id))
    await db.commit()
    return hold_view(hold)


async def link_hold(db: AsyncSession, *, hold_id: str, subject_user_id: str, link_key: str) -> None:
    hold = await _owned(db, hold_id, subject_user_id)
    if hold.status != HELD:
        return
    link = await db.get(ScheduledCashHoldLinkORM, link_key)
    if link is None:
        db.add(ScheduledCashHoldLinkORM(link_key=link_key, hold_id=hold_id))
    elif link.hold_id != hold_id:
        raise BankError("该付款已关联其他锁定资金", "HOLD_LINK_CONFLICT", 409)
    await db.commit()


async def release_holds(db: AsyncSession, *, subject_user_id: str, purpose: str, reference_id: str,
                        keep_hold_id: str | None = None) -> list[dict]:
    """End open locks for a task; the unused remainder returns to available cash."""
    holds = (await db.scalars(select(ScheduledCashHoldORM).where(
        ScheduledCashHoldORM.user_id == subject_user_id, ScheduledCashHoldORM.purpose == purpose,
        ScheduledCashHoldORM.reference_id == reference_id, ScheduledCashHoldORM.status == HELD))).all()
    released = []
    for hold in holds:
        if hold.hold_id == keep_hold_id:
            continue
        await _lock_account(db, hold.account_id)
        await db.refresh(hold)
        if hold.status == HELD:
            hold.status, hold.released_at = RELEASED, datetime.now(timezone.utc)
        released.append(hold_view(hold))
    await db.commit()
    return released


async def holds_for(db: AsyncSession, *, subject_user_id: str, purpose: str,
                    reference_ids: list[str]) -> dict[str, list[dict]]:
    if not reference_ids:
        return {}
    rows = (await db.scalars(select(ScheduledCashHoldORM).where(
        ScheduledCashHoldORM.user_id == subject_user_id, ScheduledCashHoldORM.purpose == purpose,
        ScheduledCashHoldORM.reference_id.in_(reference_ids)).order_by(ScheduledCashHoldORM.created_at))).all()
    result: dict[str, list[dict]] = {}
    for hold in rows:
        result.setdefault(hold.reference_id, []).append(hold_view(hold))
    return result


async def drawable_hold(db: AsyncSession, *, account_id: str, operation_id: str,
                        payment_request_id: str | None) -> ScheduledCashHoldORM | None:
    """The open lock this debit may spend; caller already holds the account lock."""
    keys = [f"operation:{operation_id}"] + ([f"payment_request:{payment_request_id}"] if payment_request_id else [])
    for key in keys:
        link = await db.get(ScheduledCashHoldLinkORM, key)
        if link is None:
            continue
        hold = await db.get(ScheduledCashHoldORM, link.hold_id, populate_existing=True)
        if hold is not None and hold.status == HELD and hold.account_id == account_id:
            return hold
    return None


def draw(hold: ScheduledCashHoldORM, amount_fen: int) -> int:
    """Spend from the lock; a scheduled transfer's lock ends with its single payment."""
    drawn = min(amount_fen, hold.amount_fen - hold.used_fen)
    hold.used_fen += drawn
    if hold.purpose == "scheduled_transfer":
        hold.status, hold.released_at = CONSUMED, datetime.now(timezone.utc)
    return drawn
