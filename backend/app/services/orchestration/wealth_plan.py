"""5号持久化理财计划；通过HTTP获取事实，无银行ORM依赖。"""

from datetime import date, datetime, timedelta
from ...domain.time import BEIJING
from decimal import Decimal, ROUND_DOWN
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.wealth_allocation import DEVIATION_PERCENT
from ...domain.wealth_limits import MAX_EXECUTION_ITEMS
from ...domain.wealth_recommendation import recommend
from ...integrations.wealth_bank_api import HttpWealthBankApi
from ...integrations.bank_api import BankApiError, BankResponseUnknown
from ...integrations.wealth_authorization import WealthPolicyError, require_suitability, lock_profile
from ...models.wealth import WealthPlanORM, WealthProfileORM, WealthGoalORM, WealthAuthorizationORM
from ...schemas.wealth import InvestmentCommand
from ...schemas.wealth import AdoptPlan, PlanConfirmation, PersonalConfirmation
from ...domain.wealth import units, purchase


async def owned_plan(db: AsyncSession, user_id: str, plan_id: str) -> WealthPlanORM:
    """归属检查先于计划读取或变更。"""
    row = await db.get(WealthPlanORM, plan_id, populate_existing=True)
    if row is None or row.user_id != user_id:
        raise WealthPolicyError("方案不存在", "NOT_FOUND", 404)
    return row


async def confirmation_commands(db: AsyncSession, user_id: str, plan_id: str, version: int) -> list[InvestmentCommand]:
    """只预览当前未提交整份方案，不能通过删掉验证项遗漏某笔。"""
    row = await owned_plan(db, user_id, plan_id)
    if row.version != version or row.status != "AWAITING_CONFIRMATION":
        raise WealthPolicyError("方案已更新或已经受理，请核对当前版本", "STALE_PLAN")
    commands = [InvestmentCommand.model_validate(item["command"]) for item in row.data["items"]
                if item["status"] == "AWAITING_CONFIRMATION"]
    if not commands:
        raise WealthPolicyError("没有待确认订单；保留现金无需验证", "INVALID_PLAN", 422)
    return commands


async def preview_all(db: AsyncSession, user_id: str, plan_id: str, version: int, bank: HttpWealthBankApi) -> dict:
    """整页预览，每笔仍绑定独立操作号和披露摘要。"""
    from ...integrations import wealth_authorization as policy
    commands = await confirmation_commands(db, user_id, plan_id, version)
    await db.rollback()
    views = [await bank.precheck(user_id, command) for command in commands]
    await policy.lock_profile(db, user_id)
    current = await confirmation_commands(db, user_id, plan_id, version)
    if [c.model_dump() for c in current] != [c.model_dump() for c in commands]:
        raise WealthPolicyError('预览期间方案已变化，请重新预览', 'STALE_PLAN')
    plan = await owned_plan(db, user_id, plan_id)
    normalized = {v['command']['operation_id']: v['command'] for v in views}
    plan.data = {**plan.data, 'items': [{**item, 'command': normalized.get(item['command']['operation_id'], item['command'])}
        for item in plan.data['items']]}
    items = []
    for command, view in zip(commands, views):
        command = InvestmentCommand.model_validate(view['command'])
        auth = await policy.new_preview_authorization(db, user_id, command)
        items.append({**view, "command": command.model_dump(), "authorization_id": auth.authorization_id,
            "authorization_status": auth.status, "digest": auth.param_digest, "expires_at": policy.aware(auth.expires_at).isoformat()})
    from ...integrations.wealth_verification import verification_reference
    reference = verification_reference([await policy.owned_authorization(db,user_id,i["authorization_id"]) for i in items], grouped=True)
    await db.commit()
    return {"plan_id": plan_id, "version": version, "items": items, "verification_reference_id": reference}


