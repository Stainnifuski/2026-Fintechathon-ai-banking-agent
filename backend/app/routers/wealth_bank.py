"""4号理财内部HTTP边界；管理员能力不暴露给AI工具。"""

import os
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db, DATABASE_URL
from ..domain.wealth import CALENDAR_VERSION
from ..domain.wealth_limits import MAX_EXECUTION_ITEMS
from ..integrations.wealth_authorization import WealthPolicyError
from ..schemas.wealth import StrictRequest, InvestmentCommand, InternalInvestmentSubmission, StrongVerification, PlanVerification
from ..services.bank import wealth_service as bank
from ..services.bank.transfer_service import BankError
from .bank_operations import require_internal_token

router = APIRouter(prefix="/api/v1/internal/bank/wealth", tags=["internal-wealth"],
                    dependencies=[Depends(require_internal_token)])


def fail(error: BankError | WealthPolicyError) -> None:
    """保留共享结构化领域码，不暴露输入或验证凭据。"""
    raise HTTPException(error.status_code, detail={"code": error.code, "message": error.message}) from error


class Subject(StrictRequest):
    subject_user_id: str = Field(max_length=32)

class CatalogueSubject(Subject):
    facts_only: bool = Field(default=False,strict=True)
    policy_references: bool = Field(default=False,strict=True)


from ..schemas.wealth_returns import ReturnsQuery, WealthReturns


class InternalReturns(ReturnsQuery, Subject):
    pass


from ..schemas.wealth_redemption import RedemptionRequest


class InternalRedemption(RedemptionRequest, Subject):
    operation_id: str = Field(min_length=8,max_length=64,pattern=r'^[a-zA-Z0-9_-]+$')


@router.post('/redemptions/preview')
async def redemption_preview(request: InternalRedemption, db: AsyncSession = Depends(get_db)):
    from ..services.bank.wealth_redemption import convert
    try:
        return await convert(db,request.subject_user_id,RedemptionRequest.model_validate(
            request.model_dump(exclude={'subject_user_id','operation_id'})),request.operation_id)
    except BankError as error:
        fail(error)


@router.post('/returns', response_model=WealthReturns)
async def actual_returns(request: InternalReturns, db: AsyncSession = Depends(get_db)):
    from ..services.bank.wealth_returns import read
    try:
        return await read(db, request.subject_user_id, ReturnsQuery.model_validate(request.model_dump(exclude={'subject_user_id'})))
    except BankError as error:
        fail(error)


class ProductDetail(Subject):
    product_id: str = Field(min_length=1,max_length=32)
    period: str = Field(default='1y',pattern=r'^(1m|3m|1y)$')
    product_version: int | None = Field(default=None,ge=1,strict=True)


class ExternalEvidence(Subject):
    account_id: str = Field(min_length=1,max_length=32)
    event_reference: str = Field(min_length=1,max_length=64)


@router.post('/external-evidence')
async def external_evidence(request:ExternalEvidence,db:AsyncSession=Depends(get_db)):
    try:
        return await bank.external_evidence(db,request.subject_user_id,request.account_id,request.event_reference)
    except BankError as error:fail(error)


@router.post('/product-detail')
async def product_detail(request:ProductDetail,db:AsyncSession=Depends(get_db)):
    from ..services.bank.wealth_product_detail import detail
    try:return await detail(db,request.subject_user_id,request.product_id,request.period,request.product_version)
    except BankError as error:fail(error)


from ..schemas.wealth_demo import DemoReset, DemoAdjust, DemoDeletePreview, DemoDelete


class InternalDemoReset(DemoReset, Subject):
    pass


class InternalDemoAdjust(DemoAdjust, Subject):
    pass


class InternalDemoDeletePreview(DemoDeletePreview, Subject):
    pass


class InternalDemoDelete(DemoDelete, Subject):
    pass


