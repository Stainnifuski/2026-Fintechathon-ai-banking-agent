"""显式本地隔离开发app，所有方案走正式01计算/生命周期，不连接银行库。"""
import asyncio
import os
import secrets
if __name__=='__main__':os.environ['JWT_SECRET']=secrets.token_urlsafe(32)
from copy import deepcopy
from fastapi import FastAPI,Header,HTTPException
from backend.app.routers.wealth_portfolio import router,get_portfolio_lifecycle,get_snapshot_source
from backend.app.middleware.auth import create_access_token
from tests.fixtures.wealth_portfolio_lifecycle_v1 import lifecycle_fixture
from scripts.generate_wealth_portfolio_fixtures import generate
from backend.app.schemas.wealth_portfolio_lifecycle import PortfolioCreate
from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateError

SCENARIOS=('R1','R2','R4','F18','missing-valuation','source-unavailable','version-conflict','only-C','single-share','holding-only')
services={}
def build_service(name):
    s,data,facts,bundle,clock=lifecycle_fixture(1 if name=='R1' else 2 if name in ('R2','holding-only') else 4)
    s.access.grant['context_id']='fixture-context-'+name
    s.provider.asset_source.bundle['context_id']=s.access.grant['context_id']
    if name=='R2':
        from tests.fixtures.wealth_portfolio_candidates_v1 import field
        next(p for p in data.products if p.product_id=='Q07C').fields['formal_risk']=field(4,'risk_level')
        facts.offers['Q07C'].risk_level=4
    if name=='only-C':
        for code,offer in facts.offers.items():
            if code.endswith('A'):offer.purchase_allowed=False
    if name=='single-share':
        data.products[:]=[p for p in data.products if p.share_class=='A']
        facts.offers={k:v for k,v in facts.offers.items() if k.endswith('A')}
    if name=='holding-only':
        from tests.fixtures.wealth_portfolio_candidates_v1 import field
        for code in ('Q03A','Q03C'):
            next(p for p in data.products if p.product_id==code).fields['formal_risk']=field(4,'risk_level')
            facts.offers[code].risk_level=4
    if name=='F18':
        original=generate()['F18']['input']['bundle'];b=s.provider.asset_source.bundle
        b['accounts']=deepcopy(original['accounts']);b['summary'].update({k:v for k,v in original['summary'].items() if k not in ('as_of','business_at')})
        b['orders']=deepcopy(original['orders']);b['lots']=deepcopy(original['lots'])
        for row in b['lots']:row['product_id']='Q01A';row['category']='bond'
        s.access.grant['account_ids']=b['summary']['account_ids']
    if name=='missing-valuation':
        b=s.provider.asset_source.bundle;b['summary'].update(positions_market_value_fen=None,total_assets_fen=None,valuation_complete=False,missing_fields=['position.nav'])
        b['lots'][0].update(market_value_fen=None,nav=None,nav_date=None)
    if name=='source-unavailable':s.provider.asset_source=None
    return s

services={name:build_service(name) for name in SCENARIOS}

app=FastAPI(title='01 isolated portfolio fixture app');app.include_router(router)
def service(x_portfolio_scenario: str=Header('R4')):
    if x_portfolio_scenario not in services:raise HTTPException(422,detail={'code':'SCENARIO_UNKNOWN','message':'isolated fixture scenario'})
    return services[x_portfolio_scenario]
def source(x_portfolio_scenario: str=Header('R4')):
    s=service(x_portfolio_scenario)
    if s.provider.asset_source is None:raise HTTPException(503,detail={'code':'ASSET_SUMMARY_UNAVAILABLE','message':'source unavailable'})
    return s.provider.asset_source
app.dependency_overrides[get_portfolio_lifecycle]=service
app.dependency_overrides[get_snapshot_source]=source
from scripts.wealth_portfolio_dev_routes import install
install(app)
@app.middleware('http')
async def latency(request,call_next):
    if request.method in ('POST','PUT') and '/portfolio/' in request.url.path:await asyncio.sleep(.5)
    return await call_next(request)
@app.get('/dev/bootstrap')
async def bootstrap(scenario: str='R4'):
    s=service(scenario)
    return {'token':create_access_token({'sub':'fixture-user'}),'context_id':s.access.grant['context_id'],'scenario':scenario,'source':'fixture_only','initial_p':'.625' if scenario in ('R4','version-conflict') else None}

@app.post('/dev/reset')
async def reset_fixture(scenario: str='R4'):
    if scenario not in SCENARIOS:raise HTTPException(422,detail={'code':'SCENARIO_UNKNOWN'})
    services[scenario]=build_service(scenario)
    return {'fixture_only':True,'scenario':scenario}

@app.post('/dev/change/{change}')
async def change_fixture(change: str,scenario: str='R4'):
    """固定隔离用例的事实变动；无此入口于比赛事实app。"""
    from tests.fixtures.wealth_portfolio_candidates_v1 import field
    from datetime import timedelta
    s=service(scenario);facts=s.provider.candidates.source.facts
    data=s.provider.candidates.indexes.get(facts.data_version)
    if change=='read-only':s.access.grant['can_edit']=False
    elif change=='write-lock':s.access.grant['stop_new_submissions']=True
    elif change=='expired':facts.assessment.expires_at=s.provider.candidates.clock()-timedelta(seconds=1)
    elif change=='downgrade':facts.assessment.capacity_level=1
    elif change=='stopped':
        for offer in facts.offers.values():offer.purchase_allowed=False
    elif change=='switch-risk':
        for code,offer in facts.offers.items():
            if code.endswith('C'):
                offer.risk_level=5;data.products[code].fields['formal_risk']=field(5,'risk_level')
    elif change=='all-high':
        for code,offer in facts.offers.items():
            offer.risk_level=5;data.products[code].fields['formal_risk']=field(5,'risk_level')
    elif change=='material':
        for p in data.products.values():p.fields.pop('strategy',None)
    elif change=='assets':
        b=s.provider.asset_source.bundle;b['accounts'][0]['balance_fen']+=100;b['accounts'][0]['available_fen']+=100
        for k in ('cash_balance_fen','available_cash_fen','total_assets_fen'):b['summary'][k]+=100
    elif change=='context':
        s.access.grant['context_epoch']+=1;s.provider.asset_source.bundle['context_epoch']+=1
    else:raise HTTPException(422,detail={'code':'CHANGE_UNKNOWN'})
    return {'fixture_only':True,'change':change}
@app.post('/dev/conflict/{portfolio_id}')
async def conflict(portfolio_id: str,scenario: str='version-conflict'):
    from backend.app.schemas.wealth_portfolio import PortfolioSelection
    s=service(scenario);row=await s.store.read(portfolio_id,'fixture-user');p=row.plan
    result=await s.revise('fixture-user',portfolio_id,PortfolioSelection(portfolio_id=portfolio_id,expected_version=p.version,snapshot_id=p.snapshot_id,context_id=p.context_id,items=p.selected_positions,growth_ratio_ex_cash='.626'))
    return {'server_version':result.version}

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8051)
