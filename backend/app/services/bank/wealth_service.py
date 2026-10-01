"""4号理财事实服务；受理、占款、计价、到账与审计事务一致。"""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from uuid import uuid4

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.wealth import CALENDAR_VERSION, TZ, advance_business, pricing_date, purchase, redeem, units
from ...domain.wealth_command import command_digest
from ...integrations.wealth_authorization import (
    WealthPolicyError, aware, require_suitability, validate_bank_authorization,
)
from ...models.account import AccountORM
from ...models.user import UserORM
from ...models.transaction import TransactionORM
from ...models.wealth import (WealthCashHoldORM, WealthClockORM, WealthLotORM,
    WealthNavORM, WealthOrderORM, WealthProductORM, WealthProductVersionORM,
    WealthSettlementORM, WealthUnitHoldORM)
from ...schemas.wealth import InvestmentCommand
from .available_funds import available_cash, held_cash
from .audit_service import record_bank_event
from .transfer_service import BankError


def scaled(value: str) -> int:
    """精确份额转万分之一整数。"""
    return int(units(value, positive=False) * 10000)


def quantity(value: int) -> str:
    """数据库份额以精确字符串返回，不传浮点数。"""
    return format(Decimal(value) / 10000, ".4f")


def stable_id(prefix: str, value: str) -> str:
    """派生标识固定长度，最长原操作号不会超出旧流水主键。"""
    return prefix+hashlib.sha256(value.encode()).hexdigest()[:32-len(prefix)]


async def business_now(db: AsyncSession) -> datetime:
    """业务时钟不替代任何安全时钟。"""
    row = await db.get(WealthClockORM, "mock")
    return aware(row.business_at) if row else datetime.now(timezone.utc)


async def verify_authorization(db: AsyncSession, user_id: str, authorization_id: str, proof: str) -> dict:
    """银行验证可信验证码，与逐笔验证证据同事务提交。"""
    from ...integrations.wealth_authorization import owned_authorization
    from .data_service import verify_totp_for_operation
    if db.get_bind().dialect.name == "sqlite":
        await db.execute(update(UserORM).where(UserORM.user_id == user_id)
            .values(totp_last_timestep=UserORM.totp_last_timestep))
    else:
        await db.execute(select(UserORM.user_id).where(UserORM.user_id == user_id).with_for_update())
    await db.get(UserORM, user_id, populate_existing=True)
    row = await owned_authorization(db, user_id, authorization_id)
    if row.status != "AWAITING_MFA":
        raise WealthPolicyError("请先确认本人订单及披露", "CONFIRMATION_REQUIRED")
    from ...models.audit_log import AuditLogORM
    since = datetime.now(timezone.utc)-timedelta(minutes=10)
    failures = await db.scalar(select(func.count()).select_from(AuditLogORM).where(
        AuditLogORM.user_id == user_id, AuditLogORM.action == "investment_verification",
        AuditLogORM.result == "failed", AuditLogORM.timestamp >= since))
    if failures >= 5:
        raise BankError("验证失败次数过多，请10分钟后重新预览并验证", "VERIFICATION_RATE_LIMITED", 429)
    try:
        await verify_totp_for_operation(db, user_id, proof)
    except BankError as error:
        if error.code in {"TOTP_INVALID", "TOTP_REPLAY"}:
            db.add(AuditLogORM(audit_id=f"WVFAIL{uuid4().hex}", session_id=row.operation_id, user_id=user_id,
                intent="wealth_verification", action="investment_verification", permission_level="red",
                user_confirmed=True, mfa_method="totp", result="failed",
                params={"authorization_id": authorization_id, "code": error.code},
                decision_chain=["personal_confirmation_checked", "strong_verification_denied", "no_credential_stored"]))
            await db.commit()
        raise
    row.status = "VALID"
    row.verified_at = datetime.now(timezone.utc)
    record_bank_event(db, event_key=row.authorization_id, subject_user_id=row.user_id,
        action="investment_verification", result="success", facts={"authorization_id": row.authorization_id,
            "operation_id": row.operation_id, "digest": row.param_digest},
        decision_chain=["personal_confirmation_checked", "totp_replay_prevented", "bound_authorization_verified"])
    await db.commit()
    return {"authorization_id": row.authorization_id, "status": row.status}