@router.post('/demo/current')
async def demo_current(request: Subject, db: AsyncSession = Depends(get_db)) -> dict:
    from ..services.bank import wealth_demo
    try: return await wealth_demo.current(db, request.subject_user_id)
    except BankError as error: fail(error)


@router.post('/demo/archives')
async def demo_archives(request: Subject, db: AsyncSession = Depends(get_db)) -> dict:
    from ..services.bank import wealth_demo
    try: return await wealth_demo.archives(db, request.subject_user_id)
    except BankError as error: fail(error)


@router.post('/demo/delete-preview')
async def demo_delete_preview(request: InternalDemoDeletePreview, db: AsyncSession = Depends(get_db)) -> dict:
    from ..services.bank import wealth_demo
    try: return await wealth_demo.deletion_preview(db, request.subject_user_id, [r.model_dump() for r in request.runs])
    except BankError as error: fail(error)


async def demo_write(db: AsyncSession, request: Subject, action: str) -> dict:
    from ..services.bank import wealth_demo
    try: return await wealth_demo.write(db, request.subject_user_id, action, request.model_dump(exclude={'subject_user_id'}))
    except BankError as error:
        await db.rollback()
        fail(error)


@router.post('/demo/reset')
async def demo_reset(request: InternalDemoReset, db: AsyncSession = Depends(get_db)) -> dict:
    return await demo_write(db, request, 'reset')


@router.post('/demo/adjust')
async def demo_adjust(request: InternalDemoAdjust, db: AsyncSession = Depends(get_db)) -> dict:
    return await demo_write(db, request, 'adjust')


@router.post('/demo/delete')
async def demo_delete(request: InternalDemoDelete, db: AsyncSession = Depends(get_db)) -> dict:
    return await demo_write(db, request, 'delete')


class Precheck(Subject):
    command: InvestmentCommand


class AssetSubject(Subject):
    account_id: str | None = Field(default=None, max_length=32)


class Verify(Subject, StrongVerification):
    authorization_id: str = Field(max_length=64)


class Batch(Subject):
    submissions: list[InternalInvestmentSubmission] = Field(min_length=1, max_length=MAX_EXECUTION_ITEMS)


class VerifyBatch(Subject, PlanVerification):
    authorization_ids: list[str] = Field(min_length=1, max_length=MAX_EXECUTION_ITEMS)
    plan_id: str = Field(max_length=64)


class Advance(StrictRequest):
    business_at: datetime
    subject_user_id: str = Field(max_length=32)
    context_id: str = Field(max_length=64)


class ResearchQuery(Subject):
    dataset_key: str | None = Field(default=None,max_length=96)
    query: str = Field(default="",max_length=200)
    category: str | None = Field(default=None,max_length=32)
    max_risk: int | None = Field(default=None,ge=1,le=5,strict=True)
    horizon_days: int | None = Field(default=None,ge=1,le=3650,strict=True)
    page: int = Field(default=1,ge=1,le=10000)
    page_size: int = Field(default=12,ge=1,le=60)
    product_ids: list[str] = Field(default_factory=list,max_length=4)
    period: str = Field(default="1y",pattern="^(1m|3m|1y|since)$")


class ContextChange(Subject):
    context_id: str = Field(max_length=64)
    version: int = Field(ge=1)


class FundSearch(Subject):
    query: str = Field(default='', max_length=200)
    fund_type: str | None = Field(default=None, max_length=80)
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=60)


class FundDetail(Subject):
    code: str = Field(pattern=r'^\d{6}$')
    fetch_missing: bool = True


class FundCompare(Subject):
    codes: list[str] = Field(min_length=2, max_length=4)
    period: str = Field(default='1y', pattern='^(1m|3m|1y|since)$')


