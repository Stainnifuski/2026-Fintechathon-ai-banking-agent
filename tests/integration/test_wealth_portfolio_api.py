"""Isolated route mount; no shared main wiring, DB, model or orders."""
import asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from backend.app.routers.wealth_portfolio import router, get_snapshot_source
from backend.app.middleware.auth import get_current_user
from backend.app.services.orchestration.wealth_portfolio import FixtureSnapshotSource
from scripts.generate_wealth_portfolio_fixtures import generate

def request(*, user='fixture-user', source=True, bad=False):
    app=FastAPI(); app.include_router(router)
    if user is not None:
        app.dependency_overrides[get_current_user]=lambda: {'user_id':user}
    if source:
        bundle=generate()['F18']['input']['bundle']
        if bad: bundle['summary']['total_assets_fen']=1
        provider=FixtureSnapshotSource(bundle)
        app.dependency_overrides[get_snapshot_source]=lambda:provider
    async def run():
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://isolated') as client:
            return await client.get('/api/v1/wealth/portfolio/snapshot')
    return asyncio.run(run())

def test_full_fixture_scope_response():
    r=request(); assert r.status_code==200
    assert r.json()['basis_status']=='fixture_only'
    assert r.json()['total_assets_fen']==1000000 and len(r.json()['accounts'])==2

def test_unauthenticated_rejected(): assert request(user=None).status_code==401
def test_other_user_rejected(): assert request(user='other').status_code==403
def test_missing_b_source_not_automatically_substituted():
    r=request(source=False); assert r.status_code==503
    assert r.json()['detail']['code']=='ASSET_SUMMARY_UNAVAILABLE'
def test_malformed_upstream_not_silently_zeroed(): assert request(bad=True).status_code==409