async def advance_clock(db: AsyncSession, business_at: datetime) -> dict:
    """隔离管理员推进业务时间并留痕，不改变安全时钟。"""
    if business_at.tzinfo is None:
        raise BankError("业务时间必须带时区", "INVALID_TIME", 422)
    row = await db.get(WealthClockORM, "mock", with_for_update=True)
    if row and aware(row.business_at) == business_at:
        return {"business_at": business_at.isoformat()}
    if business_at < await business_now(db):
        raise BankError("模拟业务时间不能倒退", "INVALID_TIME", 409)
    if row is None:
        db.add(WealthClockORM(clock_id="mock", business_at=business_at, calendar_version=CALENDAR_VERSION))
    else:
        row.business_at = business_at
    record_bank_event(db, event_key=business_at.isoformat(), subject_user_id="mock-admin",
        action="investment_clock", result="success", facts={"business_at": business_at.isoformat()},
        decision_chain=["isolated_database_admin", "monotonic_business_clock", "security_clock_unchanged"])
    await db.commit()
    return {"business_at": business_at.isoformat()}


async def lock_accounts(db: AsyncSession, account_ids: list[str]) -> None:
    """所有理财写操作先按账户ID加锁，与转账顺序一致。"""
    for key in sorted(set(account_ids)):
        if db.get_bind().dialect.name == "sqlite":
            await db.execute(update(AccountORM).where(AccountORM.account_id == key)
                .values(balance_fen=AccountORM.balance_fen))
        else:
            await db.execute(select(AccountORM.account_id).where(AccountORM.account_id == key).with_for_update())


async def owned_account(db: AsyncSession, user_id: str, account_id: str) -> AccountORM:
    """读写均核对归属，写时另核对客户和账户状态。"""
    account = await db.get(AccountORM, account_id, populate_existing=True)
    if account is None or account.user_id != user_id:
        raise BankError("账户不存在", "ACCOUNT_NOT_FOUND", 404)
    user = await db.get(UserORM, user_id)
    if user is None or user.status != "active" or account.status != "active":
        raise BankError("模拟客户或账户不可用", "ACCOUNT_INACTIVE", 403)
    if account.account_type == "credit":
        raise BankError("不支持使用信用账户投资", "ACCOUNT_TYPE_UNSUPPORTED", 409)
    return account


async def product_version(db: AsyncSession, command: InvestmentCommand) -> WealthProductVersionORM:
    """不可缺失规则、披露或版本；暂停不能被客户端绕过。"""
    product = await db.get(WealthProductORM, command.product_id)
    row = await db.get(WealthProductVersionORM, f"{command.product_id}:{command.product_version}")
    if product is None or row is None:
        raise BankError("产品不存在", "PRODUCT_UNAVAILABLE", 404)
    if product.current_version != command.product_version or row.disclosure.get("version") != command.disclosure_version:
        raise BankError("产品规则或披露已改变，请重新预览", "RULE_VERSION_CHANGED", 409)
    required = {"currency", "min_amount_fen", "max_amount_fen", "purchase_fee_rate", "redeem_fee_bands",
        "holding_days", "confirm_business_days", "cash_business_days", "cutoff", "calendar_version",
        "cancel_before_pricing", "purchase_paused", "redeem_paused"}
    if not required <= row.rules.keys() or not {"nature", "manager", "seller", "risk", "fees", "liquidity",
        "holding_period_days", "pricing", "settlement", "cancellation", "limitations"} <= row.disclosure.keys():
        raise BankError("产品规则或披露不完整", "PRODUCT_UNAVAILABLE", 409)
    if row.rules["calendar_version"] != CALENDAR_VERSION:
        raise BankError("不支持此交易日历版本", "PRODUCT_UNAVAILABLE", 409)
    if command.direction == "purchase" and (product.status != "on_sale" or row.rules["purchase_paused"]):
        raise BankError("产品当前不可申购", "PRODUCT_UNAVAILABLE", 409)
    if command.direction == "redeem" and row.rules["redeem_paused"]:
        raise BankError("产品暂停赎回", "PRODUCT_UNAVAILABLE", 409)
    return row


