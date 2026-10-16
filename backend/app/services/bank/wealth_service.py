"""4号理财事实服务；受理、占款、计价、到账与审计事务一致。"""

from dataclasses import asdict
from ...domain.wealth_paper_policy import PRICING_MODES
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from uuid import uuid4

from sqlalchemy import select, update, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.wealth import CALENDAR_VERSION, TZ, advance_business, pricing_date, purchase, redeem, units
from ...domain.wealth_command import command_digest
from ...domain.wealth_limits import MAX_EXECUTION_ITEMS
from ...integrations.wealth_authorization import (
    WealthPolicyError, aware, require_suitability, validate_bank_authorization,
)
from ...models.account import AccountORM
from ...models.user import UserORM
from ...models.transaction import TransactionORM
from ...models.wealth import (WealthCashHoldORM, WealthClockORM, WealthLotORM,
    WealthNavORM, WealthOrderORM, WealthProductORM, WealthProductVersionORM,
    WealthSettlementORM, WealthUnitHoldORM, WealthVerificationORM)
from ...schemas.wealth import InvestmentCommand
from ...domain.time import utc_now, as_utc, utc_iso
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


async def business_now(db: AsyncSession, account_id: str | None = None) -> datetime:
    """业务时钟不替代任何安全时钟。"""
    if account_id:
        from ...models.wealth_research import WealthContextORM
        context = (await db.scalars(select(WealthContextORM).where(WealthContextORM.account_id == account_id))).one_or_none()
        if context and context.data.get("disposable") is True:
            value=datetime.fromisoformat(context.data['business_at'])
            if value.tzinfo is None:
                raise BankError('测试业务时间缺少时区，需按来源核对', 'INVALID_BUSINESS_TIME', 409)
            return value
    return utc_now()


async def verify_authorization(db: AsyncSession, user_id: str, authorization_id: str, proof: str,
                               *, method: str = "totp", challenge_id: str | None = None) -> dict:
    result = await verify_authorizations(db, user_id, [authorization_id], proof,
        method=method, challenge_id=challenge_id)
    return {**result, "authorization_id": authorization_id}


async def verify_authorizations(db: AsyncSession, user_id: str, authorization_ids: list[str], proof: str,
                                *, method: str = "totp", challenge_id: str | None = None,
                                plan_id: str | None = None, version: int | None = None) -> dict:
    """统一第二因素一次验证，逐笔证据同事务写入；方案必须完整且当前。"""
    from ...integrations import wealth_authorization as policy
    from ...integrations.wealth_verification import verification_reference
    from .data_service import verify_second_factor
    from ...models.audit_log import AuditLogORM
    if not 1 <= len(authorization_ids) <= MAX_EXECUTION_ITEMS or len(set(authorization_ids)) != len(authorization_ids):
        raise BankError("验证项不能重复或遗漏", "INVALID_PLAN", 422)
    await policy.lock_profile(db, user_id)
    if db.get_bind().dialect.name == "sqlite":
        await db.execute(update(UserORM).where(UserORM.user_id == user_id)
            .values(totp_last_timestep=UserORM.totp_last_timestep))
    else:
        await db.execute(select(UserORM.user_id).where(UserORM.user_id == user_id).with_for_update())
    await db.get(UserORM, user_id, populate_existing=True)
    rows = [await policy.owned_authorization(db, user_id, aid) for aid in authorization_ids]
    if plan_id:
        from ..orchestration.wealth_plan import confirmation_commands
        commands = await confirmation_commands(db, user_id, plan_id, version)
        if {r.operation_id for r in rows} != {c.operation_id for c in commands}:
            raise WealthPolicyError("必须验证整份当前方案", "STALE_PLAN")
    elif len(rows) != 1:
        raise WealthPolicyError("批量验证必须绑定方案", "INVALID_PLAN", 422)
    for row in rows:
        if row.status != "AWAITING_MFA" or not row.confirmed_at:
            raise WealthPolicyError("请先本人确认当前订单", "CONFIRMATION_REQUIRED")
    since = datetime.now(timezone.utc)-timedelta(minutes=10)
    failures = await db.scalar(select(func.count()).select_from(AuditLogORM).where(
        AuditLogORM.user_id == user_id, AuditLogORM.action == "investment_verification",
        AuditLogORM.result == "failed", AuditLogORM.timestamp >= since))
    if failures >= 5:
        raise BankError("验证失败次数过多，请10分钟后重新验证", "VERIFICATION_RATE_LIMITED", 429)
    reference = verification_reference(rows, grouped=bool(plan_id))
    try:
        await verify_second_factor(db, user_id, proof, method=method, challenge_id=challenge_id,
            purpose="wealth", reference_id=reference)
    except BankError as error:
        if error.code in {"TOTP_INVALID", "TOTP_REPLAY", "SMS_INVALID"}:
            db.add(AuditLogORM(audit_id=f"WVFAIL{uuid4().hex}", session_id=rows[0].operation_id, user_id=user_id,
                intent="wealth_verification", action="investment_verification", permission_level="red",
                user_confirmed=True, mfa_method=method, result="failed",
                params={"authorization_ids": authorization_ids, "code": error.code},
                decision_chain=["personal_confirmation_checked", "strong_verification_denied", "no_credential_stored"]))
            await db.commit()
        raise
    from ...security.sms import get_sms_provider
    simulated = method == "sms" and get_sms_provider().name == "mock"
    now = datetime.now(timezone.utc)
    for row in rows:
        from ..orchestration.wealth_task import require_write_control
        row.status, row.verified_at = "VALID", now
        await require_write_control(db, user_id, InvestmentCommand.model_validate(row.command), row)
        db.add(WealthVerificationORM(authorization_id=row.authorization_id, method=method.upper(),
            provider_version="unified-mfa-v1", simulated=simulated, replacement_required=False, verified_at=now))
        db.add(AuditLogORM(audit_id=hashlib.sha256(f"investment_verification:{row.authorization_id}".encode()).hexdigest(),
            session_id=row.operation_id, user_id=user_id, intent="wealth_verification", permission_level="red",
            action="investment_verification", result="success", user_confirmed=True, mfa_method=method,
            params={"authorization_id":row.authorization_id,"operation_id":row.operation_id,
                "digest":row.param_digest,"reference_id":reference,"simulated_delivery":simulated},
            decision_chain=["personal_confirmation_checked","unified_second_factor_verified","bound_authorization_verified"]))
    await db.commit()
    return {"status": "VALID", "authorization_ids": authorization_ids}