@router.post('/funds/compare')
async def fund_compare(request: FundCompare, background: BackgroundTasks, db: AsyncSession=Depends(get_db)) -> dict:
    import re
    from datetime import datetime, timezone
    from ..domain.wealth_analytics import comparison
    if len(set(request.codes)) != len(request.codes) or any(not re.fullmatch(r'\d{6}', c) for c in request.codes):
        raise HTTPException(422, detail={'code':'INVALID_FUND_CODES'})
    details = [await fund_detail(FundDetail(subject_user_id=request.subject_user_id, code=c), background, db) for c in request.codes]
    products = []
    for detail in details:
        evidence = detail['evidence'].get('history', {}).get('data', {})
        products.append({'product_id':detail['product_id'], 'name':detail['name'], 'source':'akshare',
            'dataset_key':'on-demand-cache', 'history':evidence.get('observations', [])})
    return {'identity':'真实基金数据', 'trade_eligible':False, 'details':details,
        'analysis':comparison(products, datetime.now(timezone.utc).date().isoformat(), request.period),
        'notice':'缓存未齐时不计算；货币收益单列，不与单位净值混算；家族未知不证明独立风险。'}


@router.post('/funds/search')
async def fund_search(request: FundSearch, db: AsyncSession=Depends(get_db)) -> dict:
    from ..services.bank.wealth_fund_data import search
    return await search(db, request.query, request.fund_type, request.page, request.page_size)


@router.post('/funds/detail')
async def fund_detail(request: FundDetail, background: BackgroundTasks, db: AsyncSession=Depends(get_db)) -> dict:
    from ..services.bank import wealth_fund_data as funds
    from sqlalchemy.ext.asyncio import async_sessionmaker
    try:
        identity = await db.get(funds.Directory, request.code)
        if identity is None: raise ValueError('FUND_NOT_IN_DIRECTORY')
        if request.fetch_missing and os.getenv('WEALTH_FUND_ON_DEMAND_ENABLED') == 'true':
            for kind in ['money_yield' if '货币' in identity.fund_type else 'history', 'status', 'fees']:
                if await funds.claim(db, request.code, kind):
                    background.add_task(funds.refresh, async_sessionmaker(db.bind, expire_on_commit=False), request.code, kind)
        result = await funds.detail(db, request.code)
        result['on_demand_enabled'] = os.getenv('WEALTH_FUND_ON_DEMAND_ENABLED') == 'true'
        return result
    except ValueError as error:
        raise HTTPException(404, detail={'code': str(error), 'message': '基金不在已核验目录中'}) from error


@router.post('/knowledge')
async def knowledge(request: ResearchQuery,db: AsyncSession=Depends(get_db)) -> dict:
    from ..services.bank.wealth_knowledge import retrieve
    return {'citations':await retrieve(db,request.query,request.dataset_key),'retrieval_version':'chinese-bigram-v1'}


@router.post("/contexts")
async def contexts(request: Subject,db: AsyncSession=Depends(get_db)) -> dict:
    from ..services.bank.wealth_research import context_list
    return await context_list(db,request.subject_user_id)


@router.post("/contexts/select")
async def context_select(request: ContextChange,db: AsyncSession=Depends(get_db)) -> dict:
    from ..services.bank.wealth_research import select_context
    try: return await select_context(db,request.subject_user_id,request.context_id,request.version)
    except (BankError,WealthPolicyError) as error:
        await db.rollback(); fail(error)


