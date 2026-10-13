"""独立比赛事实接线；不启动银行、不迁移、不签发身份、不回退fixture。"""
import os
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.app.middleware.auth import get_current_user
from backend.app.routers.wealth_portfolio import router, get_snapshot_source, get_portfolio_lifecycle
from backend.app.integrations.wealth_portfolio_bank_facts import LocalBankReads, BankPortfolioFacts, BankPortfolioAccess
from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateService, SnapshotIndexCache, CandidateError
from backend.app.services.orchestration.wealth_portfolio_model import PortfolioModelProvider
from backend.app.services.orchestration.wealth_portfolio_lifecycle import PortfolioLifecycle
from backend.app.services.orchestration.wealth_portfolio_store import MemoryPortfolioStore


def create_app(base_url: str, data_version: str, daily_version: str, root: Path) -> FastAPI:
    """使用原银行/auth/me验证现有Bearer，source只接受该本人身份。"""
    app = FastAPI(title='Portfolio protected competition facts')
    app.include_router(router)
    bearer = HTTPBearer(auto_error=False)
    store = MemoryPortfolioStore(lambda: datetime.now(timezone.utc))
    indexes = SnapshotIndexCache.local(root)
    candidate_sessions: dict = {}
    candidate_contexts: dict = {}
    candidate_lock = asyncio.Lock()

    def transport(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> LocalBankReads:
        if credentials is None:
            raise HTTPException(401, detail='请先登录')
        return LocalBankReads(base_url, credentials.credentials)

    async def identity(client: LocalBankReads = Depends(transport)) -> dict:
        try:
            return await client.get('/api/v1/auth/me')
        except CandidateError as error:
            raise HTTPException(error.status_code, detail={'code': error.code}) from error

    def source(client: LocalBankReads = Depends(transport)) -> BankPortfolioFacts:
        return BankPortfolioFacts(client, data_version, daily_version)

    def lifecycle(facts: BankPortfolioFacts = Depends(source)) -> PortfolioLifecycle:
        candidates = CandidateService(facts, indexes)
        # 来源/凭据按请求隔离；候选版本与绑定保持同一独立app会话状态。
        candidates.sessions, candidates.lock = candidate_sessions, candidate_lock
        provider = PortfolioModelProvider(candidates, facts)
        service = PortfolioLifecycle(provider, store, BankPortfolioAccess(facts))
        service.candidate_contexts = candidate_contexts
        return service

    app.dependency_overrides[get_current_user] = identity
    app.dependency_overrides[get_snapshot_source] = source
    app.dependency_overrides[get_portfolio_lifecycle] = lifecycle
    app.state.store = store
    from scripts.wealth_portfolio_dev_routes import install
    install(app)
    return app


def configured_app() -> FastAPI:
    return create_app(os.environ['PORTFOLIO_BANK_BASE_URL'], os.environ['PORTFOLIO_DATA_VERSION'],
                      os.environ['PORTFOLIO_DAILY_VERSION'], Path(__file__).resolve().parents[1])


if __name__ == '__main__':
    import socket
    import uvicorn
    port = int(os.getenv('PORTFOLIO_BANK_PORT', '8052'))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', port))
    uvicorn.run(configured_app(), host='127.0.0.1', port=port)