async def advance_clock(db: AsyncSession, business_at: datetime, user_id: str | None = None, context_id: str | None = None) -> dict:
    """隔离管理员推进业务时间并留痕，不改变安全时钟。"""
    if business_at.tzinfo is None:
        raise BankError("业务时间必须带时区", "INVALID_TIME", 422)
    if not user_id or not context_id:
        raise BankError('必须指定本人可处置轮次，禁止推进全局普通账户时间', 'EXPLICIT_CONTEXT_REQUIRED', 422)
    from .wealth_demo import context_owned
    from ...integrations.wealth_authorization import lock_profile
    await lock_profile(db, user_id)
    context = await context_owned(db, user_id, context_id)
    now = await business_now(db, context.account_id)
    if now == business_at:
        return {'business_at':business_at.isoformat(), 'context_id':context_id}
    if business_at < now:
        raise BankError("模拟业务时间不能倒退", "INVALID_TIME", 409)
    context.data = {**context.data, 'business_at':business_at.isoformat()}
    record_bank_event(db, event_key=context_id+business_at.isoformat(), subject_user_id=user_id,
        action="investment_clock", result="success", facts={"business_at": business_at.isoformat(), 'context_id':context_id},
        decision_chain=["isolated_database_admin", "monotonic_business_clock", "security_clock_unchanged"])
    await db.commit()
    return {"business_at": business_at.isoformat(), 'context_id':context_id}


async def lock_accounts(db: AsyncSession, account_ids: list[str]) -> None:
    """所有理财写操作先按账户ID加锁，与转账顺序一致。"""
    for key in sorted(set(account_ids)):
        if db.get_bind().dialect.name == "sqlite":
            await db.execute(update(AccountORM).where(AccountORM.account_id == key)
                .values(balance_fen=AccountORM.balance_fen, updated_at=AccountORM.updated_at))
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
    from .wealth_reconciliation import require_reconciled
    await require_reconciled(db, account_id)
    from .wealth_demo import require_active
    await require_active(db, account_id)
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
    from ...domain.wealth_calendar import CALENDAR_VERSION as CN_CALENDAR_VERSION
    if row.rules["calendar_version"] not in {CALENDAR_VERSION, "synthetic-weekdays-v1", CN_CALENDAR_VERSION}:
        raise BankError("不支持此交易日历版本", "PRODUCT_UNAVAILABLE", 409)
    if command.direction == "purchase" and (product.status != "on_sale" or row.rules["purchase_paused"]):
        raise BankError("产品当前不可申购", "PRODUCT_UNAVAILABLE", 409)
    if command.direction == "redeem" and row.rules["redeem_paused"]:
        raise BankError("产品暂停赎回", "PRODUCT_UNAVAILABLE", 409)
    return row


async def latest_nav(db: AsyncSession, version_id: str, at: datetime) -> WealthNavORM | None:
    """仅读取业务时点以前净值，禁止未来数据。"""
    day=at.astimezone(TZ).date()
    rows = (await db.scalars(select(WealthNavORM).where(WealthNavORM.version_id == version_id,
        WealthNavORM.nav_date <= day).order_by(WealthNavORM.nav_date.desc()))).all()
    return next((price for price in rows if nav_available_at(price)<=at),None)

def effective_disclosure(row):
    """Current versioned runtime policy, without editing historical bank records."""
    from ...domain.wealth_product_content import effective_disclosure as effective
    return effective(row.disclosure,row.rules)


def nav_available_at(price: WealthNavORM) -> datetime:
    """导入证据的可用瞬时；旧模拟种子缺字段按明确18点发布假设保守处理。"""
    return as_utc(price.available_at,legacy_utc=True) if price.available_at else datetime.combine(
        price.nav_date,datetime.strptime('18:00','%H:%M').time(),TZ)