@router.post("/research")
async def research_query(request: ResearchQuery,db: AsyncSession=Depends(get_db)) -> dict:
    from ..services.bank import wealth_research as research
    from ..domain.wealth_analytics import metrics,comparison
    from ..models.wealth_research import WealthDatasetORM,WealthMacroORM
    from sqlalchemy import select
    datasets=(await db.scalars(select(WealthDatasetORM).order_by(WealthDatasetORM.ingested_at.desc()))).all()
    context=await research.selected(db,request.subject_user_id)
    chosen_key=request.dataset_key or await research.default_dataset_key(db)
    visible_at=await bank.business_now(db,context.account_id) if context and context.dataset_key==chosen_key else None
    rows=await research.catalog(db,request.dataset_key,visible_at)
    rows=[p for p in rows if (not request.category or p.get('category')==request.category) and (not request.query or request.query.casefold() in (p['name']+' '+p['product_id']).casefold())]
    from ..domain.wealth_allocation import liquidity_days
    rows=[p for p in rows if (request.max_risk is None or (p.get('risk_level') is not None and p['risk_level']<=request.max_risk)) and (request.horizon_days is None or (p.get('rules') and liquidity_days(p)<=request.horizon_days))]
    total=len(rows)
    as_of=max((p['nav_date'] for p in rows if p['nav_date']),default='2026-09-30')
    if request.product_ids:
        if len(request.product_ids)!=len(set(request.product_ids)): raise HTTPException(422,detail={'code':'INVALID_COMPARISON','message':'产品不能重复'})
        chosen=[next((p for p in rows if p['product_id']==pid),None) for pid in request.product_ids]
        if any(p is None for p in chosen): raise HTTPException(404,detail={'code':'PRODUCT_UNAVAILABLE','message':'产品不在指定数据集中'})
        analysis=comparison(chosen,as_of,request.period)
    else: analysis=None
    page=rows[(request.page-1)*request.page_size:request.page*request.page_size]
    for p in page:
        p['metrics']=metrics(p['history'],as_of=as_of,period=request.period,dividends_verified=p['source']=='synthetic')
        p['history']=p['history'][-1500:]
    key=request.dataset_key or (rows[0]['dataset_key'] if rows else None)
    macros=(await db.scalars(select(WealthMacroORM).where(WealthMacroORM.dataset_key==key))).all() if key else []
    return {'items':page,'total':total,'page':request.page,'page_size':request.page_size,'analysis':analysis,'as_of':as_of,
        'datasets':[{'dataset_key':d.dataset_key,'mode':d.mode,**d.manifest} for d in datasets],
        'macros':[{'indicator':m.indicator,'observations':[o for o in m.observations if o['period'][:10]<=as_of and (not visible_at or datetime.fromisoformat(o['available_at'])<=visible_at)][-24:]} for m in macros]}


def require_mock_admin() -> None:
    """业务时间和结算只可在显式启用的隔离模拟库使用。"""
    if (os.getenv("WEALTH_MOCK_ADMIN_ENABLED") != "true"
            or not any(marker in DATABASE_URL for marker in ("wealth_test_", "wealth-demo", "wealth_test"))):
        raise HTTPException(403, detail={"code": "MOCK_ADMIN_DISABLED", "message": "仅允许显式启用的隔离模拟库管理员"})


