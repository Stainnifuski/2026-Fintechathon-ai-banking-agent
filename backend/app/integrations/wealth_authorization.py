"""3号理财授权策略；不访问账户、持仓等银行ORM。"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo
import hashlib

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.wealth_command import command_digest
from ..domain.wealth_risk import QUESTIONNAIRE_VERSION, score
from ..models.wealth import WealthAssessmentORM, WealthAuthorizationORM, WealthProfileORM, WealthPlanORM, WealthGoalORM
from ..models.audit_log import AuditLogORM
from ..schemas.wealth import AssessmentSubmission, InvestmentCommand, PersonalConfirmation


# Normal service default; only the dedicated test server may override in memory.
ASSESSMENT_DAILY_LIMIT = 2
ASSESSMENT_ROLLING_YEAR_LIMIT = 8


class WealthPolicyError(Exception):
    """适当性与授权失败，拒绝执行，保留可恢复意图。"""
    def __init__(self, message: str, code: str, status_code: int = 409) -> None:
        self.message, self.code, self.status_code = message, code, status_code
        super().__init__(message)


def aware(value: datetime) -> datetime:
    """SQLite无时区值按UTC存储契约解释。"""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def confirmation_audit(db: AsyncSession, row: WealthAuthorizationORM) -> None:
    """3号逐笔本人确认审计，无问卷答案、验证码或隐藏思维链。"""
    db.add(AuditLogORM(audit_id=hashlib.sha256(f"wealth-consent:{row.authorization_id}".encode()).hexdigest(),
        session_id=row.operation_id, user_id=row.user_id, intent="wealth_consent", permission_level="red",
        action="investment_personal_confirmation", params={"operation_id": row.operation_id,
            "authorization_id": row.authorization_id, "digest": row.param_digest,
            "product_version": row.command["product_version"], "disclosure_version": row.command["disclosure_version"],
            "plan_version": row.command["plan_version"]}, user_confirmed=True,
        result="success", decision_chain=["authenticated_subject", "explicit_unchecked_by_default_component",
                                          "order_and_disclosure_digest_bound", "strong_verification_still_required"]))


async def lock_profile(db: AsyncSession, user_id: str) -> WealthProfileORM:
    """用户级串行化用于重评限次、画像版本和确认。"""
    # INSERT ON CONFLICT避免首次访问并发创建竞态。
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
    await db.execute(insert(WealthProfileORM).values(user_id=user_id, version=1, data={})
                     .on_conflict_do_nothing(index_elements=["user_id"]))
    if db.get_bind().dialect.name == "sqlite":
        await db.execute(update(WealthProfileORM).where(WealthProfileORM.user_id == user_id)
                         .values(version=WealthProfileORM.version,updated_at=WealthProfileORM.updated_at))
    return (await db.scalars(select(WealthProfileORM).where(
        WealthProfileORM.user_id == user_id).with_for_update().execution_options(populate_existing=True))).one()


async def submit_assessment(db: AsyncSession, user_id: str, request: AssessmentSubmission) -> dict:
    """本人提交版本化问卷；不把旧User.risk_level当作有效评估。"""
    profile = await lock_profile(db, user_id)
    if request.questionnaire_version != QUESTIONNAIRE_VERSION or request.profile_version != profile.version:
        raise WealthPolicyError("问卷或财务画像已更新，请重新读取", "STALE_PLAN")
    now = datetime.now(timezone.utc)
    prior = list((await db.scalars(select(WealthAssessmentORM).where(
        WealthAssessmentORM.user_id == user_id,
        WealthAssessmentORM.created_at >= now - timedelta(days=365)))).all())
    day = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    daily = sum(aware(row.created_at).astimezone(ZoneInfo("Asia/Shanghai")).date() == day for row in prior)
    if ASSESSMENT_ROLLING_YEAR_LIMIT > 0 and len(prior) >= ASSESSMENT_ROLLING_YEAR_LIMIT:
        raise WealthPolicyError(f"达到模拟重评次数限制：滚动12个月最多{ASSESSMENT_ROLLING_YEAR_LIMIT}次", "ASSESSMENT_LIMIT_REACHED")
    if ASSESSMENT_DAILY_LIMIT > 0 and daily >= ASSESSMENT_DAILY_LIMIT:
        raise WealthPolicyError(f"达到模拟重评次数限制：单日最多{ASSESSMENT_DAILY_LIMIT}次", "ASSESSMENT_LIMIT_REACHED")
    try:
        result = score(request.answers, adult=request.adult, full_capacity=request.full_capacity)
    except ValueError as error:
        raise WealthPolicyError(str(error), "ASSESSMENT_CLARIFICATION_REQUIRED", 422) from error
    row = WealthAssessmentORM(assessment_id=f"WA{uuid4().hex}", user_id=user_id,
        profile_version=profile.version, questionnaire_version=QUESTIONNAIRE_VERSION,
        score_version=result["score_version"], answers=request.answers, result=result,
        created_at=now, expires_at=now+timedelta(days=365))
    db.add(row)
    db.add(AuditLogORM(audit_id=f"WASSESS{uuid4().hex}", session_id=row.assessment_id, user_id=user_id,
        intent="wealth_assessment", permission_level="yellow", action="investment_assessment_submit",
        params={"assessment_id": row.assessment_id, "profile_version": profile.version,
            "questionnaire_version": QUESTIONNAIRE_VERSION, "score_version": result["score_version"],
            "capacity_level": result["capacity_level"], "daily_limit": ASSESSMENT_DAILY_LIMIT or None, "rolling_year_limit": ASSESSMENT_ROLLING_YEAR_LIMIT or None}, user_confirmed=True, result="success",
        decision_chain=["authenticated_subject", "personal_answers", "deterministic_scoring", "rate_limit_checked"]))
    await db.commit()
    return assessment_view(row)


def assessment_view(row: WealthAssessmentORM) -> dict:
    """向本人返回结果，不返回原始问卷答案。"""
    return {"assessment_id": row.assessment_id, **row.result,
            "profile_version": row.profile_version, "created_at": aware(row.created_at).isoformat(),
            "expires_at": aware(row.expires_at).isoformat(),
            "confirmed": row.confirmed_at is not None}


async def latest_assessment(db: AsyncSession, user_id: str) -> WealthAssessmentORM | None:
    """按创建时间和ID稳定读取最新评估，包括尚未确认的结果。"""
    return (await db.scalars(select(WealthAssessmentORM).where(WealthAssessmentORM.user_id == user_id)
        .order_by(WealthAssessmentORM.created_at.desc(), WealthAssessmentORM.assessment_id.desc()))).first()


async def confirm_assessment(db: AsyncSession, user_id: str, assessment_id: str) -> dict:
    """本人确认最新评估结果，旧结果不能覆盖新回答。"""
    await lock_profile(db, user_id)
    row = await latest_assessment(db, user_id)
    if row is None or row.assessment_id != assessment_id:
        raise WealthPolicyError("评估不存在或已更新", "STALE_PLAN", 404)
    if row.confirmed_at is None:
        row.confirmed_at = datetime.now(timezone.utc)
        db.add(AuditLogORM(audit_id=f"WACONF{uuid4().hex}", session_id=row.assessment_id, user_id=user_id,
            intent="wealth_assessment", permission_level="yellow", action="investment_assessment_confirm",
            params={"assessment_id": row.assessment_id, "profile_version": row.profile_version},
            user_confirmed=True, result="success", decision_chain=["authenticated_subject", "latest_assessment", "personal_confirmation"]))
    await db.commit()
    return assessment_view(row)


async def require_suitability(db: AsyncSession, user_id: str, product_risk: int, *, allow_cash_only: bool = False) -> dict:
    """服务端保守硬匹配；强验证不能覆盖此限制。"""
    row = await latest_assessment(db, user_id)
    profile = await db.get(WealthProfileORM, user_id)
    if row is None or row.confirmed_at is None:
        raise WealthPolicyError("请本人完成并确认风险评估", "RISK_ASSESSMENT_REQUIRED")
    if aware(row.expires_at) <= datetime.now(timezone.utc) or profile is None or row.profile_version != profile.version:
        raise WealthPolicyError("风险评估已过期或财务条件已变化", "RISK_ASSESSMENT_EXPIRED")
    if not allow_cash_only and (not row.result["purchase_allowed"] or product_risk > row.result["capacity_level"]):
        raise WealthPolicyError("产品风险或支付条件不匹配，可保留现金", "SUITABILITY_MISMATCH")
    return {**row.result, "assessment_id": row.assessment_id, "expires_at": aware(row.expires_at).isoformat()}


async def require_payment_capacity(db: AsyncSession, user_id: str, available_fen: int, amount_fen: int) -> None:
    """已申报近期支出必须保留；未知支出不伪造估算，画像改变使评估失效。"""
    profile = await db.get(WealthProfileORM, user_id)
    reserve = profile.data.get("monthly_expenses_fen", 0) if profile else 0
    if amount_fen > max(0, available_fen-reserve):
        raise WealthPolicyError("申购后不足以保留本人已申报的一月支出，请降低金额或保留现金", "PAYMENT_CAPACITY_MISMATCH")


async def require_current_plan(db: AsyncSession, user_id: str, command: InvestmentCommand) -> None:
    """计划改版、暂停、撤销时，旧卡片及授权不能继续执行。"""
    if command.plan_id:
        plan = await db.get(WealthPlanORM, command.plan_id)
        if (plan is None or plan.user_id != user_id or plan.version != command.plan_version
                or plan.status not in {"AWAITING_CONFIRMATION", "EXECUTING", "WAITING_SETTLEMENT"}):
            raise WealthPolicyError("计划已更新或不可执行", "STALE_PLAN")
        child = next((item for item in plan.data["items"]
            if item["command"]["operation_id"] == command.operation_id), None)
        if child is None or command_digest(InvestmentCommand.model_validate(child["command"])) != command_digest(command):
            raise WealthPolicyError("订单不属于当前方案清单", "STALE_PLAN")
        profile = await db.get(WealthProfileORM, user_id)
        goals = (await db.scalars(select(WealthGoalORM).where(WealthGoalORM.user_id == user_id))).all()
        if profile.version != plan.data["profile_version"] or {g.goal_id: g.version for g in goals} != plan.data["goal_versions"]:
            raise WealthPolicyError("财务画像或目标已改变，请重新生成方案", "STALE_PLAN")


async def new_preview_authorization(db: AsyncSession, user_id: str, command: InvestmentCommand) -> WealthAuthorizationORM:
    """将预览完整参数固定为逐笔授权；同号异参拒绝。"""
    await lock_profile(db, user_id)
    await require_current_plan(db, user_id, command)
    existing = (await db.scalars(select(WealthAuthorizationORM).where(
        WealthAuthorizationORM.operation_id == command.operation_id))).first()
    digest = command_digest(command)
    if existing:
        if existing.user_id != user_id or existing.param_digest != digest:
            raise WealthPolicyError("原操作号参数冲突", "IDEMPOTENCY_CONFLICT")
        return existing
    now = datetime.now(timezone.utc)
    row = WealthAuthorizationORM(authorization_id=f"WAuth{uuid4().hex}", user_id=user_id,
        operation_id=command.operation_id, param_digest=digest, command=command.model_dump(),
        status="AWAITING_CONFIRMATION", created_at=now, expires_at=now+timedelta(minutes=10))
    db.add(row)
    await db.flush()
    return row


async def owned_authorization(db: AsyncSession, user_id: str, authorization_id: str) -> WealthAuthorizationORM:
    """授权归属和安全有效期只使用真实时间。"""
    row = await db.get(WealthAuthorizationORM, authorization_id, with_for_update=True,
                       populate_existing=True)
    if row is None or row.user_id != user_id:
        raise WealthPolicyError("授权不存在", "NOT_FOUND", 404)
    if row.status != "CONSUMED" and aware(row.expires_at) <= datetime.now(timezone.utc):
        raise WealthPolicyError("确认已过期，请创建新的订单预览", "AUTHORIZATION_EXPIRED")
    await require_current_plan(db, user_id, InvestmentCommand.model_validate(row.command))
    if row.status=='EXPIRED':
        raise WealthPolicyError('旧预览已失效，请核对最新参数重新预览','AUTHORIZATION_EXPIRED',409)
    return row


async def record_confirmation(db: AsyncSession, user_id: str, authorization_id: str,
                              request: PersonalConfirmation, *, commit: bool = True) -> dict:
    """只接受可信表单的明确逐笔披露确认，默认不勾选。"""
    await lock_profile(db, user_id)
    row = await owned_authorization(db, user_id, authorization_id)
    if row.param_digest != request.digest or row.command["disclosure_version"] != request.disclosure_version:
        raise WealthPolicyError("预览或披露已改变", "RULE_VERSION_CHANGED")
    if row.status == "AWAITING_CONFIRMATION":
        row.confirmed_at = datetime.now(timezone.utc)
        row.status = "AWAITING_MFA"
        confirmation_audit(db, row)
    if commit:
        await db.commit()
    return {"authorization_id": row.authorization_id, "status": row.status}


async def validate_bank_authorization(db: AsyncSession, user_id: str, authorization_id: str,
                                     command: InvestmentCommand) -> WealthAuthorizationORM:
    """银行调用的策略接口；必须本人确认+验证、完整摘要、当前计划。"""
    from ..services.orchestration.wealth_task import require_write_control
    row = await owned_authorization(db, user_id, authorization_id)
    from ..models.wealth import WealthVerificationORM
    evidence = await db.get(WealthVerificationORM, authorization_id)
    if evidence and evidence.method == "SIMULATED_CLICK":
        raise WealthPolicyError("历史点击授权已停用，请重新预览并完成统一验证", "SIMULATED_VERIFICATION_DENIED", 403)
    if evidence is None or evidence.method not in {"TOTP", "SMS"}:
        raise WealthPolicyError("缺少统一高风险验证证据", "AUTHORIZATION_REQUIRED", 403)
    if (row.status != "VALID" or not row.confirmed_at or not row.verified_at
            or row.operation_id != command.operation_id or row.param_digest != command_digest(command)):
        raise WealthPolicyError("缺少绑定订单的本人确认和强验证", "AUTHORIZATION_REQUIRED", 403)
    await require_write_control(db, user_id, command, row)
    return row