async def latest_nav(db: AsyncSession, version_id: str, at: datetime) -> WealthNavORM | None:
    """仅读取业务时点以前净值，禁止未来数据。"""
    return (await db.scalars(select(WealthNavORM).where(WealthNavORM.version_id == version_id,
        WealthNavORM.nav_date <= at.astimezone(TZ).date()).order_by(WealthNavORM.nav_date.desc()))).first()


async def products(db: AsyncSession) -> list[dict]:
    """客观产品查询；未评估也可查询，不伪造缺失数据。"""
    now = await business_now(db)
    result = []
    for product in (await db.scalars(select(WealthProductORM).order_by(WealthProductORM.product_id))).all():
        row = await db.get(WealthProductVersionORM, f"{product.product_id}:{product.current_version}")
        if row is None:
            continue
        price = await latest_nav(db, row.version_id, now)
        result.append({"product_id": product.product_id, "version": row.version, "name": row.name,
            "kind": row.kind, "category": row.category, "product_risk_level": row.risk_level,
            "status": product.status, "rules": row.rules, "disclosure": row.disclosure,
            "nav": price.price if price else None, "nav_date": price.nav_date.isoformat() if price else None,
            "notice": "比赛模拟环境／模拟资金；历史净值不代表未来收益"})
    return result


async def eligible_lots(db: AsyncSession, user_id: str, command: InvestmentCommand,
                        at: datetime) -> list[tuple[WealthLotORM, int]]:
    """FIFO只选择已解锁且未占用份额；不偷偷改变全部赎回要求。"""
    rows = (await db.scalars(select(WealthLotORM).join(WealthProductVersionORM,
        WealthProductVersionORM.version_id == WealthLotORM.version_id).where(
        WealthLotORM.user_id == user_id, WealthLotORM.account_id == command.account_id,
        WealthProductVersionORM.product_id == command.product_id, WealthLotORM.units_scaled > 0)
        .order_by(WealthLotORM.acquired_date, WealthLotORM.lot_id))).all()
    result = []
    for lot in rows:
        held = int(await db.scalar(select(func.coalesce(func.sum(WealthUnitHoldORM.units_scaled), 0)).where(
            WealthUnitHoldORM.lot_id == lot.lot_id, WealthUnitHoldORM.status == "HELD")) or 0)
        if lot.unlock_date <= at.astimezone(TZ).date() and lot.units_scaled > held:
            result.append((lot, lot.units_scaled - held))
    return result


def redemption_rate(rules: dict, days: int) -> str:
    """按本订单固定版本费率及每批持有日数计算。"""
    for band in rules["redeem_fee_bands"]:
        if days < band["days_lt"]:
            return band["rate"]
    raise BankError("赎回费率缺失", "PRODUCT_UNAVAILABLE", 409)