async def confirm_all(db: AsyncSession, user_id: str, plan_id: str, request: PlanConfirmation, bank: HttpWealthBankApi) -> dict:
    """一次本人确认，所有逐笔记录原子落库；任一失效则全部拒绝。"""
    from ...integrations import wealth_authorization as policy
    commands = await confirmation_commands(db, user_id, plan_id, request.version)
    rows = [await policy.owned_authorization(db, user_id, item.authorization_id) for item in request.items]
    if len(rows) != len(commands) or {row.operation_id for row in rows} != {cmd.operation_id for cmd in commands}:
        raise WealthPolicyError("必须确认当前方案全部选中订单，不能重复或遗漏", "STALE_PLAN")
    await db.rollback()
    for command in commands:
        checked = await bank.precheck(user_id, command)
        from ...domain.wealth_command import command_digest
        if checked['digest'] != command_digest(command):
            raise WealthPolicyError('成交规则已更新，请重新预览整份方案', 'PREVIEW_REQUIRED', 409)
    await policy.lock_profile(db, user_id)
    await confirmation_commands(db, user_id, plan_id, request.version)
    for item in request.items:
        await policy.record_confirmation(db, user_id, item.authorization_id,
            PersonalConfirmation(digest=item.digest, disclosure_version=item.disclosure_version, accepted=item.accepted), commit=False)
    await db.commit()
    return {"plan_id": plan_id, "version": request.version, "status": "AWAITING_MFA"}


async def verify_all(db: AsyncSession, user_id: str, plan_id: str, request, bank: HttpWealthBankApi) -> dict:
    """一次统一第二因素验证当前方案，银行再次核对完整集合。"""
    from ...integrations import wealth_authorization as policy
    commands = await confirmation_commands(db, user_id, plan_id, request.version)
    ids = []
    for command in commands:
        row = (await db.scalars(select(WealthAuthorizationORM).where(WealthAuthorizationORM.operation_id == command.operation_id))).one_or_none()
        if row is None:
            raise WealthPolicyError("请先预览并确认整份方案", "CONFIRMATION_REQUIRED")
        row = await policy.owned_authorization(db, user_id, row.authorization_id)
        ids.append(row.authorization_id)
    await db.rollback()
    return await bank.call("POST", "/verify/batch", {"subject_user_id": user_id, "authorization_ids": ids,
        "plan_id": plan_id, **request.model_dump()})


def plan_view(row: WealthPlanORM) -> dict:
    """返回版本化快照和逐笔状态。"""
    return {"plan_id": row.plan_id, "version": row.version, "status": row.status, **row.data}