async def products(db: AsyncSession, user_id: str | None = None,*,facts_only: bool=False,policy_references: bool=False,product_ids: list[str] | None=None) -> list[dict]:
    """客观产品查询；未评估也可查询，不伪造缺失数据。"""
    from .wealth_research import selected
    context = await selected(db, user_id) if user_id else None
    now = await business_now(db, context.account_id if context else None)
    result = []
    if product_ids == []:return result
    # Read current versions in one query; current state is never response-cached.
    compact=bool(facts_only and context and context.dataset_key.startswith('real-public-paper-v1:'))
    if compact:
        from types import SimpleNamespace
        v=WealthProductVersionORM;p=WealthProductORM
        from ...domain.wealth_product_content import static_disclosure
        # Both dialects decode JSON once. Preserve objects, missing keys and
        # nulls exactly; never stringify nested terms in the compact response.
        columns=[v.disclosure]
        query=select(p.product_id,p.status,v.version_id,v.version,v.name,v.kind,v.category,v.risk_level,v.rules,*columns).join(v,(v.product_id==p.product_id)&(v.version==p.current_version))
        if product_ids is not None:query=query.where(p.product_id.in_(product_ids))
        raw=(await db.execute(query.order_by(p.product_id))).all()
        def compact_disclosure(r):
            return static_disclosure(r[9],r[8])
        pairs=[(SimpleNamespace(product_id=r[0],status=r[1]),SimpleNamespace(version_id=r[2],version=r[3],name=r[4],kind=r[5],category=r[6],risk_level=r[7],rules=r[8],disclosure=compact_disclosure(r))) for r in raw]
    else:
        query=select(WealthProductORM, WealthProductVersionORM).join(
            WealthProductVersionORM, (WealthProductVersionORM.product_id == WealthProductORM.product_id)
            & (WealthProductVersionORM.version == WealthProductORM.current_version))
        if product_ids is not None:query=query.where(WealthProductORM.product_id.in_(product_ids))
        pairs=(await db.execute(query.order_by(WealthProductORM.product_id))).all()
    visible = []
    for product, row in pairs:
        dataset_key = f'{row.rules.get("dataset_id")}:{row.rules.get("dataset_version")}'
        from ...domain.wealth_bank_portfolio_bundle import authorized_dataset_keys
        if context and dataset_key not in authorized_dataset_keys(context):
            continue
        if not context and row.rules.get("dataset_id"):
            continue
        visible.append((product,row,dataset_key))
    versions=[row.version_id for _,row,_ in visible]
    prices={};records={};datasets={}
    if versions:
        # Explicit publication times and the legacy 18:00 Beijing assumption
        # have the same visibility semantics as latest_nav, including future rows.
        local=now.astimezone(TZ);legacy_day=local.date() if local.hour>=18 else local.date()-timedelta(days=1)
        if compact and product_ids is None:
            # Keep the full set in SQL, rather than expanding 14k parameters
            # and copying the same list into every price query.
            from ...domain.wealth_bank_portfolio_bundle import authorized_dataset_keys
            allowed=authorized_dataset_keys(context)
            version_scope=select(WealthProductVersionORM.version_id).join(WealthProductORM,
                (WealthProductORM.product_id==WealthProductVersionORM.product_id)&(WealthProductORM.current_version==WealthProductVersionORM.version)).where(
                or_(*[(WealthProductVersionORM.rules['dataset_id'].as_string()==k.split(':',1)[0]) &
                    (WealthProductVersionORM.rules['dataset_version'].as_string()==k.split(':',1)[1]) for k in allowed]))
        else:version_scope=versions
        eligible=select(WealthNavORM.version_id,func.max(WealthNavORM.nav_date).label('nav_date')).where(
            WealthNavORM.version_id.in_(version_scope),WealthNavORM.nav_date<=local.date(),
            or_(WealthNavORM.available_at<=now,(WealthNavORM.available_at.is_(None)) & (WealthNavORM.nav_date<=legacy_day)))
        eligible=eligible.group_by(WealthNavORM.version_id).subquery()
        navs=(await db.scalars(select(WealthNavORM).join(eligible,
            (WealthNavORM.version_id==eligible.c.version_id)&(WealthNavORM.nav_date==eligible.c.nav_date)))).all()
        prices={p.version_id:p for p in navs}
        from ...models.wealth_research import WealthResearchProductORM,WealthDatasetORM
        keys={key for _,row,key in visible if row.rules.get('dataset_id')}
        if keys:
            from types import SimpleNamespace
            if compact:
                query=select(WealthResearchProductORM.record_id,WealthResearchProductORM.data['share_class'].as_string()).where(WealthResearchProductORM.dataset_key.in_(keys))
                if product_ids is not None:query=query.where(WealthResearchProductORM.record_id.in_([key+':'+code for key in keys for code in product_ids]))
                rows=(await db.execute(query)).all()
                records={rid:share for rid,share in rows}
            else:
                rows=(await db.execute(select(WealthResearchProductORM.record_id,WealthResearchProductORM.data)
                    .where(WealthResearchProductORM.dataset_key.in_(keys)))).all()
                records={rid:SimpleNamespace(data=data) for rid,data in rows}
            ds=(await db.scalars(select(WealthDatasetORM).where(WealthDatasetORM.dataset_key.in_(keys)))).all()
            datasets={r.dataset_key:r for r in ds}
    for product,row,dataset_key in visible:
        price=prices.get(row.version_id)
        fixture=context.data.get('fixture',{}) if context else {}
        if fixture.get('no_match'): continue
        if fixture.get('source_unavailable'): price=None
        from .wealth_material import current_material
        material = {'share_class':records.get(dataset_key+':'+product.product_id),'source_product_code':product.product_id} if compact else await current_material(db, row,records=records,datasets=datasets)
        result.append({"product_id": product.product_id, "version": row.version, "name": row.name,
            "kind": row.kind, "category": row.category, "product_risk_level": row.risk_level,
            "product_risk_source":"project_policy" if row.rules.get('execution_policy') else "original_simulation",
            "project_policy_version":row.rules.get('execution_policy',{}).get('version'),
            "underlying_source_kind":"real" if row.rules.get('execution_policy') else "simulated",
            "execution_source_kind":"simulated",
            "status": product.status, "rules": {**row.rules,'purchase_paused':True} if fixture.get('purchase_paused') and fixture.get('target')==product.product_id else row.rules, "disclosure": effective_disclosure(row),
            "nav": price.price if price else None, "nav_date": price.nav_date.isoformat() if price else None,
            "price_source":"real_public_observed_NAV" if row.rules.get('pricing_mode') in PRICING_MODES else "original_simulated_NAV",
            "real_unit_nav":price.price if price else None,
            "available_at":utc_iso(nav_available_at(price)) if price else None,
            "fund_family_id": row.rules.get("fund_family_id", product.product_id),
            "identity": row.disclosure.get('identity', '虚构产品与模拟行情'),
            "dataset_key": dataset_key if context else "legacy-v1",
            "purchase_open": (now.astimezone(TZ).date()-date(2026,9,30)).days % row.rules.get('open_interval_days',1) == 0,
            "data_stale": bool(context and price and row.rules.get('pricing_mode') not in PRICING_MODES and now.astimezone(TZ).date()>advance_business(price.nav_date,3,row.rules['calendar_version'])),
            "notice": "比赛模拟环境／模拟资金；历史净值不代表未来收益", **material})
    if compact and policy_references:
        from ...domain.wealth_paper_policy import policy_sha
        for item in result:
            # Hash the actual current DB policy. A may dereference only an
            # exact match in its already validated immutable source index.
            policy=item['rules'].get('execution_policy')
            item['execution_policy_sha256']=policy_sha(policy)
            item['rules']={k:v for k,v in item['rules'].items() if k!='execution_policy'}
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
    from .wealth_research import selected
    from ...models.wealth_research import WealthContextORM
    context = (await db.scalars(select(WealthContextORM).where(WealthContextORM.account_id == account.account_id))).one_or_none()
    active = await selected(db, user_id)
    product_key = f'{row.rules.get("dataset_id")}:{row.rules.get("dataset_version")}'
    same_run = bool(context and active and context.user_id==active.user_id and
        ((context.data.get('account_scope') == 'shared_bank' and active.context_id == context.context_id) or
         (context.data.get('disposable') is True and active.data.get('disposable') is True and
          context.data.get('run_id',context.context_id)==active.data.get('run_id',active.context_id))))
    from ...domain.wealth_bank_portfolio_bundle import authorized_dataset_keys
    if ((context and (active is None or not same_run or product_key not in authorized_dataset_keys(context)))
            or (not context and (active is not None or row.rules.get("dataset_id")))):
        raise BankError("产品与当前资金上下文不一致，请重新预览", "DATASET_MISMATCH", 409)
    now = await business_now(db, command.account_id)
    fixture=context.data.get('fixture',{}) if context else {}
    if fixture.get('no_match') or fixture.get('purchase_paused') and fixture.get('target')==command.product_id and command.direction=='purchase':
        raise BankError('模拟场景产品当前不可申购','PRODUCT_UNAVAILABLE',409)
    if fixture.get('source_unavailable'):
        raise BankError('来源不可用且无有效估值，保留旧快照但不生成交易预览','DATA_STALE',409)
    estimate = None
    suitability = None
    price = await latest_nav(db, row.version_id, now)
    paper=row.rules.get('pricing_mode') in PRICING_MODES
    if paper:
        from ...domain.wealth_paper_policy import validate_policy,registered_policy_reasons
        category=row.category
        reasons=validate_policy(row.rules.get('execution_policy'),row.product_id,category,row.risk_level,now)
        reasons+=registered_policy_reasons(row.rules.get('execution_policy'),row.product_id)
        if reasons:raise BankError('公开资料或项目政策绑定不满足：'+','.join(reasons),'PAPER_POLICY_INVALID',409)
    if row.rules.get('dataset_id') and (price is None or (not paper and advance_business(price.nav_date,3,row.rules['calendar_version'])<now.astimezone(TZ).date())):
        raise BankError('净值缺失或过期，请等待可见有效数据','DATA_STALE',409)
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
    priced = pricing_date(now, row.rules["cutoff"], row.rules["calendar_version"])
    if command.direction == 'purchase' and row.rules.get('purchase_end_date') and priced > date.fromisoformat(row.rules['purchase_end_date']):
        raise BankError('模拟募集期已结束', 'PRODUCT_UNAVAILABLE', 409)
    if command.direction == 'redeem' and row.rules.get('closed_maturity_date') and priced < date.fromisoformat(row.rules['closed_maturity_date']):
        raise BankError('封闭模拟理财尚未到期', 'HOLDING_LOCKED', 409)
    interval = row.rules.get("open_interval_days", 1)
    if interval > 1 and (priced - datetime(2026, 9, 30).date()).days % interval != 0:
        raise BankError("当前不是模拟定期开放日", "NON_OPEN_DAY", 409)
    from .account_scope import is_shared
    instant = is_shared(context)
    # Cash-management products retain their quota/window contract; choosing
    # ordinary redemption must not create an unlimited immediate-cash bypass.
    if instant and command.direction == 'redeem' and row.rules.get('quick_redemption') and command.redemption_mode != 'quick':
        if command.command_version == 2:
            raise BankError('请重新预览现金管理赎回及额度', 'QUOTE_CHANGED', 409)
        command = command.model_copy(update={'command_version': 2, 'redemption_mode': 'quick'})
    if command.redemption_mode == "quick":
        from .wealth_quick import preview as quick_preview
        command, estimate = await quick_preview(db, user_id, command, row, context, now, price, lots)
        priced = now.astimezone(TZ).date()
    elif instant:
        from .wealth_instant import bind
        command = bind(command, row, context, price)
        priced = now.astimezone(TZ).date()
    from .wealth_instant import POLICY_VERSION, NOTICE
    return {"command": command.model_dump(), "digest": command_digest(command),
        "product_name": row.name, "product_risk_level": row.risk_level, "operation_security_level": "RED",
        "suitability": {**suitability, "matched": True} if suitability else None,
        "disclosure": effective_disclosure(row), "rules": row.rules, "estimate": estimate,
        "estimated_nav": price.price if price else None, "estimated_nav_date": price.nav_date.isoformat() if price else None,
        "pricing_date": priced.isoformat(), "confirm_date": (priced if instant or command.redemption_mode == "quick" else advance_business(priced, row.rules["confirm_business_days"], row.rules["calendar_version"])).isoformat(),
        "cash_date": (priced if instant or command.redemption_mode == "quick" else advance_business(priced, row.rules["cash_business_days"], row.rules["calendar_version"])).isoformat(),
        "execution_policy": POLICY_VERSION if instant else None,
        "notice": NOTICE if instant else "比赛模拟环境／模拟资金；预览为估算，受理不等于结算成功"}


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
    if row is None:
        from ...models.wealth_research import WealthDemoReceiptORM
        receipts = (await db.scalars(select(WealthDemoReceiptORM).where(
            WealthDemoReceiptORM.user_id == user_id, WealthDemoReceiptORM.action == 'delete'))).all()
        if any(operation_id in receipt.result.get('deleted_operation_ids', []) for receipt in receipts):
            raise BankError('原测试轮次已物理删除', 'GONE', 410)
    if row is None or row.user_id != user_id:
        raise BankError("当前未查到本人订单，请保留原操作号核对", "NOT_FOUND", 404)
    return row


