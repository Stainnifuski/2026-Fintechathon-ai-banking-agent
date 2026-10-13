"""01独立只读/资格路由；不由main注册，不接受客户端评估或银行事实。"""
from fastapi import APIRouter,Depends,HTTPException
from ..middleware.auth import get_current_user
from ..schemas.wealth_portfolio_candidates import (CandidateFilter,SelectionRequest,SessionSelectionRequest,SwitchRequest,NextBatchRequest,SessionAction)
from ..services.orchestration.wealth_portfolio_candidates import CandidateService,CandidateError

router=APIRouter(prefix='/api/v1/wealth/portfolio/candidates',tags=['wealth-portfolio-candidates'])

def get_candidate_service() -> CandidateService:
    raise HTTPException(503,detail={'code':'ASSESSMENT_SOURCE_UNAVAILABLE'})

async def invoke(call):
    try:return await call
    except CandidateError as error:raise HTTPException(error.status_code,detail={'code':error.code,'state':error.details}) from error

@router.post('')
async def candidates(request: CandidateFilter,user=Depends(get_current_user),service=Depends(get_candidate_service)):
    return await invoke(service.candidates(user['user_id'],request))

@router.post('/validate-selection')
async def validate_selection(request: SelectionRequest,user=Depends(get_current_user),service=Depends(get_candidate_service)):
    return await invoke(service.validate_selection(user['user_id'],request))

@router.post('/select')
async def select(request: SessionSelectionRequest,user=Depends(get_current_user),service=Depends(get_candidate_service)):
    return await invoke(service.select(user['user_id'],request))

@router.post('/switch')
async def switch(request: SwitchRequest,user=Depends(get_current_user),service=Depends(get_candidate_service)):
    return await invoke(service.switch_share(user['user_id'],request))

@router.post('/next')
async def next_batch(request: NextBatchRequest,user=Depends(get_current_user),service=Depends(get_candidate_service)):
    return await invoke(service.next_batch(user['user_id'],request))

@router.post('/refresh')
async def refresh(request: SessionAction,user=Depends(get_current_user),service=Depends(get_candidate_service)):
    return await invoke(service.refresh(user['user_id'],request))