@router.get("/products")
async def products(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """内部客观产品读取。"""
    return await bank.products(db)


@router.post("/assets")
async def assets(request: AssetSubject, db: AsyncSession = Depends(get_db)) -> dict:
    """内部本人银行资产事实。"""
    from ..services.bank.wealth_asset_summary import begin_read
    await begin_read(db)
    return await bank.assets(db, request.subject_user_id, request.account_id)


@router.post('/asset-summary')
async def asset_summary(request: Subject, db: AsyncSession = Depends(get_db)) -> dict:
    from ..services.bank.wealth_asset_summary import read
    try: return await read(db, request.subject_user_id)
    except BankError as error: fail(error)


@router.post("/catalog")
async def contextual_catalog(request: CatalogueSubject, db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await bank.products(db, request.subject_user_id,facts_only=request.facts_only,policy_references=request.policy_references)


@router.post("/precheck")
async def precheck(request: Precheck, db: AsyncSession = Depends(get_db)) -> dict:
    """服务器重算估算和资格，不修改余额。"""
    try:
        return await bank.precheck(db, request.subject_user_id, request.command)
    except (BankError, WealthPolicyError) as error:
        fail(error)


@router.post("/verify")
async def verify(request: Verify, db: AsyncSession = Depends(get_db)) -> dict:
    """锁住本人验证码时间步，与逐笔强验证事实同事务提交。"""
    try:
        return await bank.verify_authorization(db, request.subject_user_id, request.authorization_id, request.proof,
            method=request.method, challenge_id=request.challenge_id)
    except (BankError, WealthPolicyError) as error:
        await db.rollback()
        fail(error)


@router.get("/verification-mode")
async def verification_mode(db: AsyncSession = Depends(get_db)) -> dict:
    from ..integrations.wealth_verification import verification_mode as mode
    return mode(db)


@router.post("/verify/batch")
async def batch_verify(request: VerifyBatch, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        return await bank.verify_authorizations(db, request.subject_user_id, request.authorization_ids, request.proof,
            method=request.method, challenge_id=request.challenge_id, plan_id=request.plan_id, version=request.version)
    except (BankError, WealthPolicyError) as error:
        await db.rollback()
        fail(error)


@router.post("/orders/{operation_id}/cancellation-eligibility")
async def cancellation_eligibility(operation_id: str, request: Subject, db: AsyncSession = Depends(get_db)) -> dict:
    """只读合同窗口资格，不消耗用户授权。"""
    try:
        return await bank.cancellation_eligibility(db, request.subject_user_id, operation_id)
    except (BankError, WealthPolicyError) as error:
        fail(error)


@router.post("/orders")
async def submit(request: InternalInvestmentSubmission, db: AsyncSession = Depends(get_db)) -> dict:
    """银行独立核验强授权证据后受理。"""
    try:
        result=(await bank.accept_many(db, request.subject_user_id, [(request.command, request.authorization_id)]))[0]
        from ..models.wealth_research import WealthContextORM
        from sqlalchemy import select
        ctx=await db.scalar(select(WealthContextORM).where(WealthContextORM.account_id==request.command.account_id))
        if ctx and ctx.data.get('fixture',{}).get('lose_submit_response'):
            raise HTTPException(502,detail={'code':'BANK_RESULT_UNKNOWN','message':'固定演示场景：受理后响应丢失，请按原操作号查询'})
        return result
    except (BankError, WealthPolicyError) as error:
        await db.rollback()
        fail(error)


@router.post("/orders/batch")
async def batch(request: Batch, db: AsyncSession = Depends(get_db)) -> list[dict]:
    """同主体纯申购组合原子占用全部预算。"""
    if any(item.subject_user_id != request.subject_user_id or item.command.direction != "purchase" for item in request.submissions):
        raise HTTPException(422, detail={"code": "INVALID_PLAN", "message": "批量受理仅支持本人纯申购"})
    try:
        return await bank.accept_many(db, request.subject_user_id, [(item.command, item.authorization_id) for item in request.submissions])
    except (BankError, WealthPolicyError) as error:
        await db.rollback()
        fail(error)


@router.post("/orders/{operation_id}/query")
async def query(operation_id: str, request: Subject, db: AsyncSession = Depends(get_db)) -> dict:
    """原操作号查询，不因为404更换编号重发。"""
    try:
        return bank.order_view(await bank.owned_order(db, request.subject_user_id, operation_id))
    except BankError as error:
        fail(error)


@router.post("/orders/{operation_id}/cancel")
async def cancel(operation_id: str, request: Subject, db: AsyncSession = Depends(get_db)) -> dict:
    """窗口内明确撤单请求。"""
    try:
        return await bank.cancel(db, request.subject_user_id, operation_id)
    except BankError as error:
        await db.rollback()
        fail(error)


@router.post("/admin/clock", dependencies=[Depends(require_mock_admin)])
async def advance(request: Advance, db: AsyncSession = Depends(get_db)) -> dict:
    """受控业务时间只能向前，不延长安全有效期。"""
    try:
        return await bank.advance_clock(db, request.business_at, request.subject_user_id, request.context_id)
    except BankError as error:
        await db.rollback()
        fail(error)


@router.post("/admin/settle/{operation_id}", dependencies=[Depends(require_mock_admin)])
async def settle(operation_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """隔离管理员结算，可重复执行且不追加重复账务。"""
    try:
        return await bank.settle(db, operation_id)
    except BankError as error:
        await db.rollback()
        fail(error)