async def precheck(db: AsyncSession, user_id: str, command: InvestmentCommand) -> dict:
    """预览和受理共用服务端校验，预览不占款。"""
    account = await owned_account(db, user_id, command.account_id)
    row = await product_version(db, command)
    now = await business_now(db)
    estimate = None
    suitability = None
    price = await latest_nav(db, row.version_id, now)
    if command.direction == "purchase":
        suitability = await require_suitability(db, user_id, row.risk_level)
        if not row.rules["min_amount_fen"] <= command.amount_fen <= row.rules["max_amount_fen"]:
            raise BankError("金额不符合产品申购限制", "INVALID_AMOUNT", 422)
        if await available_cash(db, account.account_id, account.balance_fen) < command.amount_fen:
            raise BankError("可用现金不足（在途赎回不可使用）", "INSUFFICIENT_AVAILABLE_FUNDS", 409)
        from ...integrations.wealth_authorization import require_payment_capacity
        await require_payment_capacity(db, user_id,
            await available_cash(db, account.account_id, account.balance_fen), command.amount_fen)
        if price:
            estimate = asdict(purchase(command.amount_fen, price.price, row.rules["purchase_fee_rate"]))
    else:
        lots = await eligible_lots(db, user_id, command, now)
        if sum(free for _, free in lots) < scaled(command.units):
            raise BankError("可赎回份额不足或批次仍在持有期", "INSUFFICIENT_REDEEMABLE_UNITS", 409)
        if price:
            remaining = scaled(command.units)
            estimates = []
            for lot, free in lots:
                take = min(remaining, free)
                if take:
                    lot_version = await db.get(WealthProductVersionORM, lot.version_id)
                    estimates.append(asdict(redeem(quantity(take), price.price,
                        redemption_rate(lot_version.rules, (now.astimezone(TZ).date()-lot.acquired_date).days),
                        lot_units=quantity(lot.units_scaled), lot_cost_fen=lot.cost_fen)))
                    remaining -= take
            estimate = {"cash_fen": sum(item["cash_fen"] for item in estimates),
                        "fee_fen": sum(item["fee_fen"] for item in estimates), "lots": estimates}
    priced = pricing_date(now, row.rules["cutoff"])
    return {"command": command.model_dump(), "digest": command_digest(command),
        "product_name": row.name, "product_risk_level": row.risk_level, "operation_security_level": "RED",
        "suitability": {**suitability, "matched": True} if suitability else None,
        "disclosure": row.disclosure, "rules": row.rules, "estimate": estimate,
        "estimated_nav": price.price if price else None, "estimated_nav_date": price.nav_date.isoformat() if price else None,
        "pricing_date": priced.isoformat(), "confirm_date": advance_business(priced, row.rules["confirm_business_days"]).isoformat(),
        "cash_date": advance_business(priced, row.rules["cash_business_days"]).isoformat(),
        "notice": "比赛模拟环境／模拟资金；预览为估算，受理不等于结算成功"}


def order_view(row: WealthOrderORM) -> dict:
    """返回银行事实，不由解释层猜测状态。"""
    return {"operation_id": row.operation_id, "account_id": row.account_id, "version_id": row.version_id,
        "direction": row.direction, "amount_fen": row.amount_fen, "units": quantity(row.units_scaled),
        "digest": row.param_digest, "plan_id": row.plan_id, "plan_version": row.plan_version,
        "status": row.status, "accepted_at": aware(row.accepted_at).isoformat(),
        "pricing_date": row.pricing_date.isoformat(), "confirm_date": row.confirm_date.isoformat(),
        "cash_date": row.cash_date.isoformat(), "details": row.details,
        "notice": "比赛模拟环境／模拟资金"}


async def owned_order(db: AsyncSession, user_id: str, operation_id: str) -> WealthOrderORM:
    """按原编号查本人权威订单；不存在不能证明未受理。"""
    row = await db.get(WealthOrderORM, operation_id, populate_existing=True)
    if row is None or row.user_id != user_id:
        raise BankError("当前未查到本人订单，请保留原操作号核对", "NOT_FOUND", 404)
    return row