async def accept_many(db: AsyncSession, user_id: str,
                      submissions: list[tuple[InvestmentCommand, str]]) -> list[dict]:
    """单笔/纯申购组合共用原子受理，占款与消费授权同事务。"""
    if not 1 <= len(submissions) <= MAX_EXECUTION_ITEMS or len({c.operation_id for c, _ in submissions}) != len(submissions):
        raise BankError(f"每批1至{MAX_EXECUTION_ITEMS}笔且操作号不能重复", "INVALID_PLAN", 422)
    from ...integrations.wealth_authorization import lock_profile
    await lock_profile(db, user_id)
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
        if preview['digest'] != command_digest(command):
            raise BankError('成交规则已更新，请重新预览、本人确认和MFA', 'PREVIEW_REQUIRED', 409)
        authorization = await validate_bank_authorization(db, user_id, auth_id, command)
        now = await business_now(db, command.account_id)
        version = await product_version(db, command)
        priced = pricing_date(now, version.rules["cutoff"], version.rules["calendar_version"])
        row = WealthOrderORM(operation_id=command.operation_id, user_id=user_id,
            account_id=command.account_id, version_id=version.version_id, direction=command.direction,
            amount_fen=command.amount_fen, units_scaled=scaled(command.units),
            param_digest=command_digest(command), authorization_id=auth_id, plan_id=command.plan_id,
            plan_version=command.plan_version, status="ACCEPTED", accepted_at=utc_now(), pricing_date=priced,
            confirm_date=advance_business(priced, version.rules["confirm_business_days"], version.rules["calendar_version"]),
            cash_date=advance_business(priced, version.rules["cash_business_days"], version.rules["calendar_version"]),
            details={"preview": preview["estimate"], "estimated_nav": preview["estimated_nav"],
                     "calculation_version": "mock-units-v1", "calendar_version": version.rules['calendar_version'],
                     'dataset_id':version.rules.get('dataset_id','legacy-v1'),'dataset_version':version.rules.get('dataset_version','v1'),
                     'business_at':now.isoformat(),
                     'execution_policy':preview.get('execution_policy'),
                     'original_product_pricing_policy_version':preview['disclosure'].get('runtime_pricing_policy_version'),
                     'runtime_pricing_policy_version':preview.get('execution_policy') or preview['disclosure'].get('runtime_pricing_policy_version')})
        evidence = await db.get(WealthVerificationORM, auth_id)
        row.details = {**row.details, "verification": {
            "method": evidence.method if evidence else "LEGACY_TOTP",
            "simulated": evidence.simulated if evidence else False,
            "replacement_required": evidence.replacement_required if evidence else False}}
        db.add(row)
        await db.flush()
        if preview.get('execution_policy') and command.redemption_mode != 'quick':
            from .wealth_instant import complete
            account = await owned_account(db, user_id, command.account_id)
            await complete(db, row, account, command, version, now, preview)
        elif command.direction == "purchase":
            db.add(WealthCashHoldORM(operation_id=row.operation_id, account_id=row.account_id,
                                     amount_fen=row.amount_fen, status="HELD"))
        else:
            if command.redemption_mode == "quick":
                from .wealth_quick import complete
                account = await owned_account(db, user_id, command.account_id)
                row.pricing_date = row.confirm_date = row.cash_date = now.astimezone(TZ).date()
                await complete(db, row, account, command, version, now, preview["estimate"])
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
                   "digest": row.param_digest, "version_id": row.version_id, "direction": row.direction,
                   "verification": row.details["verification"]},
            decision_chain=["subject_checked", "suitability_checked",
                "bound_confirmation_and_simulated_verification_checked" if evidence and evidence.simulated else "bound_confirmation_and_mfa_checked",
                "immediate_trade_settled_atomically" if preview.get('execution_policy') else "quick_cash_settled_atomically" if command.redemption_mode == "quick" else "funds_or_units_reserved"])
        await db.flush()
        results.append(order_view(row))
    await db.commit()
    return results


