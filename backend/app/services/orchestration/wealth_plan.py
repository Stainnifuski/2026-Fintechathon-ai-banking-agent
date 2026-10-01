"""5号持久化理财计划；通过HTTP获取事实，无银行ORM依赖。"""

from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.wealth_allocation import allocate, WEIGHTS, DEVIATION_PERCENT
from ...integrations.wealth_bank_api import HttpWealthBankApi
from ...integrations.bank_api import BankApiError, BankResponseUnknown
from ...integrations.wealth_authorization import WealthPolicyError, require_suitability, lock_profile
from ...models.wealth import WealthPlanORM, WealthProfileORM, WealthGoalORM, WealthAuthorizationORM
from ...schemas.wealth import InvestmentCommand


async def owned_plan(db: AsyncSession, user_id: str, plan_id: str) -> WealthPlanORM:
    """归属检查先于计划读取或变更。"""
    row = await db.get(WealthPlanORM, plan_id, populate_existing=True)
    if row is None or row.user_id != user_id:
        raise WealthPolicyError("方案不存在", "NOT_FOUND", 404)
    return row


def plan_view(row: WealthPlanORM) -> dict:
    """返回版本化快照和逐笔状态。"""
    return {"plan_id": row.plan_id, "version": row.version, "status": row.status, **row.data}


async def create(db: AsyncSession, user_id: str, bank: HttpWealthBankApi, request: dict,
                 plan_id: str | None = None) -> dict:
    """新资金/持仓调整先读取事实，随后短事务保存版本化计划。"""
    facts, products = await bank.assets(user_id), await bank.products()
    suitability = await require_suitability(db, user_id, 1)
    budget, reserve = request["budget_fen"], request["reserve_fen"]
    account = next((a for a in facts["cash"] if a["account_id"] == request["account_id"] and a["account_type"] != "credit"), None)
    if account is None:
        raise WealthPolicyError("账户不存在或不支持", "ACCOUNT_NOT_FOUND", 404)
    if budget > account["available_fen"]:
        raise WealthPolicyError("方案预算超过银行可用现金", "INSUFFICIENT_AVAILABLE_FUNDS")
    goals = (await db.scalars(select(WealthGoalORM).where(WealthGoalORM.user_id == user_id))).all()
    business_date = date.fromisoformat(facts["business_at"][:10])
    goal_reserve = sum(goal.data["amount_fen"] for goal in goals if
        date.fromisoformat(goal.data["due_date"]) <= business_date+timedelta(days=365)
        and goal.data["liquidity"] == "immediate")
    effective_reserve = max(reserve, min(goal_reserve, budget))
    config = allocate(products, budget_fen=budget, reserve_fen=effective_reserve,
        capacity=suitability["capacity_level"], horizon_days=request["horizon_days"],
        excluded=request["excluded_products"], purchase_allowed=suitability["purchase_allowed"])
    goal_versions = {g.goal_id: g.version for g in goals}
    await db.rollback()
    await lock_profile(db, user_id)
    row = await owned_plan(db, user_id, plan_id) if plan_id else None
    if row and (row.version != request.get("version") or row.status in {"EXECUTING", "WAITING_SETTLEMENT", "COMPLETED"}):
        raise WealthPolicyError("方案版本已改变或已受理，请先核对在途", "STALE_PLAN")
    if row:
        row.version += 1
    else:
        row = WealthPlanORM(plan_id=f"WP{uuid4().hex}", user_id=user_id, version=1,
            status="AWAITING_CONFIRMATION", data={})
        db.add(row)
    items = []
    if request["kind"] == "adjustment":
        # 先用新增现金改善配置；预算能投资时不先卖出，避免不必要费用。
        if not config["items"]:
            total = sum(lot["market_value_fen"] or 0 for lot in facts["lots"])
            target = WEIGHTS[suitability["capacity_level"]]
            current = {category: sum(lot["market_value_fen"] or 0 for lot in facts["lots"] if lot["category"] == category)
                       for category in {lot["category"] for lot in facts["lots"]}}
            for lot in facts["lots"]:
                if len(items) >= 5:
                    break
                value = lot["market_value_fen"]
                target_fen = total*target.get(lot["category"], 0)//100
                excess = current[lot["category"]]-target_fen
                if not value or excess <= total*DEVIATION_PERCENT//100 or Decimal(lot["redeemable_units"]) <= 0:
                    continue
                count = min(Decimal(lot["redeemable_units"]),
                    (Decimal(excess)/Decimal(value)*Decimal(lot["units"])).quantize(Decimal("0.0001"), ROUND_DOWN))
                if count <= 0:
                    continue
                product = next(p for p in products if p["product_id"] == lot["product_id"])
                cmd = InvestmentCommand(operation_id=f"WO{uuid4().hex}", direction="redeem",
                    product_id=product["product_id"], product_version=product["version"],
                    disclosure_version=product["disclosure"]["version"], account_id=lot["account_id"],
                    units=format(count, ".4f"), plan_id=row.plan_id, plan_version=row.version)
                items.append({"command": cmd.model_dump(), "reason": "偏离模拟目标，按可赎回份额减持；费用和锁定期仍需逐笔预检",
                              "status": "AWAITING_CONFIRMATION", "depends_on": []})
                current[lot["category"]] -= int(count/Decimal(lot["units"])*value)
    if not items:
        for item in config["items"]:
            cmd = InvestmentCommand(operation_id=f"WO{uuid4().hex}", direction="purchase",
                product_id=item["product_id"], product_version=item["product_version"],
                disclosure_version=item["disclosure_version"], account_id=request["account_id"],
                amount_fen=item["amount_fen"], plan_id=row.plan_id, plan_version=row.version)
            items.append({"command": cmd.model_dump(), "reason": item["reason"],
                          "status": "AWAITING_CONFIRMATION", "depends_on": []})
    profile = await db.get(WealthProfileORM, user_id)
    row.status = "AWAITING_CONFIRMATION" if items else "COMPLETED"
    row.data = {"request": request, "configuration": config, "items": items,
        "profile_version": profile.version, "snapshot": facts, "goal_versions": goal_versions,
        "stage": "SELL" if items and items[0]["command"]["direction"] == "redeem" else "BUY",
        "external_assets": profile.data.get("external_assets", []),
        "limitations": ["外部资产只诊断，不能形成银行交易", "目标分配为标签，不自动冻结余额", "锁定和在途份额不强制卖出"],
        "notice": "比赛模拟环境／模拟资金；调整不自动交易，赎回后买入需新确认和强验证"}
    await db.commit()
    return plan_view(row)


