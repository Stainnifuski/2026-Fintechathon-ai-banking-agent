import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app.routers.wealth_portfolio import router,get_portfolio_lifecycle
from backend.app.middleware.auth import create_access_token
from tests.fixtures.wealth_portfolio_lifecycle_v1 import lifecycle_fixture

@pytest.fixture
def api():
    s,*_=lifecycle_fixture();app=FastAPI();app.include_router(router);app.dependency_overrides[get_portfolio_lifecycle]=lambda:s
    return TestClient(app),s

def auth(user='fixture-user'):return {'Authorization':'Bearer '+create_access_token({'sub':user})}

def test_actual_auth_create_get_revise_and_private404(api):
    client,s=api
    assert client.post('/api/v1/wealth/portfolio/plans',json={'context_id':'fixture-context'}).status_code==401
    r=client.post('/api/v1/wealth/portfolio/plans',headers=auth(),json={'context_id':'fixture-context','growth_ratio_ex_cash':'.625'});assert r.status_code==200
    p=r.json();url='/api/v1/wealth/portfolio/plans/'+p['portfolio_id']
    assert client.get(url,headers=auth(),params={'context_id':'fixture-context'}).json()==p
    assert client.get(url,headers=auth('other'),params={'context_id':'fixture-context'}).status_code==404
    req={k:p[k] for k in ['portfolio_id','snapshot_id','context_id']};req.update(expected_version=1,items=[],growth_ratio_ex_cash='.626')
    r=client.put(url,headers=auth(),json=req);assert r.status_code==200 and r.json()['version']==2
    assert client.put(url,headers=auth(),json=req).status_code==409
    req['portfolio_id']='other';assert client.put(url,headers=auth(),json=req).status_code==422
    assert not any(x in str(app_route.path) for app_route in router.routes for x in ['execute','prepare','confirm'])

@pytest.mark.parametrize('field,value',[('T',1),('risk_level',5),('source','fixture'),('enable_fixture',True),('user_id','other'),('U',{}),('b',[0,0,1]),('authorization_id','fake')])
def test_client_cannot_supply_facts(api,field,value):
    client,s=api;body={'context_id':'fixture-context',field:value}
    assert client.post('/api/v1/wealth/portfolio/plans',headers=auth(),json=body).status_code==422
    assert not s.store.rows

def test_default_source503_and_candidate_business_state(api):
    app=FastAPI();app.include_router(router);client=TestClient(app)
    assert client.post('/api/v1/wealth/portfolio/plans',headers=auth(),json={'context_id':'fixture-context'}).status_code==503
    client,s=api
    r=client.post('/api/v1/wealth/portfolio/recommendations',headers=auth(),json={'context_id':'fixture-context','filter':{'mode':'theme','theme_key':'broad_market'}})
    assert r.status_code==200 and r.json()['status']=='no_theme_match'
    r=client.post('/api/v1/wealth/portfolio/recommendations',headers=auth(),json={'context_id':'fixture-context','filter':{'mode':'general'}})
    assert r.status_code==200 and r.json()['session_binding']['version']==1


def test_stale_GET409_and_PUT_cannot_supply_model_contract(api):
    client,s=api;p=client.post('/api/v1/wealth/portfolio/plans',headers=auth(),json={'context_id':'fixture-context'}).json();url='/api/v1/wealth/portfolio/plans/'+p['portfolio_id']
    body={'portfolio_id':p['portfolio_id'],'expected_version':1,'snapshot_id':p['snapshot_id'],'context_id':p['context_id'],'items':[],'U':{'delta':99}}
    assert client.put(url,headers=auth(),json=body).status_code==422
    assert s.store.rows[p['portfolio_id']][0].plan.version==1
    s.provider.candidates.source.facts.offers['Q00A'].purchase_allowed=False
    response=client.get(url,headers=auth(),params={'context_id':'fixture-context'})
    assert response.status_code==409 and response.json()['detail']['code']=='PORTFOLIO_FACTS_STALE'
    assert s.store.rows[p['portfolio_id']][0].plan.version==1