async def create(db: AsyncSession, user_id: str, bank: HttpWealthBankApi, request: dict,
                 plan_id: str | None = None) -> dict:
    """新资金/持仓调整先读取事实，随后短事务保存版本化计划。"""
    facts, products = await bank.assets(user_id), await bank.products(user_id)
    suitability = await require_suitability(db, user_id, 1, allow_cash_only=True)
    profile = await db.get(WealthProfileORM, user_id)
    if request.get('need')=='cash': suitability={**suitability,'capacity_level':1}
    elif request.get('need')=='balanced': suitability={**suitability,'capacity_level':min(suitability['capacity_level'],3)}
    external = list(profile.data.get("external_assets", [])) if profile else []
    budget, reserve = request["budget_fen"], request.get("reserve_fen")
    account = next((a for a in facts["cash"] if a["account_id"] == request["account_id"] and a["account_type"] != "credit"), None)
    if account is None:
        raise WealthPolicyError("账户不存在或不支持", "ACCOUNT_NOT_FOUND", 404)
    if budget > account["available_fen"]:
        raise WealthPolicyError(f"本次方案预算¥{budget/100:.2f}超过银行可用现金¥{account['available_fen']/100:.2f}。持仓和在途不属于可用现金，请明确修改本次预算。", "INSUFFICIENT_AVAILABLE_FUNDS")
    goals = (await db.scalars(select(WealthGoalORM).where(WealthGoalORM.user_id == user_id))).all()
    business_date = datetime.fromisoformat(facts["business_at"]).astimezone(BEIJING).date()
    goal_reserve = sum(goal.data["amount_fen"] for goal in goals if
        date.fromisoformat(goal.data["due_date"]) <= business_date+timedelta(days=365)
        and goal.data["liquidity"] == "immediate")
    # 现金保留属于整个账户；预算之外的可用现金已满足的部分不重复扣除。
    outside_budget=max(0,account['available_fen']-budget)
    effective_reserve=min(budget,max(0,max(reserve or 0,goal_reserve)-outside_budget))
    category_values = {}
    def allocation(products, **parameters):
        parameters['full_investment']=request.get('full_investment',False)
        versions={p['product_id']:{'product_version':p['version'],
            'dataset_key':p.get('dataset_key'),'calendar_version':p['rules']['calendar_version'],
            'nav_date':p.get('nav_date')} for p in products}
        return recommend({'products':products,'allocation':parameters,'as_of':facts['business_at'],
            'available_cash_fen':account['available_fen'],'minimum_account_cash_fen':max(reserve or 0,goal_reserve),
            'constraints':request.get('constraint_states',{}),'data_versions':versions,
            'features':{'history_status':'not_required_by_rule_provider'},
            'current_exposure_fen':category_values})
    config = allocation(products, budget_fen=budget, reserve_fen=effective_reserve,
        capacity=suitability["capacity_level"], horizon_days=request["horizon_days"],
        excluded=request["excluded_products"], purchase_allowed=suitability["purchase_allowed"])
    goal_versions = {g.goal_id: g.version for g in goals}
    category_values = {}
    for lot in facts["lots"]:
        category_values[lot["category"]] = category_values.get(lot["category"], 0)+(lot["market_value_fen"] or 0)
    for asset in external:
        category_values[asset["category"]] = category_values.get(asset["category"], 0)+asset["amount_fen"]
    category_values["cash"] = category_values.get("cash", 0)+sum(a["balance_fen"] for a in facts["cash"] if a["account_type"] != "credit")
    diagnostic = {"category_values_fen": category_values, "external_value_fen": sum(a["amount_fen"] for a in external),
        "liabilities_fen": profile.data.get("liabilities_fen", 0) if profile else 0,
        "note": "包括外部模拟资产的全景诊断，与银行可交易资产分开；锁定和在途不作为即时交易资金"}
    total_value = sum(category_values.values())
    diagnostic["allocation"] = [{"category": category,
        "current_fen": category_values.get(category, 0), "target_percent": weight,
        "target_fen": total_value*weight//100,
        "difference_fen": total_value*weight//100-category_values.get(category, 0)}
        for category, weight in config['target_category_weights'].items()]
    diagnostic["goal_shortfall_fen"] = max(0, goal_reserve-budget)
    diagnostic["expense_reserve_fen"] = profile.data.get("monthly_expenses_fen", 0) if profile else 0
    effective_reserve=max(effective_reserve,min(budget,max(0,diagnostic['expense_reserve_fen']-outside_budget)))
    config = allocation(products, budget_fen=budget, reserve_fen=effective_reserve,
        capacity=suitability["capacity_level"], horizon_days=request["horizon_days"],
        excluded=request["excluded_products"], purchase_allowed=suitability["purchase_allowed"],
        exposure_fen=category_values if request["kind"] == "adjustment" or facts["lots"] or external else None)
    await db.rollback()
    await lock_profile(db, user_id)
    row = await owned_plan(db, user_id, plan_id) if plan_id else None
    if row and row.data.get('portfolio_id'):
        raise WealthPolicyError('整体目标必须经配置服务修订，不能用旧预算接口覆盖', 'PORTFOLIO_REVISION_REQUIRED', 409)
    linked_task=None
    if row:
        from .wealth_task import require_plan_edit
        linked_task=await require_plan_edit(db,user_id,plan_id)
    if row and (row.version != request.get("version") or row.status in {"EXECUTING", "WAITING_SETTLEMENT", "COMPLETED"}):
        raise WealthPolicyError("方案版本已改变或已受理，请先核对在途", "STALE_PLAN")
    if row and any(item["status"] in {"SUBMITTING", "UNKNOWN", "ACCEPTED", "PENDING_PRICING", "PENDING_CASH"} for item in row.data["items"]):
        raise WealthPolicyError("仍有在途或未知步骤，请先核对银行事实，不能覆盖原方案", "STALE_PLAN")
    if row:
        previous_history = list(row.data.get("history", []))
        if row.data.get("stage") == "SELL" and all(item["status"] == "SETTLED" for item in row.data["items"]):
            previous_history.append({"version": row.version, "stage": "SELL", "items": row.data["items"]})
            # 原卖出成功事实保留，新版本购买必须全部新授权。
            request = {**request, "kind": "new_cash"}
        elif any(item["status"] == "SETTLED" for item in row.data["items"]):
            previous_history.append({"version": row.version, "stage": row.data["stage"],
                "items": [item for item in row.data["items"] if item["status"] == "SETTLED"]})
        row.version += 1
    else:
        previous_history = []
        row = WealthPlanORM(plan_id=f"WP{uuid4().hex}", user_id=user_id, version=1,
            status="AWAITING_CONFIRMATION", data={})
        db.add(row)
    prior_count = sum(len(stage["items"]) for stage in previous_history)
    if prior_count:
        config = allocation(products, budget_fen=budget, reserve_fen=effective_reserve,
            capacity=suitability["capacity_level"], horizon_days=request["horizon_days"],
            excluded=request["excluded_products"], purchase_allowed=suitability["purchase_allowed"],
            exposure_fen=category_values, max_items=max(0, MAX_EXECUTION_ITEMS-prior_count))
    items = []
    if request["kind"] == "adjustment":
        # 先用新增现金改善配置；预算能投资时不先卖出，避免不必要费用。
        if not config["items"]:
            total = sum(category_values.values())
            target = config['target_category_weights']
            current = dict(category_values)
            for lot in facts["lots"]:
                if len(items) >= MAX_EXECUTION_ITEMS:
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
    config['pricing_snapshot'] = {p['product_id']: {'nav': p['nav'],
        'purchase_fee_rate': p['rules']['purchase_fee_rate'], 'product_version': p['version']}
        for p in products if any(i['command']['product_id'] == p['product_id'] for i in items)}
    row.data = {"request": request, "configuration": config, "items": items,
        "history": previous_history,
        "diagnostic": diagnostic,
        "profile_version": profile.version, "snapshot": facts, "goal_versions": goal_versions,
        "stage": "SELL" if items and items[0]["command"]["direction"] == "redeem" else "BUY",
        "external_assets": profile.data.get("external_assets", []),
        "limitations": ["外部资产只诊断，不能形成银行交易", "目标分配为标签，不自动冻结余额", "锁定和在途份额不强制卖出"],
        "notice": "比赛模拟环境／模拟资金；调整不自动交易，赎回后买入需新确认和强验证"}
    await db.commit()
    if linked_task:
        from .wealth_task import register_plan
        await register_plan(db,user_id,linked_task.session_id,linked_task.context_id,plan_view(row))
    return plan_view(row)


async def progress(db: AsyncSession, user_id: str, plan_id: str, bank: HttpWealthBankApi) -> dict:
    """核对每一笔银行事实；404或网络问题不释放占款或伪造失败。"""
    row = await owned_plan(db, user_id, plan_id)
    data, version = dict(row.data), row.version
    items = [dict(item) for item in data["items"]]
    await db.rollback()
    for item in items:
        if item["status"] in {"AWAITING_CONFIRMATION", "PAUSED", "REJECTED", "CANCELLED"}:
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
        row.status = "AWAITING_CONFIRMATION" if data["stage"] == "SELL" and not data.get('manual_redemption') else "COMPLETED"
        if data.get('manual_redemption'):
            data['next_action'] = '赎回已全部成交，资金与持仓已刷新'
        elif data["stage"] == "SELL":
            data["next_action"] = "赎回已到账，请刷新可用现金并创建新购买版本，重新确认与强验证"
    elif "PAUSED" in statuses or statuses & {"REJECTED", "CANCELLED"}:
        row.status = "PAUSED"
        data["next_action"] = "未提交步骤已暂停；继续核对在途及成功订单，调整需创建新版本，不自动回滚"
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
                except BankApiError as error:
                    if not isinstance(error, BankResponseUnknown):
                        for child in data["items"]:
                            if child["command"]["operation_id"] == item["command"]["operation_id"]:
                                child["status"] = "REJECTED"
                    # 后续尚未提交的卖出明确暂停，当前笔仍保留UNKNOWN核对。
                    for unsubmitted in submissions[index+1:]:
                        for child in data["items"]:
                            if child["command"]["operation_id"] == unsubmitted["command"]["operation_id"]:
                                child["status"] = "PAUSED"
                    raise
    except BankApiError as error:
        await lock_profile(db, user_id)
        row = await owned_plan(db, user_id, plan_id)
        if data["stage"] == "BUY" and not isinstance(error, BankResponseUnknown):
            data["items"] = [{**item, "status": "PAUSED"} if item["status"] == "SUBMITTING" else item for item in data["items"]]
        row.data = {**data, "last_error": {"code": error.code, "message": error.message}}
        row.status = "PAUSED"
        await db.commit()
    return await progress(db, user_id, plan_id, bank)


async def adopt(db: AsyncSession, user_id: str, plan_id: str, request: AdoptPlan) -> dict:
    """部分采用或减少金额/份额产生新版本，拒绝变相增加授权范围。"""
    await lock_profile(db, user_id)
    from .wealth_task import require_plan_edit, register_plan
    task=await require_plan_edit(db,user_id,plan_id)
    row = await owned_plan(db, user_id, plan_id)
    if row.version != request.version or row.status != "AWAITING_CONFIRMATION" or any(
        child["status"] != "AWAITING_CONFIRMATION" for child in row.data["items"]):
        raise WealthPolicyError("仅未受理当前方案可部分采用", "STALE_PLAN")
    if row.data.get('manual_redemption') or row.data.get('manual_purchase'):
        raise WealthPolicyError('请返回所选产品重新填写数量并统一核对', 'EXPLICIT_INPUT_REQUIRED', 409)
    if row.data.get('portfolio_id'):
        raise WealthPolicyError('整体目标必须经配置服务修订，再重新准备差额', 'PORTFOLIO_REVISION_REQUIRED', 409)
    previous = {child["command"]["operation_id"]: child for child in row.data["items"]}
    if len({item.operation_id for item in request.items}) != len(request.items):
        raise WealthPolicyError("选项不能重复", "INVALID_PLAN", 422)
    children = []
    for selected in request.items:
        if selected.operation_id not in previous:
            raise WealthPolicyError("选项不属于当前方案", "INVALID_PLAN", 422)
        child = previous[selected.operation_id]
        cmd = InvestmentCommand.model_validate(child["command"])
        if selected.amount_fen is not None:
            if cmd.direction != "purchase" or selected.amount_fen > cmd.amount_fen:
                raise WealthPolicyError("部分采用只能减少当前申购金额", "INVALID_PLAN", 422)
            cmd.amount_fen = selected.amount_fen
        if selected.units is not None:
            if cmd.direction != "redeem" or units(selected.units) > units(cmd.units):
                raise WealthPolicyError("部分采用只能减少当前赎回份额", "INVALID_PLAN", 422)
            cmd.units = format(units(selected.units), ".4f")
        cmd.operation_id, cmd.plan_version = f"WO{uuid4().hex}", row.version+1
        children.append({**child, "command": cmd.model_dump(), "status": "AWAITING_CONFIRMATION"})
    data = dict(row.data)
    data["items"] = children
    if data["stage"] == "BUY":
        spent = sum(child["command"]["amount_fen"] for child in children)
        pricing = data['configuration'].get('pricing_snapshot', {})
        fees = 0
        for child in children:
            command = child['command']; price = pricing.get(command['product_id'])
            if price is None or price['product_version'] != command['product_version']:
                fees = None
                break
            fees += purchase(command['amount_fen'], price['nav'], price['purchase_fee_rate']).fee_fen
        cash = next(a['available_fen'] for a in data['snapshot']['cash'] if a['account_id'] == data['request']['account_id'])
        data["configuration"] = {**data["configuration"], "investment_fen": spent,
            "retained_cash_fen": data["request"]["budget_fen"]-spent,
            "unallocated_fen": data["request"]["budget_fen"]-spent,
            "estimated_fee_fen": fees, "account_remaining_cash_fen": cash-spent}
    row.data, row.version = data, row.version+1
    row.status = "AWAITING_CONFIRMATION" if children else "COMPLETED"
    await db.commit()
    if task:
        await register_plan(db,user_id,task.session_id,task.context_id,plan_view(row))
    return plan_view(row)