async def accept_many(db: AsyncSession, user_id: str,
                      submissions: list[tuple[InvestmentCommand, str]]) -> list[dict]:
    """单笔/纯申购组合共用原子受理，占款与消费授权同事务。"""
    if not 1 <= len(submissions) <= 5 or len({c.operation_id for c, _ in submissions}) != len(submissions):
        raise BankError("每批1至5笔且操作号不能重复", "INVALID_PLAN", 422)
    await lock_accounts(db, [c.account_id for c, _ in submissions])
    results = []
    for command, auth_id in submissions:
        existing = await db.get(WealthOrderORM, command.operation_id, populate_existing=True)
        if existing:
            if existing.user_id != user_id or existing.param_digest != command_digest(command):
                raise BankError("原操作号参数冲突", "IDEMPOTENCY_CONFLICT", 409)
            results.append(order_view(existing))
            continue
        preview = await precheck(db, user_id, command)
        authorization = await validate_bank_authorization(db, user_id, auth_id, command)
        now = await business_now(db)
        version = await product_version(db, command)
        priced = pricing_date(now, version.rules["cutoff"])
        row = WealthOrderORM(operation_id=command.operation_id, user_id=user_id,
            account_id=command.account_id, version_id=version.version_id, direction=command.direction,
            amount_fen=command.amount_fen, units_scaled=scaled(command.units),
            param_digest=command_digest(command), authorization_id=auth_id, plan_id=command.plan_id,
            plan_version=command.plan_version, status="ACCEPTED", accepted_at=now, pricing_date=priced,
            confirm_date=advance_business(priced, version.rules["confirm_business_days"]),
            cash_date=advance_business(priced, version.rules["cash_business_days"]),
            details={"preview": preview["estimate"], "estimated_nav": preview["estimated_nav"],
                     "calculation_version": "mock-units-v1", "calendar_version": CALENDAR_VERSION})
        db.add(row)
        await db.flush()
        if command.direction == "purchase":
            db.add(WealthCashHoldORM(operation_id=row.operation_id, account_id=row.account_id,
                                     amount_fen=row.amount_fen, status="HELD"))
        else:
            remaining = row.units_scaled
            for lot, free in await eligible_lots(db, user_id, command, now):
                take = min(remaining, free)
                if take:
                    db.add(WealthUnitHoldORM(hold_id=hashlib.sha256(f"{row.operation_id}:{lot.lot_id}".encode()).hexdigest(),
                        operation_id=row.operation_id, lot_id=lot.lot_id, units_scaled=take, status="HELD"))
                    remaining -= take
        authorization.status = "CONSUMED"
        record_bank_event(db, event_key=row.operation_id, subject_user_id=user_id,
            action="investment_accept", result="success",
            facts={"operation_id": row.operation_id, "authorization_id": auth_id,
                   "digest": row.param_digest, "version_id": row.version_id, "direction": row.direction},
            decision_chain=["subject_checked", "suitability_checked", "bound_confirmation_and_mfa_checked", "funds_or_units_reserved"])
        await db.flush()
        results.append(order_view(row))
    await db.commit()
    return results


async def cancel(db: AsyncSession, user_id: str, operation_id: str) -> dict:
    """仅计价前可撤单；账户锁排除并发结算，重复撤单返回事实。"""
    initial = await owned_order(db, user_id, operation_id)
    await lock_accounts(db, [initial.account_id])
    row = await owned_order(db, user_id, operation_id)
    if row.status == "CANCELLED":
        return order_view(row)
    rules = (await db.get(WealthProductVersionORM, row.version_id)).rules
    now = await business_now(db)
    deadline = datetime.combine(row.pricing_date, datetime.strptime(rules["cutoff"], "%H:%M").time(), TZ)
    if row.status not in {"ACCEPTED", "PENDING_PRICING"} or not rules["cancel_before_pricing"] or now >= deadline:
        raise BankError("已超过撤单窗口或订单已计价", "CANCELLATION_NOT_ALLOWED", 409)
    await db.execute(update(WealthCashHoldORM).where(WealthCashHoldORM.operation_id == operation_id,
        WealthCashHoldORM.status == "HELD").values(status="RELEASED"))
    await db.execute(update(WealthUnitHoldORM).where(WealthUnitHoldORM.operation_id == operation_id,
        WealthUnitHoldORM.status == "HELD").values(status="RELEASED"))
    row.status = "CANCELLED"
    record_bank_event(db, event_key=operation_id, subject_user_id=user_id, action="investment_cancel",
        result="cancelled", facts={"operation_id": operation_id}, decision_chain=["owner_checked", "window_checked", "holds_released"])
    await db.commit()
    return order_view(row)


