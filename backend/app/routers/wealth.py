"""理财可信页面入口；银行事实只通过内部HTTP获取。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..domain.wealth_risk import QUESTIONS, QUESTIONNAIRE_VERSION
from ..integrations.bank_api import BankApiError, BankResponseUnknown
from ..integrations.wealth_bank_api import HttpWealthBankApi
from ..integrations import wealth_authorization as policy
from ..middleware.auth import get_current_user
from ..models.wealth import WealthAuthorizationORM, WealthProfileORM
from ..schemas.wealth import AssessmentSubmission, InvestmentCommand, PersonalConfirmation, StrongVerification, PlanInput, PlanVersion, ProfileUpdate, GoalInput, WealthMessage, AdoptPlan, PlanConfirmation
from ..services.orchestration import wealth_plan, wealth_profile, wealth_execution
from ..schemas.wealth import WealthResearchQuery,WealthContextChange,StrictRequest
from pydantic import Field

router = APIRouter(prefix="/api/v1/wealth", tags=["wealth"])



def fail(error: BankApiError | policy.WealthPolicyError) -> None:
    """错误传递保留事实未知语义。"""
    code = "RESULT_UNKNOWN" if isinstance(error, BankResponseUnknown) else error.code
    raise HTTPException(error.status_code, detail={"code": code, "message": error.message}) from error


def get_wealth_bank() -> HttpWealthBankApi:
    """可替换HTTP客户端用于隔离集成测试。"""
    return HttpWealthBankApi()


@router.get('/contexts')
async def contexts(user: dict=Depends(get_current_user),bank: HttpWealthBankApi=Depends(get_wealth_bank)) -> dict:
    try: return await bank.call('POST','/contexts',{'subject_user_id':user['user_id']})
    except BankApiError as error: fail(error)


@router.post('/contexts/select')
async def select_context(request: WealthContextChange,user: dict=Depends(get_current_user),bank: HttpWealthBankApi=Depends(get_wealth_bank)) -> dict:
    try: return await bank.call('POST','/contexts/select',{**request.model_dump(),'subject_user_id':user['user_id']})
    except BankApiError as error: fail(error)


@router.post('/research')
async def research(request: WealthResearchQuery,user: dict=Depends(get_current_user),bank: HttpWealthBankApi=Depends(get_wealth_bank)) -> dict:
    try: return await bank.call('POST','/research',{**request.model_dump(),'subject_user_id':user['user_id']})
    except BankApiError as error: fail(error)


class FundSearchRequest(StrictRequest):
    query: str = Field(default='', max_length=200)
    fund_type: str | None = Field(default=None, max_length=80)
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=60)


class FundDetailRequest(StrictRequest):
    code: str = Field(pattern=r'^\d{6}$')
    fetch_missing: bool = True


class FundCompareRequest(StrictRequest):
    codes: list[str] = Field(min_length=2, max_length=3)
    period: str = Field(default='1y', pattern='^(1m|3m|1y|since)$')


@router.post('/funds/compare')
async def fund_compare(request: FundCompareRequest, user: dict=Depends(get_current_user), bank: HttpWealthBankApi=Depends(get_wealth_bank)) -> dict:
    try: return await bank.call('POST', '/funds/compare', {**request.model_dump(), 'subject_user_id':user['user_id']})
    except BankApiError as error: fail(error)


@router.post('/funds/search')
async def fund_search(request: FundSearchRequest, user: dict=Depends(get_current_user), bank: HttpWealthBankApi=Depends(get_wealth_bank)) -> dict:
    try: return await bank.call('POST', '/funds/search', {**request.model_dump(), 'subject_user_id': user['user_id']})
    except BankApiError as error: fail(error)


@router.post('/funds/detail')
async def fund_detail(request: FundDetailRequest, user: dict=Depends(get_current_user), bank: HttpWealthBankApi=Depends(get_wealth_bank)) -> dict:
    try: return await bank.call('POST', '/funds/detail', {**request.model_dump(), 'subject_user_id': user['user_id']})
    except BankApiError as error: fail(error)



@router.get("/verification-mode")
async def verification_mode(user: dict = Depends(get_current_user), bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    try:
        return await bank.call("GET", "/verification-mode")
    except BankApiError as error:
        fail(error)


@router.post("/previews/{authorization_id}/verify/demo")
async def demo_verify(authorization_id: str, user: dict = Depends(get_current_user), bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    try:
        return await bank.call("POST", "/verify/demo", {"subject_user_id": user["user_id"], "authorization_ids": [authorization_id]})
    except BankApiError as error:
        fail(error)


@router.post("/plans/{plan_id}/preview")
async def preview_plan(plan_id: str, request: PlanVersion, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db), bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    try:
        return await wealth_plan.preview_all(db, user["user_id"], plan_id, request.version, bank)
    except (BankApiError, policy.WealthPolicyError) as error:
        await db.rollback()
        fail(error)


@router.post("/plans/{plan_id}/confirm")
async def confirm_plan(plan_id: str, request: PlanConfirmation, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db), bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    try:
        return await wealth_plan.confirm_all(db, user["user_id"], plan_id, request, bank)
    except (BankApiError, policy.WealthPolicyError) as error:
        await db.rollback()
        fail(error)


@router.post("/plans/{plan_id}/verify/demo")
async def demo_verify_plan(plan_id: str, request: PlanVersion, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db), bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    try:
        return await wealth_plan.demo_verify_all(db, user["user_id"], plan_id, request.version, bank)
    except (BankApiError, policy.WealthPolicyError) as error:
        await db.rollback()
        fail(error)


@router.get("/products")
async def products(user: dict = Depends(get_current_user), bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> list[dict]:
    """鉴权客观产品探索。"""
    try:
        return await bank.products(user["user_id"])
    except BankApiError as error:
        fail(error)


@router.get("/compare")
async def compare(product_ids: str, user: dict = Depends(get_current_user),
                  bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> list[dict]:
    """2至3个唯一产品客观对比，全部字段来自银行。"""
    keys = product_ids.split(",")
    if not 2 <= len(keys) <= 3 or len(set(keys)) != len(keys):
        raise HTTPException(422, detail={"code": "INVALID_COMPARISON", "message": "请选择2至3个不同产品"})
    try:
        rows = [row for row in await bank.products(user["user_id"]) if row["product_id"] in keys]
        if len(rows) != len(keys):
            raise HTTPException(404, detail={"code": "PRODUCT_UNAVAILABLE", "message": "部分产品不存在"})
        return rows
    except BankApiError as error:
        fail(error)


@router.get("/questionnaire")
async def questionnaire(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    """本人独立回答；AI不能提交问卷或选择答案。"""
    profile = await db.get(WealthProfileORM, user["user_id"])
    return {"version": QUESTIONNAIRE_VERSION, "profile_version": profile.version if profile else 1,
        "questions": [{"id": key, "title": title, "options": options} for key, title, options in QUESTIONS],
        "notice": "比赛模拟量表，未经专业效度验证；请本人回答，日2次/滚动12个月8次"}


@router.post("/assessments")
async def assess(request: AssessmentSubmission, user: dict = Depends(get_current_user),
                 db: AsyncSession = Depends(get_db)) -> dict:
    """版本化评分与有效期。"""
    try:
        return await policy.submit_assessment(db, user["user_id"], request)
    except policy.WealthPolicyError as error:
        await db.rollback()
        fail(error)


@router.get("/assessments/current")
async def assessment(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict | None:
    """本人最新评估，不返回敏感原始答案。"""
    row = await policy.latest_assessment(db, user["user_id"])
    return policy.assessment_view(row) if row else None


@router.post("/assessments/{assessment_id}/confirm")
async def confirm_assessment(assessment_id: str, user: dict = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)) -> dict:
    """明确确认最新结果。"""
    try:
        return await policy.confirm_assessment(db, user["user_id"], assessment_id)
    except policy.WealthPolicyError as error:
        await db.rollback()
        fail(error)


@router.get("/assets")
async def assets(user: dict = Depends(get_current_user), bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    """现金、占款、持仓批次、应收、订单和亏损。"""
    try:
        return await bank.assets(user["user_id"])
    except BankApiError as error:
        fail(error)


@router.post("/previews")
async def preview(request: InvestmentCommand, user: dict = Depends(get_current_user),
                  db: AsyncSession = Depends(get_db), bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    """银行预检通过后固定逐笔授权，不扣款。"""
    try:
        view = await bank.precheck(user["user_id"], request)
        row = await policy.new_preview_authorization(db, user["user_id"], request)
        await db.commit()
        return {**view, "authorization_id": row.authorization_id, "authorization_status": row.status,
                "expires_at": policy.aware(row.expires_at).isoformat()}
    except (BankApiError, policy.WealthPolicyError) as error:
        await db.rollback()
        fail(error)


@router.get("/previews")
async def previews(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    """刷新找回本人未消费预览；老卡仍由服务端再次验证。"""
    rows = (await db.scalars(select(WealthAuthorizationORM).where(
        WealthAuthorizationORM.user_id == user["user_id"], WealthAuthorizationORM.status != "CONSUMED")
        .order_by(WealthAuthorizationORM.created_at.desc()))).all()
    return [{"authorization_id": row.authorization_id, "command": row.command,
        "digest": row.param_digest, "status": row.status, "expires_at": policy.aware(row.expires_at).isoformat()} for row in rows]


@router.post("/previews/{authorization_id}/confirm")
async def confirm(authorization_id: str, request: PersonalConfirmation, user: dict = Depends(get_current_user),
                  db: AsyncSession = Depends(get_db), bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    """聊天好的不能调用；必须明确绑定预览和披露版本。"""
    try:
        row = await policy.owned_authorization(db, user["user_id"], authorization_id)
        command = InvestmentCommand.model_validate(row.command)
        await db.rollback()
        await bank.precheck(user["user_id"], command)
        return await policy.record_confirmation(db, user["user_id"], authorization_id, request)
    except (BankApiError, policy.WealthPolicyError) as error:
        await db.rollback()
        fail(error)


@router.post("/previews/{authorization_id}/verify")
async def verify(authorization_id: str, request: StrongVerification, user: dict = Depends(get_current_user),
                 bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    """验证码只进入可信验证组件和银行，不进入模型消息。"""
    try:
        return await bank.call("POST", "/verify", {"subject_user_id": user["user_id"],
            "authorization_id": authorization_id, "proof": request.proof})
    except BankApiError as error:
        fail(error)


@router.post("/previews/{authorization_id}/execute")
async def execute(authorization_id: str, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db),
                  bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    """原号查询及受理；网络失败不以成功或失败覆盖未知事实。"""
    try:
        return await wealth_execution.execute(db, user["user_id"], authorization_id, bank)
    except (BankApiError, policy.WealthPolicyError) as error:
        fail(error)


@router.get("/orders/{operation_id}")
async def order(operation_id: str, user: dict = Depends(get_current_user),
                bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    """原号恢复查询。"""
    try:
        return await bank.query(user["user_id"], operation_id)
    except BankApiError as error:
        fail(error)


@router.get("/executions")
async def executions(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    """刷新找回SUBMITTING/UNKNOWN，保留原操作号。"""
    return await wealth_execution.observations(db, user["user_id"])


@router.post("/orders/{operation_id}/cancel")
async def cancel(operation_id: str, request: PersonalConfirmation, user: dict = Depends(get_current_user),
                 bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    """撤单也需明确确认并核对当前订单摘要。"""
    try:
        current = await bank.query(user["user_id"], operation_id)
        if current["digest"] != request.digest:
            raise policy.WealthPolicyError("订单已变化", "STALE_PLAN")
        return await bank.call("POST", f"/orders/{operation_id}/cancel", {"subject_user_id": user["user_id"]})
    except (BankApiError, policy.WealthPolicyError) as error:
        fail(error)


@router.get("/plans")
async def plans(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    """刷新后找回原版本方案、在途和已完成效果。"""
    from ..models.wealth import WealthPlanORM
    rows = (await db.scalars(select(WealthPlanORM).where(WealthPlanORM.user_id == user["user_id"])
                              .order_by(WealthPlanORM.created_at.desc()))).all()
    return [wealth_plan.plan_view(row) for row in rows]


@router.post("/plans")
async def create_plan(request: PlanInput, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db),
                      bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    """新资金或存量诊断，确定性规则不直接交易。"""
    try:
        return await wealth_plan.create(db, user["user_id"], bank, request.model_dump())
    except (BankApiError, policy.WealthPolicyError, ValueError) as error:
        await db.rollback()
        if isinstance(error, ValueError):
            raise HTTPException(422, detail={"code": "INVALID_PLAN", "message": str(error)}) from error
        fail(error)


@router.put("/plans/{plan_id}")
async def edit_plan(plan_id: str, request: PlanInput, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db),
                    bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    """修改保留现金/期限/排除产品即新版本，旧确认失效。"""
    try:
        return await wealth_plan.create(db, user["user_id"], bank, request.model_dump(), plan_id)
    except (BankApiError, policy.WealthPolicyError, ValueError) as error:
        await db.rollback()
        if isinstance(error, ValueError):
            raise HTTPException(422, detail={"code": "INVALID_PLAN", "message": str(error)}) from error
        fail(error)


@router.get("/plans/{plan_id}/progress")
async def plan_progress(plan_id: str, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db),
                        bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    """继续核对在途，不假造成功或释放未知占款。"""
    try:
        return await wealth_plan.progress(db, user["user_id"], plan_id, bank)
    except (BankApiError, policy.WealthPolicyError) as error:
        await db.rollback()
        fail(error)


@router.post("/plans/{plan_id}/execute")
async def execute_plan(plan_id: str, request: PlanVersion, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db),
                       bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    """逐笔确认和验证全部满足后执行，纯购买批次原子占款。"""
    try:
        return await wealth_plan.execute(db, user["user_id"], plan_id, request.version, bank)
    except (BankApiError, policy.WealthPolicyError) as error:
        await db.rollback()
        fail(error)


@router.post("/plans/{plan_id}/adopt")
async def adopt_plan(plan_id: str, request: AdoptPlan, user: dict = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)) -> dict:
    """部分采用或降低金额，旧版本确认撤销。"""
    try:
        return await wealth_plan.adopt(db, user["user_id"], plan_id, request)
    except (policy.WealthPolicyError, ValueError) as error:
        await db.rollback()
        if isinstance(error, ValueError):
            raise HTTPException(422, detail={"code": "INVALID_PLAN", "message": str(error)}) from error
        fail(error)


@router.get("/messages/history")
async def message_history(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict | None:
    """本人最近会话刷新恢复，只返回受控模拟消息。"""
    from ..models.wealth import WealthConversationORM
    row = (await db.scalars(select(WealthConversationORM).where(WealthConversationORM.user_id == user["user_id"])
        .order_by(WealthConversationORM.updated_at.desc(), WealthConversationORM.session_id.desc()))).first()
    return {"session_id": row.session_id, "version": row.version, **row.data} if row else None


@router.get("/profile")
async def profile(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    """受控本人模拟画像与目标。"""
    return await wealth_profile.read(db, user["user_id"])


@router.put("/profile")
async def update_profile(request: ProfileUpdate, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    """财务改版重新评估，外部资产保持只读诊断。"""
    try:
        return await wealth_profile.update_profile(db, user["user_id"], request)
    except policy.WealthPolicyError as error:
        await db.rollback()
        fail(error)


@router.post("/goals")
async def goal(request: GoalInput, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db),
               bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    """跨目标资产标签不重叠；不会创建外部资金交易。"""
    try:
        return await wealth_profile.save_goal(db, user["user_id"], request, bank)
    except (BankApiError, policy.WealthPolicyError) as error:
        await db.rollback()
        fail(error)


@router.post("/messages")
async def message(request: WealthMessage, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db),
                   bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    """离线有限理解和待确认卡片，可信事实解释；不接受验证码。"""
    from ..services.ai.wealth import message as respond
    try:
        return await respond(db, user["user_id"], bank, request.message, request.session_id, request.version,request.client_message_id)
    except (BankApiError, policy.WealthPolicyError, ValueError) as error:
        await db.rollback()
        if isinstance(error, ValueError):
            raise HTTPException(422, detail={"code": "CLARIFICATION_REQUIRED", "message": str(error)}) from error
        fail(error)