async def progress(db: AsyncSession, user_id: str, plan_id: str, bank: HttpWealthBankApi) -> dict:
    """核对每一笔银行事实；404或网络问题不释放占款或伪造失败。"""
    row = await owned_plan(db, user_id, plan_id)
    data, version = dict(row.data), row.version
    items = [dict(item) for item in data["items"]]
    await db.rollback()
    for item in items:
        if item["status"] in {"AWAITING_CONFIRMATION", "PAUSED"}:
            continue
        try:
            fact = await bank.query(user_id, item["command"]["operation_id"])
            item["status"], item["bank_result"] = fact["status"], fact
        except BankApiError:
            item["status"] = "UNKNOWN"
    await lock_profile(db, user_id)
    row = await owned_plan(db, user_id, plan_id)
    if row.version != version:
        raise WealthPolicyError("计划已更新", "STALE_PLAN")
    statuses = {item["status"] for item in items}
    if items and statuses <= {"SETTLED"}:
        row.status = "AWAITING_CONFIRMATION" if data["stage"] == "SELL" else "COMPLETED"
        if data["stage"] == "SELL":
            data["next_action"] = "赎回已到账，请刷新可用现金并创建新购买版本，重新确认与强验证"
    elif statuses & {"UNKNOWN", "SUBMITTING", "ACCEPTED", "PENDING_PRICING", "PENDING_CASH"}:
        row.status = "WAITING_SETTLEMENT"
    elif "SETTLED" in statuses:
        row.status = "PARTIALLY_COMPLETED"
    data["items"] = items
    row.data = data
    await db.commit()
    return plan_view(row)


async def execute(db: AsyncSession, user_id: str, plan_id: str, version: int, bank: HttpWealthBankApi) -> dict:
    """纯申购全部原子占款，卖出逐笔执行；失败暂停剩余，不撤销成功。"""
    await lock_profile(db, user_id)
    row = await owned_plan(db, user_id, plan_id)
    if row.version != version or row.status not in {"AWAITING_CONFIRMATION", "EXECUTING", "WAITING_SETTLEMENT"}:
        raise WealthPolicyError("旧版本或不可执行方案", "STALE_PLAN")
    data = dict(row.data)
    pending = [dict(item) for item in data["items"] if item["status"] == "AWAITING_CONFIRMATION"]
    submissions = []
    for item in pending:
        auth = (await db.scalars(select(WealthAuthorizationORM).where(
            WealthAuthorizationORM.user_id == user_id,
            WealthAuthorizationORM.operation_id == item["command"]["operation_id"]))).first()
        if auth is None or auth.status != "VALID":
            raise WealthPolicyError("每笔都需要当前版本的本人确认及强验证", "AUTHORIZATION_REQUIRED")
        submissions.append({"subject_user_id": user_id, "command": item["command"], "authorization_id": auth.authorization_id})
    if not submissions:
        await db.rollback()
        return await progress(db, user_id, plan_id, bank)
    data["items"] = [{**item, "status": "SUBMITTING"} if item["status"] == "AWAITING_CONFIRMATION" else item for item in data["items"]]
    row.data, row.status = data, "EXECUTING"
    await db.commit()
    try:
        if data["stage"] == "BUY":
            await bank.call("POST", "/orders/batch", {"subject_user_id": user_id, "submissions": submissions})
        else:
            for index, item in enumerate(submissions):
                try:
                    await bank.submit(user_id, InvestmentCommand.model_validate(item["command"]), item["authorization_id"])
                except BankApiError:
                    # 后续尚未提交的卖出明确暂停，当前笔仍保留UNKNOWN核对。
                    for unsubmitted in submissions[index+1:]:
                        for child in data["items"]:
                            if child["command"]["operation_id"] == unsubmitted["command"]["operation_id"]:
                                child["status"] = "PAUSED"
                    raise
    except BankApiError as error:
        await lock_profile(db, user_id)
        row = await owned_plan(db, user_id, plan_id)
        row.data = {**data, "last_error": {"code": error.code, "message": error.message}}
        row.status = "PAUSED"
        await db.commit()
    return await progress(db, user_id, plan_id, bank)