async def settle(db: AsyncSession, operation_id: str) -> dict:
    """管理员结算：受理后无需重新授权，固定价格/唯一分录保证重试安全。"""
    initial = await db.get(WealthOrderORM, operation_id)
    if initial is None:
        raise BankError("订单不存在", "NOT_FOUND", 404)
    await lock_accounts(db, [initial.account_id])
    row = await db.get(WealthOrderORM, operation_id, populate_existing=True)
    if row.status in {"SETTLED", "CANCELLED", "REJECTED"}:
        return order_view(row)
    now = await business_now(db)
    day = now.astimezone(TZ).date()
    version = await db.get(WealthProductVersionORM, row.version_id)
    account = await db.get(AccountORM, row.account_id, populate_existing=True)
    if row.status != "PENDING_CASH":
        row.status = "PENDING_PRICING"
        price = await db.get(WealthNavORM, f"{row.version_id}:{row.pricing_date.isoformat()}")
        if day < row.confirm_date or price is None or price.nav_date > day:
            row.details = {**row.details, "waiting_reason": "NAV_UNAVAILABLE" if price is None else "CONFIRMATION_DATE_NOT_REACHED"}
            await db.commit()
            return order_view(row)
        if row.direction == "purchase":
            value = purchase(row.amount_fen, price.price, version.rules["purchase_fee_rate"])
            hold = await db.get(WealthCashHoldORM, row.operation_id)
            if hold is None or hold.status != "HELD" or account.balance_fen < hold.amount_fen:
                raise BankError("占款事实异常，需要核对", "BANK_LEDGER_INVALID", 503)
            account.balance_fen -= value.gross_fen - value.refund_fen
            hold.status = "CONSUMED"
            db.add(WealthLotORM(lot_id=stable_id("LOT", row.operation_id), operation_id=row.operation_id,
                user_id=row.user_id, account_id=row.account_id, version_id=row.version_id,
                units_scaled=scaled(value.units), cost_fen=value.gross_fen-value.refund_fen,
                acquired_date=row.confirm_date, unlock_date=row.confirm_date+timedelta(days=version.rules["holding_days"])))
            row.units_scaled = scaled(value.units)
            row.details = {**row.details, "actual": asdict(value), "actual_nav": price.price, "waiting_reason": None}
            row.status = "SETTLED"
            db.add(TransactionORM(tx_id=stable_id("WPT", row.operation_id), account_id=row.account_id,
                type="investment_purchase", amount_fen=value.gross_fen-value.refund_fen,
                counterparty=version.name, category="investment", note=None,
                timestamp=now, status="completed", channel="app"))
        else:
            holds = (await db.scalars(select(WealthUnitHoldORM).where(
                WealthUnitHoldORM.operation_id == row.operation_id, WealthUnitHoldORM.status == "HELD"))).all()
            values = []
            for hold in holds:
                lot = await db.get(WealthLotORM, hold.lot_id, populate_existing=True)
                lot_version = await db.get(WealthProductVersionORM, lot.version_id)
                value = redeem(quantity(hold.units_scaled), price.price,
                    redemption_rate(lot_version.rules, (row.pricing_date-lot.acquired_date).days),
                    lot_units=quantity(lot.units_scaled), lot_cost_fen=lot.cost_fen)
                lot.units_scaled -= hold.units_scaled
                lot.cost_fen -= value.cost_fen
                hold.status = "CONSUMED"
                values.append({"lot_id": lot.lot_id, "units": quantity(hold.units_scaled), **asdict(value)})
            if not holds:
                raise BankError("份额占用缺失，需要核对", "BANK_LEDGER_INVALID", 503)
            row.details = {**row.details, "actual_nav": price.price, "waiting_reason": None,
                "actual": {"lots": values, **{field: sum(item[field] for item in values)
                    for field in ("gross_fen", "fee_fen", "cash_fen", "cost_fen", "realized_pnl_fen")}}}
            row.status = "PENDING_CASH"
        event = WealthSettlementORM(event_id=f"{row.operation_id}:pricing", operation_id=row.operation_id,
            kind="pricing", data=row.details["actual"], created_at=now)
        db.add(event)
        record_bank_event(db, event_key=event.event_id, subject_user_id=row.user_id,
            action="investment_pricing", result="success", facts={"operation_id": row.operation_id,
                "version_id": row.version_id, "pricing_date": row.pricing_date.isoformat(), "actual": row.details["actual"]},
            decision_chain=["account_locked", "fixed_nav_checked", "hold_consumed", "unique_settlement"])
    if row.status == "PENDING_CASH" and day >= row.cash_date:
        cash = row.details["actual"]["cash_fen"]
        account.balance_fen += cash
        row.status = "SETTLED"
        event = WealthSettlementORM(event_id=f"{row.operation_id}:cash", operation_id=row.operation_id,
            kind="cash", data={"cash_fen": cash}, created_at=now)
        db.add(event)
        if cash > 0:
            db.add(TransactionORM(tx_id=stable_id("WRT", row.operation_id), account_id=row.account_id,
                type="investment_redemption", amount_fen=cash, counterparty=version.name,
                category="investment", note=None, timestamp=now, status="completed", channel="app"))
        record_bank_event(db, event_key=event.event_id, subject_user_id=row.user_id,
            action="investment_cash", result="success", facts={"operation_id": row.operation_id, "cash_fen": cash},
            decision_chain=["receivable_checked", "cash_date_reached", "unique_credit"])
    await db.commit()
    return order_view(row)


