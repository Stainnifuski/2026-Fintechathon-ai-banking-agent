"""独立A验收应用薄包装；不注册公共app，不创建交易或新的共同契约。"""
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from backend.app.middleware.auth import get_current_user
from backend.app.routers.wealth_portfolio import get_portfolio_lifecycle
from backend.app.schemas.wealth_portfolio_candidates import SessionSelectionRequest, SwitchRequest, SessionAction
from backend.app.services.orchestration.wealth_portfolio_access import access_binding
from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateError
from backend.app.services.orchestration.wealth_portfolio import build_snapshot, SnapshotError

class SelectBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    context_id: str
    action: SessionSelectionRequest

class SwitchBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    context_id: str
    action: SwitchRequest

def install(app):
    async def call(service,user,body,switch=False):
        try:
            grant=await service._access(user,body.context_id,False)
            candidates=service.provider.candidates
            async with candidates.lock:
                state=candidates._state(user,SessionAction(session_id=body.action.session_id,expected_version=body.action.expected_version))
                if service.candidate_contexts.get(state.session_id)!=access_binding(grant):
                    raise CandidateError('PORTFOLIO_CANDIDATE_BINDING_STALE')
            result=await (candidates.switch_share(user,body.action) if switch else candidates.select(user,body.action))
            if access_binding(await service._access(user,body.context_id,False))!=access_binding(grant):
                raise CandidateError('PORTFOLIO_CONTEXT_STALE')
            return result
        except CandidateError as error:
            raise HTTPException(error.status_code,detail={'code':error.code,'evidence':error.details}) from error

    @app.post('/dev/candidates/select')
    async def select(body: SelectBody,user=Depends(get_current_user),service=Depends(get_portfolio_lifecycle)):
        return await call(service,user['user_id'],body)

    @app.post('/dev/candidates/switch')
    async def switch(body: SwitchBody,user=Depends(get_current_user),service=Depends(get_portfolio_lifecycle)):
        return await call(service,user['user_id'],body,True)

    @app.get('/dev/context')
    async def context(user=Depends(get_current_user),service=Depends(get_portfolio_lifecycle)):
        try:
            if service.provider.asset_source is None:raise CandidateError('ASSET_SUMMARY_UNAVAILABLE',503)
            raw=await service.provider.asset_source.read(user['user_id'])
            snapshot=build_snapshot(raw)
            context_id=raw['context_id']
            grant=await service._access(user['user_id'],context_id,False)
            try:
                writable=await service._access(user['user_id'],context_id,True)
                can_edit=writable.get('can_edit') is True;reason=None
            except CandidateError as error:
                can_edit=False;reason=error.code
            return {'context_id':context_id,'can_read':grant['can_read'],'can_edit':can_edit,'write_reason':reason,
                    'snapshot_id':snapshot.snapshot_id,
                    'asset_totals':{key:raw['summary'].get(key) for key in ('available_cash_fen','held_fen','redemption_receivable_fen')}}
        except (CandidateError,SnapshotError) as error:
            raise HTTPException(error.status_code,detail={'code':error.code,'evidence':getattr(error,'details',None)}) from error
