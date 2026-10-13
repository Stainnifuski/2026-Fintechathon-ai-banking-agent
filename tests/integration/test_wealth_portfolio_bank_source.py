"""独立可信入口认证关闭检查，无伪造身份或fixture注册。"""
from pathlib import Path
import httpx
import pytest
from scripts.wealth_portfolio_bank_dev import create_app

@pytest.mark.asyncio
async def test_independent_app_no_identity_no_bank_call():
    app = create_app('http://127.0.0.1:8010', 'data', 'daily', Path('.'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        result = await client.get('/api/v1/wealth/portfolio/snapshot')
    assert result.status_code == 401 and app.state.store.rows == {}
    assert not any('bootstrap' in path for path in app.openapi()['paths'])
    from backend.app.routers.wealth_portfolio import get_portfolio_lifecycle
    from backend.app.integrations.wealth_portfolio_bank_facts import BankPortfolioFacts
    factory = app.dependency_overrides[get_portfolio_lifecycle]
    first_source = BankPortfolioFacts(None, 'data', 'daily')
    second_source = BankPortfolioFacts(None, 'data', 'daily')
    first, second = factory(first_source), factory(second_source)
    assert first.store is second.store
    assert first.candidate_contexts is second.candidate_contexts
    assert first.provider.candidates.sessions is second.provider.candidates.sessions
    assert first.provider.candidates.lock is second.provider.candidates.lock
    assert first.provider.candidates.source is first_source
    assert second.provider.candidates.source is second_source
