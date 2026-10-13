"""独立ASGI资格入口，未接银行应用/数据库/交易。"""
import asyncio
from fastapi import FastAPI
from httpx import AsyncClient,ASGITransport
from backend.app.routers.wealth_portfolio_candidates import router,get_candidate_service
from backend.app.middleware.auth import get_current_user
from backend.app.domain.wealth_portfolio_eligibility import version_context
from tests.fixtures.wealth_portfolio_candidates_v1 import fixture_pool,service,share


def request(engine,path,body,user='fixture-user',mount_source=True):
    app=FastAPI();app.include_router(router)
    if user is not None:app.dependency_overrides[get_current_user]=lambda:{'user_id':user}
    if mount_source:app.dependency_overrides[get_candidate_service]=lambda:engine
    async def perform():
        async with AsyncClient(transport=ASGITransport(app),base_url='http://isolated') as client:
            return await client.post('/api/v1/wealth/portfolio/candidates'+path,json=body)
    return asyncio.run(perform())


def test_http_r2_r4_direct_manual_small_weight_and_zero():
    snapshot,facts=fixture_pool(1,2,4);engine,_=service(snapshot,facts)
    for weight in ['0.00000001','0']:
        body={'expected_context':version_context(facts).model_dump(mode='json'),'items':[share('Q00A',weight).model_dump(mode='json')]}
        result=request(engine,'/validate-selection',body)
        assert result.status_code==200 and result.json()['accepted'] is False
        assert result.json()['failures']=={'Q00A':['RISK_EXCEEDS_CAPACITY']}
        assert result.json()['decisions'][0]['user_level']==2 and result.json()['decisions'][0]['product_level']==4


def test_removed_http_AC_switch_preserves_prior_selection():
    snapshot,facts=fixture_pool(1);snapshot.products[1].fields['formal_risk'].value=4;facts.offers['Q00C'].risk_level=4
    engine,_=service(snapshot,facts)
    created=request(engine,'',{'mode':'general'}).json();state=created['session']
    selected=request(engine,'/select',{'session_id':state['session_id'],'expected_version':state['version'],'items':[share('Q00A').model_dump(mode='json')]}).json()
    state=selected['session']
    switched=request(engine,'/switch',{'session_id':state['session_id'],'expected_version':state['version'],'family_id':'fixture-family-00','target':share('Q00C').model_dump(mode='json')})
    assert switched.status_code==410 and switched.json()['detail']['code']=='SHARE_SWITCH_REMOVED'
    assert engine._view(engine.sessions[state['session_id']])==state


def test_http_client_facts_fixture_and_profile_rejected():
    engine,_=service()
    for forbidden in ['risk_level','fixture','purchase_allowed','user_id','assessment']:
        assert request(engine,'',{'mode':'general',forbidden:5}).status_code==422
    assert request(engine,'',{'mode':'general'},user=None).status_code==401


def test_http_missing_source_distinct_from_missing_assessment():
    engine,source=service()
    response=request(engine,'',{'mode':'general'},mount_source=False)
    assert response.status_code==503 and response.json()['detail']['code']=='ASSESSMENT_SOURCE_UNAVAILABLE'
    source.facts.assessment=None
    result=request(engine,'',{'mode':'general'}).json()
    assert result['status']=='unavailable' and result['reason_codes']==['ASSESSMENT_REQUIRED']
    assert result['pipeline']['stages']['matched']==16


def test_http_fixture_not_normal_and_cross_user_cursor_rejected():
    engine,_=service(enable_fixture=False)
    assert request(engine,'',{'mode':'general'}).status_code==403
    engine,_=service();result=request(engine,'',{'mode':'general'}).json();s=result['session']
    response=request(engine,'/next',{'session_id':s['session_id'],'expected_version':s['version'],'cursor':s['cursor']},user='another-user')
    assert response.status_code==403 and response.json()['detail']['code']=='CANDIDATE_SESSION_FORBIDDEN'


def test_http_context_version_conflict_409():
    snapshot,facts=fixture_pool(1);engine,_=service(snapshot,facts)
    context=version_context(facts).model_dump(mode='json');context['assessment_version']=999
    response=request(engine,'/validate-selection',{'expected_context':context,'items':[share('Q00A').model_dump(mode='json')]})
    assert response.status_code==409 and response.json()['detail']['code']=='QUALIFICATION_CONTEXT_STALE'
