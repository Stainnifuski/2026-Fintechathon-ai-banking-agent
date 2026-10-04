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
from ..domain.wealth_requirements import SemanticFrame


router = APIRouter(prefix="/api/v1/wealth", tags=["wealth"])


def get_wealth_bank() -> HttpWealthBankApi:
    """可替换HTTP客户端用于隔离集成测试。"""
    return HttpWealthBankApi()


@router.post('/sessions')
async def new_session(user:dict=Depends(get_current_user),db:AsyncSession=Depends(get_db),
                      bank:HttpWealthBankApi=Depends(get_wealth_bank)):
    from ..services.orchestration import wealth_lifecycle
    try:
        context=(await bank.call('POST','/contexts',{'subject_user_id':user['user_id']}))['selected']
        await wealth_lifecycle.expire(db)
        return await wealth_lifecycle.start(db,user['user_id'],context)
    except (BankApiError,policy.WealthPolicyError) as error:await db.rollback();fail(error)


@router.post('/sessions/{session_id}/close')
async def close_session(session_id:str,user:dict=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    from ..services.orchestration.wealth_lifecycle import close
    try:return await close(db,user['user_id'],session_id)
    except policy.WealthPolicyError as error:await db.rollback();fail(error)


@router.get('/sessions/{session_id}')
async def current_session(session_id:str,user:dict=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    from ..models.wealth import WealthConversationORM
    row=await db.get(WealthConversationORM,session_id)
    if not row or row.user_id!=user['user_id'] or row.data.get('closed'):
        raise HTTPException(404,detail={'code':'NOT_FOUND'})
    return {'session_id':row.session_id,'version':row.version,**row.data}


class ContinueTask(StrictRequest):
    task_id:str=Field(min_length=1,max_length=64)


@router.post('/sessions/{session_id}/continue')
async def continue_current(session_id:str,request:ContinueTask,user:dict=Depends(get_current_user),
        db:AsyncSession=Depends(get_db),bank:HttpWealthBankApi=Depends(get_wealth_bank)):
    from ..models.wealth import WealthConversationORM
    from ..services.orchestration import wealth_task
    facts=await bank.assets(user['user_id'])
    task=await wealth_task.owned(db,user['user_id'],request.task_id)
    row=await db.get(WealthConversationORM,session_id)
    if (not row or row.user_id!=user['user_id'] or row.data.get('closed') or
        row.data.get('context_id')!=task.context_id or task.status in wealth_task.TERMINAL):
        raise HTTPException(409,detail={'code':'STALE_TASK','message':'没有可续接的当前任务'})
    if row.data.get('active_task'):raise HTTPException(409,detail={'code':'ACTIVE_TASK_SELECTION_REQUIRED'})
    row.data={**row.data,'active_task':{'task_id':task.task_id,'version':task.version},
        'requirements':task.data.get('requirements'),'planning_task_id':task.task_id}
    task.session_id=session_id;row.version+=1
    await db.commit()
    return {'kind':'task','message':'已核对原任务进度。后续交易须重新读现金、预览及本人确认授权。',
        'session_id':row.session_id,'version':row.version,'task':await wealth_task.view(db,task),'assets':facts,
        'requirement_card':task.data.get('requirements')}


class RequirementEdit(StrictRequest):
    version:int=Field(ge=1,strict=True)
    client_message_id:str=Field(min_length=8,max_length=64)
    semantic:SemanticFrame


class WindowPreview(StrictRequest):
    authorization_id:str=Field(min_length=1,max_length=64)


@router.post('/sessions/{session_id}/preview-link')
async def link_window_preview(session_id:str,request:WindowPreview,user:dict=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    from ..models.wealth import WealthConversationORM
    await policy.lock_profile(db,user['user_id'])
    row=await db.get(WealthConversationORM,session_id)
    if not row or row.user_id!=user['user_id'] or row.data.get('closed'):raise HTTPException(409,detail={'code':'SESSION_CLOSED'})
    auth=await policy.owned_authorization(db,user['user_id'],request.authorization_id)
    row.data={**row.data,'preview_ids':list(dict.fromkeys([*row.data.get('preview_ids',[]),auth.authorization_id]))}
    await db.commit();return {'linked':True}


@router.post('/sessions/{session_id}/requirements')
async def edit_requirements(session_id:str,request:RequirementEdit,user:dict=Depends(get_current_user),
        db:AsyncSession=Depends(get_db),bank:HttpWealthBankApi=Depends(get_wealth_bank)):
    from ..services.ai.wealth_dialogue import message as respond
    from ..services.ai.wealth_model import Intent
    text='；'.join(p.quote for p in request.semantic.patches) or '更新建议'
    try:return await respond(db,user['user_id'],bank,text,session_id,request.version,
        request.client_message_id,parsed_intent=Intent(intent='allocate',semantic=request.semantic))
    except (BankApiError,policy.WealthPolicyError,ValueError) as error:
        await db.rollback()
        if isinstance(error,ValueError):raise HTTPException(422,detail={'code':'INVALID_REQUIREMENTS','message':str(error)})
        fail(error)


@router.post('/sessions/{session_id}/dirty')
async def dirty_requirements(session_id:str,user:dict=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    from ..models.wealth import WealthConversationORM,WealthPlanORM
    await policy.lock_profile(db,user['user_id'])
    row=await db.get(WealthConversationORM,session_id,populate_existing=True)
    if not row or row.user_id!=user['user_id'] or row.data.get('closed'):
        raise HTTPException(409,detail={'code':'SESSION_CLOSED','message':'请开始新对话'})
    from ..services.orchestration.wealth_lifecycle import invalidate
    await invalidate(db,user['user_id'],row.data)
    return {'dirty':True}



def fail(error: BankApiError | policy.WealthPolicyError) -> None:
    """错误传递保留事实未知语义。"""
    code = "RESULT_UNKNOWN" if isinstance(error, BankResponseUnknown) else error.code
    raise HTTPException(error.status_code, detail={"code": code, "message": error.message}) from error


@router.get('/products/{product_id}/detail')
async def product_detail(product_id:str,period:str='1y',user:dict=Depends(get_current_user),bank:HttpWealthBankApi=Depends(get_wealth_bank)):
    if period not in {'1m','3m','1y'}:raise HTTPException(422,detail={'code':'INVALID_CHART_RANGE'})
    try:return await bank.call('POST','/product-detail',{'subject_user_id':user['user_id'],'product_id':product_id,'period':period})
    except BankApiError as error:fail(error)


from ..schemas.wealth_demo import DemoReset, DemoAdjust, DemoDeletePreview, DemoDelete


@router.get('/demo/current')
async def demo_current(user: dict=Depends(get_current_user), bank: HttpWealthBankApi=Depends(get_wealth_bank)) -> dict:
    try: return await bank.call('POST', '/demo/current', {'subject_user_id': user['user_id']})
    except BankApiError as error: fail(error)


@router.get('/demo/archives')
async def demo_archives(user: dict=Depends(get_current_user), bank: HttpWealthBankApi=Depends(get_wealth_bank)) -> dict:
    try: return await bank.call('POST', '/demo/archives', {'subject_user_id': user['user_id']})
    except BankApiError as error: fail(error)


async def demo_call(action: str, request: StrictRequest, user: dict, bank: HttpWealthBankApi) -> dict:
    try: return await bank.call('POST', '/demo/'+action, {**request.model_dump(), 'subject_user_id': user['user_id']})
    except BankApiError as error: fail(error)


@router.post('/demo/reset')
async def demo_reset(request: DemoReset, user: dict=Depends(get_current_user), bank: HttpWealthBankApi=Depends(get_wealth_bank)) -> dict:
    return await demo_call('reset', request, user, bank)


@router.post('/demo/adjust')
async def demo_adjust(request: DemoAdjust, user: dict=Depends(get_current_user), bank: HttpWealthBankApi=Depends(get_wealth_bank)) -> dict:
    return await demo_call('adjust', request, user, bank)


@router.post('/demo/delete-preview')
async def demo_delete_preview(request: DemoDeletePreview, user: dict=Depends(get_current_user), bank: HttpWealthBankApi=Depends(get_wealth_bank)) -> dict:
    return await demo_call('delete-preview', request, user, bank)


@router.post('/demo/delete')
async def demo_delete(request: DemoDelete, user: dict=Depends(get_current_user), bank: HttpWealthBankApi=Depends(get_wealth_bank)) -> dict:
    return await demo_call('delete', request, user, bank)


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
    codes: list[str] = Field(min_length=2, max_length=4)
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
    """2至4个唯一产品客观对比，全部字段来自银行。"""
    keys = product_ids.split(",")
    if not 2 <= len(keys) <= 4 or len(set(keys)) != len(keys):
        raise HTTPException(422, detail={"code": "INVALID_COMPARISON", "message": "请选择2至4个不同产品"})
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
        row = await policy.new_preview_authorization(db, user["user_id"], InvestmentCommand.model_validate(view["command"]))
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


@router.get("/orders/{operation_id}/cancellation-eligibility")
async def cancellation_eligibility(operation_id: str, user: dict = Depends(get_current_user),
                                  bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> dict:
    try:
        return await bank.call("POST", f"/orders/{operation_id}/cancellation-eligibility", {"subject_user_id":user["user_id"]})
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
async def plans(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db),
                bank: HttpWealthBankApi = Depends(get_wealth_bank)) -> list[dict]:
    """刷新后找回原版本方案、在途和已完成效果。"""
    from ..models.wealth import WealthPlanORM
    rows = (await db.scalars(select(WealthPlanORM).where(WealthPlanORM.user_id == user["user_id"])
                              .order_by(WealthPlanORM.created_at.desc()))).all()
    try:
        facts = await bank.assets(user['user_id'])
    except BankApiError as error: fail(error)
    account_ids = {account['account_id'] for account in facts['cash']}
    return [wealth_plan.plan_view(row) for row in rows if row.data.get('request',{}).get('account_id') in account_ids]


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


@router.get('/messages/history')
async def message_history(user:dict=Depends(get_current_user)):
    """Deprecated: workspace never restores historical conversations."""
    return None


@router.get('/messages/archives')
async def message_archives(user:dict=Depends(get_current_user)):
    return {'items':[]}


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