async def cancellation_eligibility(db: AsyncSession, user_id: str, operation_id: str) -> dict:
    """仅读资格，按绑定合同/方向/端点判断，不释放占款或份额。"""
    row = await owned_order(db, user_id, operation_id)
    version = await db.get(WealthProductVersionORM, row.version_id)
    rules = version.rules
    now = await business_now(db, row.account_id)
    deadline = datetime.combine(row.pricing_date, datetime.strptime(rules["cutoff"], "%H:%M").time(), TZ) if rules.get("cutoff") else None
    status, reason = "DENIED", "订单已计价、已完成或已撤销"
    if row.details.get("redemption_mode") == "quick":
        reason = "快赎成功受理并到账后不可撤"
    elif row.status in {"ACCEPTED", "PENDING_PRICING"}:
        if "cancel_before_pricing" not in rules or deadline is None:
            status, reason = "UNKNOWN", "撤单合同资料不足，需核对"
        elif not rules["cancel_before_pricing"]:
            reason = "该合同交易类型明确不可撤"
        elif now >= deadline:
            reason = "已达或超过计价日撤单截止时刻"
        else:
            status, reason = "ALLOWED", "模拟合同允许截止前全额撤单"
    return {"operation_id":row.operation_id,"eligibility":status,"reason":reason,
        "direction":row.direction,"action":"cancel_purchase" if row.direction == "purchase" else "withdraw_standard_redemption",
        "deadline":deadline.astimezone(timezone.utc).isoformat() if deadline else None,"end_inclusive":False,
        "as_of":utc_now().isoformat(),"business_at":now.isoformat(),"order_digest":row.param_digest,
        "version_id":row.version_id,"rule_source":rules.get("rules_source","versioned_mock_contract_v1"),
        "calendar_version":rules.get("calendar_version"),"full_only":True,
        "consequence":"释放占款，不增加余额、不生成退款流水" if row.direction == "purchase" else "释放未处置份额，不增加现金"}


