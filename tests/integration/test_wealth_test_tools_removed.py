"""Retired wealth test tools are unavailable on both HTTP boundaries."""
import httpx
import pytest
from fastapi import FastAPI

from backend.app.routers.wealth import router as wealth_router
from backend.app.routers.wealth_bank import router as bank_router


@pytest.mark.asyncio
async def test_wealth_test_tool_endpoints_are_removed():
    app = FastAPI()
    app.include_router(wealth_router)
    app.include_router(bank_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        for prefix in ('/api/v1/wealth', '/api/v1/internal/bank/wealth'):
            for action in ('current', 'archives', 'reset', 'adjust', 'delete-preview', 'delete'):
                method = 'GET' if prefix == '/api/v1/wealth' and action in ('current', 'archives') else 'POST'
                response = await client.request(method, f'{prefix}/demo/{action}')
                assert response.status_code == 404, (method, response.url, response.text)
