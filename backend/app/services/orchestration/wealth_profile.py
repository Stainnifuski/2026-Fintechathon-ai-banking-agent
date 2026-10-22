"""模拟财务画像与目标标签；外部资产不会进入银行执行命令。"""
from ..audit.spans import audited

from uuid import uuid4
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...integrations.wealth_authorization import lock_profile, WealthPolicyError
from ...integrations.wealth_bank_api import HttpWealthBankApi
from ...models.wealth import WealthGoalORM, WealthProfileORM
from ...schemas.wealth import ProfileUpdate, GoalInput


async def read(db: AsyncSession, user_id: str) -> dict:
    """本人受控数据，不向模型发送画像原始值。"""
    profile = await db.get(WealthProfileORM, user_id)
    goals = (await db.scalars(select(WealthGoalORM).where(WealthGoalORM.user_id == user_id))).all()
    return {"version": profile.version if profile else 1, "data": profile.data if profile else {},
        "goals": [{"goal_id": goal.goal_id, "version": goal.version, **goal.data} for goal in goals],
        "notice": "请只输入虚构数据；外部资产不属于模拟银行可交易资金"}


@audited("wealth")
async def update_profile(db: AsyncSession, user_id: str, request: ProfileUpdate) -> dict:
    """关键财务信息改版使既有评估失效，不能仅调整User.risk_level。"""
    if len({asset.asset_id for asset in request.external_assets}) != len(request.external_assets):
        raise WealthPolicyError("外部模拟资产编号不能重复", "INVALID_EXTERNAL_ASSET", 422)
    row = await lock_profile(db, user_id)
    if row.version != request.version:
        raise WealthPolicyError("财务画像已更新", "STALE_PLAN")
    payload = request.model_dump(exclude={"version"})
    if payload != {key: value for key, value in row.data.items() if key != "updated_at"}:
        # 避免修改外部资产后目标标签超分配。
        goals = (await db.scalars(select(WealthGoalORM).where(WealthGoalORM.user_id == user_id))).all()
        totals = {}
        for goal in goals:
            for allocation in goal.data["allocations"]:
                ref = allocation["asset_ref"]
                totals[ref] = totals.get(ref, 0)+allocation["amount_fen"]
        available = {f"EXT:{asset.asset_id}": asset.amount_fen for asset in request.external_assets}
        if any(amount > available.get(ref, 0) for ref, amount in totals.items() if ref.startswith("EXT:")):
            raise WealthPolicyError("修改会使外部资产目标标签超出资产，请先调整目标", "GOAL_ALLOCATION_CONFLICT")
        row.version += 1
        row.data = {**payload, "updated_at": datetime.now(timezone.utc).isoformat()}
    await db.commit()
    return await read(db, user_id)


@audited("wealth")
async def save_goal(db: AsyncSession, user_id: str, request: GoalInput, bank: HttpWealthBankApi) -> dict:
    """目标金额与用途独立；同资产多目标不能重复超分配。"""
    facts = await bank.assets(user_id)
    await lock_profile(db, user_id)
    profile = await db.get(WealthProfileORM, user_id)
    sources = {f"CASH:{a['account_id']}": a["available_fen"] for a in facts["cash"] if a["account_type"] != "credit"}
    sources.update({f"LOT:{lot['lot_id']}": lot["market_value_fen"] or 0 for lot in facts["lots"]})
    sources.update({f"EXT:{asset['asset_id']}": asset["amount_fen"] for asset in profile.data.get("external_assets", [])})
    goals = (await db.scalars(select(WealthGoalORM).where(WealthGoalORM.user_id == user_id))).all()
    target = next((goal for goal in goals if goal.goal_id == request.goal_id), None)
    if request.goal_id and (target is None or target.version != request.version):
        raise WealthPolicyError("目标不存在或已更新", "STALE_PLAN")
    totals = {}
    for goal in goals:
        if goal is not target:
            for item in goal.data["allocations"]:
                ref = item["asset_ref"]
                totals[ref] = totals.get(ref, 0)+item["amount_fen"]
    for item in request.allocations:
        totals[item.asset_ref] = totals.get(item.asset_ref, 0)+item.amount_fen
    if any(ref not in sources or amount > sources[ref] for ref, amount in totals.items()):
        raise WealthPolicyError("目标分配超过可用资产或引用不属于本人", "GOAL_ALLOCATION_CONFLICT")
    if sum(item.amount_fen for item in request.allocations) > request.amount_fen:
        raise WealthPolicyError("分配标签超过目标金额", "GOAL_ALLOCATION_CONFLICT")
    payload = request.model_dump(exclude={"goal_id", "version"}, mode="json")
    if target:
        target.version += 1
        target.data = payload
    else:
        db.add(WealthGoalORM(goal_id=f"WG{uuid4().hex}", user_id=user_id, version=1, data=payload))
    await db.commit()
    return await read(db, user_id)