async def cancel(db: AsyncSession, user_id: str, operation_id: str) -> dict:
    """仅计价前可撤单；账户锁排除并发结算，重复撤单返回事实。"""
    from ...integrations.wealth_authorization import lock_profile
    from ...models.wealth import WealthTaskORM
    await lock_profile(db, user_id)
    initial = await owned_order(db, user_id, operation_id)
    current_facts = await assets(db, user_id)
    if not any(row['operation_id']==operation_id for row in current_facts['orders']):
        raise BankError('订单不属于当前资产范围','ACCOUNT_CONTEXT_MISMATCH',409)
    controlled = await db.scalar(select(WealthTaskORM.task_id).where(WealthTaskORM.user_id==user_id,
        WealthTaskORM.account_id==initial.account_id,or_(WealthTaskORM.control_mode!='AGENT',WealthTaskORM.status=='PAUSED'),
        WealthTaskORM.status.not_in({'COMPLETED','PARTIAL_COMPLETED','CANCELLED','FAILED'})))
    if controlled:
        raise BankError('人工接管期间禁止通过旧入口撤单', 'TASK_WRITE_BLOCKED', 409)
    await lock_accounts(db, [initial.account_id])
    row = await owned_order(db, user_id, operation_id)
    if row.status == "CANCELLED":
        return order_view(row)
    eligibility = await cancellation_eligibility(db, user_id, operation_id)
    if eligibility["eligibility"] != "ALLOWED":
        raise BankError(eligibility["reason"], "CANCELLATION_RULES_UNKNOWN" if eligibility["eligibility"] == "UNKNOWN" else "CANCELLATION_NOT_ALLOWED", 409)
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
    from .wealth_reconciliation import require_reconciled
    await require_reconciled(db, row.account_id)
    from .wealth_demo import require_active
    await require_active(db, row.account_id)
    now = await business_now(db, row.account_id)
    day = now.astimezone(TZ).date()
    version = await db.get(WealthProductVersionORM, row.version_id)
    account = await db.get(AccountORM, row.account_id, populate_existing=True)
    if row.status!='PENDING_CASH' and version.rules.get('pricing_mode') in PRICING_MODES:
        from .wealth_quotes import refresh
        await refresh(db,[version.version_id],now,subject_user_id=row.user_id)
    if row.status != "PENDING_CASH":
        row.status = "PENDING_PRICING"
        price = await db.get(WealthNavORM, f"{row.version_id}:{row.pricing_date.isoformat()}")
        if version.rules.get('pricing_mode') in PRICING_MODES:
            row.details={**row.details,'underlying_source_kind':'real','execution_source_kind':'simulated',
                'source_nav_date':price.nav_date.isoformat() if price else None,
                'price_source':'public_nav_for_order_pricing_date','project_policy_version':version.rules['execution_policy']['version']}
        from ...models.wealth_research import WealthContextORM
        context=(await db.scalars(select(WealthContextORM).where(WealthContextORM.account_id==row.account_id))).first()
        if context and context.data.get('fixture',{}).get('missing_pricing_nav'): price=None
        if day < row.confirm_date or price is None or price.nav_date > day or nav_available_at(price)>now:
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
                acquired_date=row.confirm_date, unlock_date=date.fromisoformat(version.rules['closed_maturity_date']) if version.rules.get('closed_maturity_date') else row.confirm_date+timedelta(days=version.rules["holding_days"])))
            row.units_scaled = scaled(value.units)
            row.details = {**row.details, "actual": asdict(value), "actual_nav": price.price, "waiting_reason": None}
            row.status = "SETTLED"
            db.add(TransactionORM(tx_id=stable_id("WPT", row.operation_id), operation_id=row.operation_id, account_id=row.account_id,
                type="investment_purchase", amount_fen=value.gross_fen-value.refund_fen,
                counterparty=version.name, category="investment", note=None,
                timestamp=utc_now(), status="completed", channel="app"))
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
            kind="pricing", data={**row.details["actual"], "business_at": now.isoformat()}, created_at=utc_now())
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
            kind="cash", data={"cash_fen": cash, "business_at": now.isoformat()}, created_at=utc_now())
        db.add(event)
        if cash > 0:
            db.add(TransactionORM(tx_id=stable_id("WRT", row.operation_id), operation_id=row.operation_id, account_id=row.account_id,
                type="investment_redemption", amount_fen=cash, counterparty=version.name,
                category="investment", note=None, timestamp=utc_now(), status="completed", channel="app"))
        record_bank_event(db, event_key=event.event_id, subject_user_id=row.user_id,
            action="investment_cash", result="success", facts={"operation_id": row.operation_id, "cash_fen": cash},
            decision_chain=["receivable_checked", "cash_date_reached", "unique_credit"])
    await db.commit()
    return order_view(row)


