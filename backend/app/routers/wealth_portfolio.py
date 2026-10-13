"""A1独立只读路由；正式全量来源和中央注册由B接线。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from ..middleware.auth import get_current_user
from ..schemas.wealth_portfolio import AssetSnapshot
from ..services.orchestration.wealth_portfolio import AssetSnapshotSource, SnapshotError, read_snapshot

router = APIRouter(prefix='/api/v1/wealth/portfolio', tags=['wealth-portfolio'])


from ..database.connection import get_db
from .wealth import get_wealth_bank

def require_provider(request: Request):
    if getattr(request.app.state,'wealth_portfolio_source',None) is None:
        raise HTTPException(503,detail={'code':'ASSET_SUMMARY_UNAVAILABLE','message':'Canonical portfolio source not registered'})

def portfolio_bank(_=Depends(require_provider),bank=Depends(get_wealth_bank)):
    return bank

class CanonicalSnapshotReader:
    def __init__(self,provider,db,bank):self.provider,self.db,self.bank=provider,db,bank
    async def read_snapshot(self,user_id):
        facts=await self.bank.assets(user_id)
        value=await self.provider.snapshot(self.db,user_id,facts['context_id'],self.bank)
        result=AssetSnapshot.model_validate(value)
        if result.context_id!=facts['context_id'] or set(result.account_ids)!=set(facts['asset_summary']['account_ids']):
            raise SnapshotError('snapshot scope changed','PORTFOLIO_CONTEXT_STALE',409)
        return result

def get_snapshot_source(request: Request, user: dict=Depends(get_current_user), db=Depends(get_db), bank=Depends(portfolio_bank)) -> AssetSnapshotSource:
    provider=getattr(request.app.state,'wealth_portfolio_source',None)
    if provider is not None:
        return provider.bind(db,user['user_id'],bank)[0] if hasattr(provider,'bind') else CanonicalSnapshotReader(provider,db,bank)

    raise HTTPException(503, detail={'code': 'ASSET_SUMMARY_UNAVAILABLE',
        'message': 'B full-scope AssetSummary source not registered; use explicit isolated fixture app'})


@router.get('/snapshot', response_model=AssetSnapshot)
async def snapshot(user: dict = Depends(get_current_user), source: AssetSnapshotSource=Depends(get_snapshot_source), context_id: str | None=None) -> AssetSnapshot:
    try:
        result=await source.read_snapshot(user['user_id']) if hasattr(source,'read_snapshot') else await read_snapshot(source, user['user_id'])
        if context_id is not None and result.context_id!=context_id:
            raise SnapshotError('requested context is not the current snapshot context','PORTFOLIO_CONTEXT_STALE',409)
        return result
    except SnapshotError as error:
        raise HTTPException(error.status_code, detail={'code': error.code, 'message': error.message}) from error
    except (ValidationError, KeyError, ValueError) as error:
        raise HTTPException(409, detail={'code': 'SNAPSHOT_INCONSISTENT',
            'message': 'upstream facts incomplete or inconsistent; no total fabricated'}) from error


from ..schemas.wealth_portfolio import PortfolioPlan,PortfolioSelection,CandidateGroup
from ..schemas.wealth_portfolio_lifecycle import PortfolioCreate,PortfolioRecommendation
from ..services.orchestration.wealth_portfolio_lifecycle import PortfolioLifecycle
from ..services.orchestration.wealth_portfolio_candidates import CandidateError
from ..domain.wealth_portfolio_allocation import ModelError

def get_portfolio_lifecycle(request: Request, user: dict=Depends(get_current_user), db=Depends(get_db), bank=Depends(portfolio_bank)) -> PortfolioLifecycle:
    provider=getattr(request.app.state,'wealth_portfolio_source',None)
    if provider is not None and hasattr(provider,'bind'):return provider.bind(db,user['user_id'],bank)[1]

    raise HTTPException(503,detail={'code':'PORTFOLIO_SOURCE_UNAVAILABLE','message':'全授权资产、当前评估与context来源尚未注册'})

async def _invoke(call):
    try:return await call
    except (CandidateError,SnapshotError,ModelError) as error:
        code=error.code;status=getattr(error,'status_code',422)
        details=getattr(error,'details',None)
        reasons=details.get('reason_codes',[]) if isinstance(details,dict) else []
        message=str(error)+('：'+', '.join(r for r in reasons if isinstance(r,str)) if reasons else '')
        raise HTTPException(status,detail={'code':code,'message':message,'evidence':details}) from error

@router.post('/plans',response_model=PortfolioPlan)
async def create_plan(request: PortfolioCreate,user: dict=Depends(get_current_user),service: PortfolioLifecycle=Depends(get_portfolio_lifecycle)) -> PortfolioPlan:
    return await _invoke(service.create(user['user_id'],request))

@router.get('/plans/{portfolio_id}',response_model=PortfolioPlan)
async def get_plan(portfolio_id: str,context_id: str,user: dict=Depends(get_current_user),service: PortfolioLifecycle=Depends(get_portfolio_lifecycle)) -> PortfolioPlan:
    return await _invoke(service.get(user['user_id'],portfolio_id,context_id))

@router.put('/plans/{portfolio_id}',response_model=PortfolioPlan)
async def revise_plan(portfolio_id: str,request: PortfolioSelection,user: dict=Depends(get_current_user),service: PortfolioLifecycle=Depends(get_portfolio_lifecycle)) -> PortfolioPlan:
    return await _invoke(service.revise(user['user_id'],portfolio_id,request))

@router.post('/recommendations',response_model=CandidateGroup)
async def recommendations(request: PortfolioRecommendation,user: dict=Depends(get_current_user),service: PortfolioLifecycle=Depends(get_portfolio_lifecycle)) -> CandidateGroup:
    return await _invoke(service.recommend(user['user_id'],request))
