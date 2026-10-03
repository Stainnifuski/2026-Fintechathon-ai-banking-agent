"""模拟现金管理快赎：不垫资、不排队，成功同事务立即到账。"""
import hashlib
from dataclasses import asdict
from datetime import datetime, time
from decimal import Decimal, ROUND_DOWN

from sqlalchemy.ext.asyncio import AsyncSession
from ...domain.time import BEIJING, utc_now
from ...domain.wealth import business_day, redeem
from ...models.transaction import TransactionORM
from ...models.wealth import WealthQuickQuotaORM, WealthSettlementORM, WealthUnitHoldORM, WealthLotORM
from .transfer_service import BankError
from .audit_service import record_bank_event


def window_open(contract: dict, now: datetime, calendar: str) -> bool:
    """开始含端点、结束不含端点；产品02另外校验模拟交易日。"""
    local = now.astimezone(BEIJING)
    return (time.fromisoformat(contract["start"]) <= local.time() < time.fromisoformat(contract["end"])
        and (contract["window"] != "trading_day" or business_day(local.date(), calendar)))


def quote_key(price) -> str:
    """固定净值版本和价格，原行同ID变价也导致重新确认。"""
    return hashlib.sha256((price.nav_id+":"+price.price).encode()).hexdigest()


def quota_key(user_id: str, product_id: str, scope: str, now: datetime) -> str:
    return ":".join([user_id, product_id, now.astimezone(BEIJING).date().isoformat(), scope])


async def preview(db: AsyncSession, user_id: str, command, version, context, now, price, lots) -> tuple[object, dict]:
    """只读预检；缺净值、关窗、额度不足都不静默改为普通赎回。"""
    from .wealth_service import scaled, quantity
    contract = version.rules.get("quick_redemption")
    if not contract:
        raise BankError("该产品不支持快赎，可明确选择普通赎回", "QUICK_UNSUPPORTED", 409)
    if not window_open(contract, now, version.rules["calendar_version"]):
        raise BankError("当前快赎窗口关闭，可重新选择普通赎回", "QUICK_WINDOW_CLOSED", 409)
    if price is None:
        raise BankError("缺少当前已发布模拟净值", "NAV_UNAVAILABLE", 409)
    published = datetime.combine(price.nav_date,
        time.fromisoformat(version.rules.get('nav_publication_time', '18:00')), tzinfo=BEIJING)
    if published > now:
        raise BankError('净值尚未发布，不可用于快赎', 'NAV_UNAVAILABLE', 409)
    scope = context.context_id if context else "ordinary"
    epoch = context.version if context else 1
    if command.context_id is not None and (command.context_id != scope or command.context_epoch != epoch):
        raise BankError("资金轮次已改变，请重新预览", "STALE_CONTEXT", 409)
    gross = int((Decimal(command.units) * Decimal(price.price) * 100).quantize(Decimal("1"), ROUND_DOWN))
    if gross < 1:
        raise BankError("快赎到账不足1分", "INVALID_AMOUNT", 422)
    key = quote_key(price)
    if command.nav_quote_id and (command.nav_quote_id != key or command.quoted_cash_fen != gross):
        raise BankError("净值或到账金额改变，请重新预览确认", "QUOTE_CHANGED", 409)
    quota = await db.get(WealthQuickQuotaORM, quota_key(user_id, version.product_id, scope, now))
    used = quota.paid_fen if quota else 0
    if used + gross > contract["daily_limit_fen"]:
        raise BankError("快赎自然日累计额度不足，可重新选择普通赎回", "QUICK_DAILY_LIMIT", 409)
    bound = command.model_copy(update={"nav_quote_id":key,"quoted_cash_fen":gross,"context_id":scope,"context_epoch":epoch})
    remaining = scaled(command.units)
    allocations = []
    for lot, free in lots:
        take = min(remaining, free)
        if take:
            value = redeem(quantity(take), price.price, "0", lot_units=quantity(lot.units_scaled), lot_cost_fen=lot.cost_fen)
            allocations.append({"lot_id":lot.lot_id,"units_scaled":take,**asdict(value)})
            remaining -= take
    if remaining:
        raise BankError("可赎回份额不足", "INSUFFICIENT_REDEEMABLE_UNITS", 409)
    # 单次总到账向下取整；分批尾差归入最后批，不能丢现金或超过报价。
    if allocations:
        allocations[-1]["cash_fen"] += gross - sum(a["cash_fen"] for a in allocations)
    return bound, {"cash_fen":gross,"fee_fen":0,"lots":allocations,
        "daily_used_fen":used,"daily_remaining_fen":contract["daily_limit_fen"]-used,
        "redemption_mode":"quick","nav_quote_id":key,"nav":price.price,"nav_date":price.nav_date.isoformat(),
        "nav_available_at":published.isoformat(),
        "contract":contract,"notice":"模拟快赎同事务立即到账；成功受理后不可撤，不保本。"}


async def complete(db: AsyncSession, row, account, command, version, now, estimate: dict) -> None:
    """调用方已锁用户及账户，授权/报价预检完毕；由批次统一commit。"""
    from .wealth_service import stable_id, quantity
    scope = command.context_id or "ordinary"
    key = quota_key(row.user_id, version.product_id, scope, now)
    quota = await db.get(WealthQuickQuotaORM, key)
    if quota is None:
        quota = WealthQuickQuotaORM(quota_id=key,user_id=row.user_id,product_id=version.product_id,
            business_date=now.astimezone(BEIJING).date(),scope_id=scope,paid_fen=0)
        db.add(quota)
    if quota.paid_fen + estimate["cash_fen"] > version.rules["quick_redemption"]["daily_limit_fen"]:
        raise BankError("快赎额度已变化，请核对", "QUICK_DAILY_LIMIT", 409)
    actual_lots = []
    for allocation in estimate["lots"]:
        lot = await db.get(WealthLotORM, allocation["lot_id"], populate_existing=True)
        take = allocation["units_scaled"]
        lot.units_scaled -= take; lot.cost_fen -= allocation["cost_fen"]
        actual_lots.append({**allocation,"units":quantity(take)})
    cash = estimate["cash_fen"]
    account.balance_fen += cash; quota.paid_fen += cash
    row.status = "SETTLED"
    row.details = {**row.details, "redemption_mode":"quick","waiting_reason":None,
        "actual_nav":estimate["nav"],"actual":{"cash_fen":cash,"fee_fen":0,"lots":actual_lots,
        "cost_fen":sum(a["cost_fen"] for a in actual_lots),"realized_pnl_fen":cash-sum(a["cost_fen"] for a in actual_lots)},
        "quick_contract":estimate["contract"],"business_at":now.isoformat(),"occurred_at":utc_now().isoformat()}
    db.add(WealthSettlementORM(event_id=row.operation_id+":quick",operation_id=row.operation_id,
        kind="quick_cash",data=row.details["actual"],created_at=utc_now()))
    db.add(TransactionORM(tx_id=stable_id("WRT",row.operation_id),account_id=row.account_id,
        type="investment_redemption",amount_fen=cash,counterparty=version.name,category="investment",
        note="模拟快速赎回立即到账",timestamp=utc_now(),status="completed",channel="app"))
    record_bank_event(db,event_key=row.operation_id,subject_user_id=row.user_id,action="investment_quick_cash",result="success",
        facts={"operation_id":row.operation_id,"cash_fen":cash,"scope":scope,"quota_id":key},
        decision_chain=["user_and_account_locked","bound_confirmation_checked","published_nav_and_quota_checked","atomic_units_cash_unique_ledger"])