async def assets(db: AsyncSession, user_id: str, account_id: str | None = None) -> dict:
    """现金、在途应收、持仓分开估值，占款仅为现金子集不重复相加。"""
    from .wealth_research import selected
    from ...models.wealth_research import WealthContextORM
    if account_id:
        account = await db.get(AccountORM, account_id)
        if not account or account.user_id != user_id:
            raise BankError('账户不存在', 'ACCOUNT_NOT_FOUND', 404)
        context = await db.scalar(select(WealthContextORM).where(WealthContextORM.account_id==account_id))
    else:
        context = await selected(db, user_id)
    now = await business_now(db, context.account_id if context else None)
    from .account_scope import isolated_account_ids, is_shared
    sandbox_accounts = set((await db.scalars(isolated_account_ids().where(WealthContextORM.user_id == user_id))).all())
    group_accounts = {context.account_id} if context else set()
    if is_shared(context):
        group_accounts = {a.account_id for a in (await db.scalars(select(AccountORM).where(
            AccountORM.user_id == user_id, AccountORM.account_id.not_in(isolated_account_ids())))).all()}
    elif context:
        peers=(await db.scalars(select(WealthContextORM).where(WealthContextORM.user_id==user_id))).all()
        run_scope=context.data.get('run_id',context.context_id)
        group_accounts={peer.account_id for peer in peers if peer.data.get('disposable') is True and
            peer.data.get('run_id',peer.context_id)==run_scope}
    accounts = [a for a in (await db.scalars(select(AccountORM).where(AccountORM.user_id == user_id))).all()
                  if (a.account_id == account_id if account_id else a.account_id in group_accounts if context else a.account_id not in sandbox_accounts)]
    allowed_accounts = {a.account_id for a in accounts}
    cash = [{"account_id": a.account_id, "account_type": a.account_type,
             "balance_fen": a.balance_fen, "held_fen": await held_cash(db, a.account_id),
             "available_fen": await available_cash(db, a.account_id, a.balance_fen)} for a in accounts]
    lots = []
    for lot in (await db.scalars(select(WealthLotORM).where(WealthLotORM.user_id == user_id,
            WealthLotORM.units_scaled > 0).order_by(WealthLotORM.acquired_date, WealthLotORM.lot_id))).all():
        if lot.account_id not in allowed_accounts:
            continue
        price = await latest_nav(db, lot.version_id, now)
        version = await db.get(WealthProductVersionORM, lot.version_id)
        held = int(await db.scalar(select(func.coalesce(func.sum(WealthUnitHoldORM.units_scaled), 0)).where(
            WealthUnitHoldORM.lot_id == lot.lot_id, WealthUnitHoldORM.status == "HELD")) or 0)
        value = int((Decimal(quantity(lot.units_scaled)) * Decimal(price.price) * 100).quantize(Decimal("1"))) if price else None
        from .wealth_quotes import _cache
        quote_status=db.info.get('wealth_quote_updates',{}).get(version.product_id,_cache.get(version.product_id,(None,{}))[1])
        lots.append({"lot_id": lot.lot_id, "product_id": version.product_id, "product_name": version.name,
            'quote_update_status':'failed_using_last_reliable' if quote_status.get('ok') is False and price else 'unavailable' if price is None else 'available',
            'quote_update_error':quote_status.get('error'),
            "account_id": lot.account_id, "version_id": lot.version_id, "category": version.category,
            'fund_family_id':version.rules.get('fund_family_id',version.product_id),
            "units": quantity(lot.units_scaled), "held_units": quantity(held),
            "redeemable_units": quantity(lot.units_scaled-held) if lot.unlock_date <= now.astimezone(TZ).date() else "0.0000",
            "locked_units": quantity(lot.units_scaled) if lot.unlock_date > now.astimezone(TZ).date() else "0.0000",
            "cost_fen": lot.cost_fen, "market_value_fen": value,
            "unrealized_pnl_fen": value-lot.cost_fen if value is not None else None,
            "nav": price.price if price else None, "nav_date": price.nav_date.isoformat() if price else None,
            "acquired_date": lot.acquired_date.isoformat(), "unlock_date": lot.unlock_date.isoformat()})
    orders = (await db.scalars(select(WealthOrderORM).where(WealthOrderORM.user_id == user_id)
                               .order_by(WealthOrderORM.accepted_at.desc()))).all()
    orders = [row for row in orders if row.account_id in allowed_accounts]
    receivable = sum(row.details["actual"]["cash_fen"] for row in orders if row.status == "PENDING_CASH")
    realized = sum(row.details.get("actual", {}).get("realized_pnl_fen", 0) for row in orders if row.direction == "redeem")
    from ...domain.wealth_analytics import concentration, hypothetical_portfolio
    concentration_view=concentration([{**lot,'amount_fen':lot['market_value_fen'] or 0} for lot in lots])
    hypothesis=None
    if context and lots and all(lot['market_value_fen'] is not None for lot in lots):
        from .wealth_research import catalog
        weights={}
        for lot in lots: weights[lot['product_id']]=weights.get(lot['product_id'],0)+lot['market_value_fen']
        hypothesis=hypothetical_portfolio(await catalog(db,context.dataset_key),weights,now.astimezone(TZ).date().isoformat())
    order_rows = [order_view(row) for row in orders]
    from .wealth_table_facts import enrich
    await enrich(db, lots, order_rows, now)
    facts = {"cash": cash, "lots": lots, "orders": order_rows,
        'concentration':concentration_view,'historical_hypothesis':hypothesis,'actual_return_unavailable_reason':'NO_VERIFIED_COMPLETE_CASHFLOW_RETURN_SERIES',
        "receivable_fen": receivable, "realized_pnl_fen": realized,
        "bank_assets_fen": sum(a["balance_fen"] for a in cash if a["account_type"] != "credit") + receivable
            + sum(lot["market_value_fen"] or 0 for lot in lots) if all(lot["market_value_fen"] is not None for lot in lots) else None,
        "valuation_complete": all(lot["market_value_fen"] is not None for lot in lots),
        "business_at": now.isoformat(), "as_of": utc_now().isoformat(),
        "context_id": context.context_id if context else "legacy-v1",
        "scope": "disposable_simulation" if context and not is_shared(context) else "ordinary",
        "notice": "比赛模拟环境／模拟资金；外部资产不计入银行可交易资金"}
    if account_id is None:
        from .wealth_asset_summary import build
        facts['asset_summary'] = build(user_id, facts)
    return facts



async def external_evidence(db, user_id, account_id, event_reference):
    """银行包内读取外部依赖账务事实，HTTP层不访问账本ORM。"""
    from ...models import AccountORM,TransactionORM
    from ...domain.time import as_utc,utc_iso
    account=await db.get(AccountORM,account_id)
    event=await db.get(TransactionORM,event_reference)
    if (account is None or account.user_id!=user_id or event is None
        or event.account_id!=account.account_id or event.type!='transfer_out' or event.status!='completed'):
        raise BankError('未找到当前本人账户的已完成转账事实','EXTERNAL_EVIDENCE_INVALID',409)
    now=await business_now(db,account.account_id)
    if as_utc(event.timestamp,legacy_utc=True)>now:
        raise BankError('未来流水不能用于完成外部依赖','EXTERNAL_EVIDENCE_INVALID',409)
    return {'event_reference':event.tx_id,'account_id':account.account_id,'type':event.type,
        'status':'completed','occurred_at':utc_iso(as_utc(event.timestamp,legacy_utc=True)),
        'operation_id':event.operation_id,'source':'bank_ledger'}