async def assets(db: AsyncSession, user_id: str) -> dict:
    """现金、在途应收、持仓分开估值，占款仅为现金子集不重复相加。"""
    now = await business_now(db)
    accounts = (await db.scalars(select(AccountORM).where(AccountORM.user_id == user_id))).all()
    cash = [{"account_id": a.account_id, "account_type": a.account_type,
             "balance_fen": a.balance_fen, "held_fen": await held_cash(db, a.account_id),
             "available_fen": await available_cash(db, a.account_id, a.balance_fen)} for a in accounts]
    lots = []
    for lot in (await db.scalars(select(WealthLotORM).where(WealthLotORM.user_id == user_id,
            WealthLotORM.units_scaled > 0).order_by(WealthLotORM.acquired_date, WealthLotORM.lot_id))).all():
        price = await latest_nav(db, lot.version_id, now)
        version = await db.get(WealthProductVersionORM, lot.version_id)
        held = int(await db.scalar(select(func.coalesce(func.sum(WealthUnitHoldORM.units_scaled), 0)).where(
            WealthUnitHoldORM.lot_id == lot.lot_id, WealthUnitHoldORM.status == "HELD")) or 0)
        value = int((Decimal(quantity(lot.units_scaled)) * Decimal(price.price) * 100).quantize(Decimal("1"))) if price else None
        lots.append({"lot_id": lot.lot_id, "product_id": version.product_id, "product_name": version.name,
            "account_id": lot.account_id, "version_id": lot.version_id, "category": version.category,
            "units": quantity(lot.units_scaled), "held_units": quantity(held),
            "redeemable_units": quantity(lot.units_scaled-held) if lot.unlock_date <= now.astimezone(TZ).date() else "0.0000",
            "locked_units": quantity(lot.units_scaled) if lot.unlock_date > now.astimezone(TZ).date() else "0.0000",
            "cost_fen": lot.cost_fen, "market_value_fen": value,
            "unrealized_pnl_fen": value-lot.cost_fen if value is not None else None,
            "nav": price.price if price else None, "nav_date": price.nav_date.isoformat() if price else None,
            "acquired_date": lot.acquired_date.isoformat(), "unlock_date": lot.unlock_date.isoformat()})
    orders = (await db.scalars(select(WealthOrderORM).where(WealthOrderORM.user_id == user_id)
                               .order_by(WealthOrderORM.accepted_at.desc()))).all()
    receivable = sum(row.details["actual"]["cash_fen"] for row in orders if row.status == "PENDING_CASH")
    realized = sum(row.details.get("actual", {}).get("realized_pnl_fen", 0) for row in orders if row.direction == "redeem")
    return {"cash": cash, "lots": lots, "orders": [order_view(row) for row in orders],
        "receivable_fen": receivable, "realized_pnl_fen": realized,
        "bank_assets_fen": sum(a["balance_fen"] for a in cash if a["account_type"] != "credit") + receivable
            + sum(lot["market_value_fen"] or 0 for lot in lots),
        "valuation_complete": all(lot["market_value_fen"] is not None for lot in lots),
        "business_at": now.isoformat(), "notice": "比赛模拟环境／模拟资金；外部资产不计入银行可交易资金"}
