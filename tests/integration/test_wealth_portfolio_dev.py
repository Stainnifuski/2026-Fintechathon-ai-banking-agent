"""独立验收薄接线；不启动或写入银行。"""
from fastapi.testclient import TestClient
from backend.app.middleware.auth import create_access_token
from scripts.wealth_portfolio_dev import app, services, build_service

def client(scenario='R4'):
    services[scenario]=build_service(scenario)
    return TestClient(app), {'Authorization':'Bearer '+create_access_token({'sub':'fixture-user'}),'X-Portfolio-Scenario':scenario}

def test_controls_snapshot_no_implicit_plan():
    c,h=client();r=c.get('/dev/context',headers=h)
    assert r.status_code==200 and r.json()['can_edit'] is True
    assert r.json()['asset_totals']['available_cash_fen']==190000
    assert c.get('/api/v1/wealth/portfolio/snapshot',headers=h).json()['basis_status']=='fixture_only'

def test_selection_binding_create_and_wrong_context():
    c,h=client();ctx='fixture-context-R4'
    result=c.post('/api/v1/wealth/portfolio/recommendations',headers=h,json={'context_id':ctx,'filter':{'mode':'general'}}).json()
    session=result['session_binding'];ref=result['items'][0]['evidence']['default_share']
    action={'session_id':session['session_id'],'expected_version':session['version'],'items':[ref]}
    assert c.post('/dev/candidates/select',headers=h,json={'context_id':'wrong','action':action}).status_code==409
    r=c.post('/dev/candidates/select',headers=h,json={'context_id':ctx,'action':action});assert r.status_code==200
    state=r.json()['session']; assert r.json()['selection']['accepted']
    request={'context_id':ctx,'selected':[{k:ref[k] for k in ('product_id','product_version','share_class')}],
             'candidate_binding':{'session_id':state['session_id'],'expected_version':state['version'],'filter':{'mode':'general'}}}
    # Creating without enough buckets may be infeasible; binding itself is valid.
    reply=c.post('/api/v1/wealth/portfolio/plans',headers=h,json=request)
    assert reply.status_code in (200,422)
    assert 'BINDING' not in str(reply.json()) and 'SELECTION_STALE' not in str(reply.json())

def test_only_c_and_no_fake_single_ac():
    for scenario in ('only-C','single-share'):
        c,h=client(scenario)
        r=c.post('/api/v1/wealth/portfolio/recommendations',headers=h,json={'context_id':'fixture-context-'+scenario,'filter':{'mode':'general'}})
        assert r.status_code==200 and r.json()['items']
        for row in r.json()['items']:
            assert row['evidence']['default_share']['share_class']==('C' if scenario=='only-C' else 'A')

def test_readonly_read_and_write_reject_and_source_failure():
    c,h=client();c.post('/dev/change/read-only?scenario=R4')
    assert c.get('/dev/context',headers=h).json()['can_edit'] is False
    assert c.get('/api/v1/wealth/portfolio/snapshot',headers=h).status_code==200
    assert c.post('/api/v1/wealth/portfolio/plans',headers=h,json={'context_id':'fixture-context-R4'}).status_code==409
    c,h=client('source-unavailable');assert c.get('/dev/context',headers=h).status_code==503

def test_fixture_change_not_in_bank_app():
    from scripts.wealth_portfolio_bank_dev import create_app
    from pathlib import Path
    bank=create_app('http://127.0.0.1:8010','missing','missing',Path.cwd())
    paths=set(bank.openapi()['paths'])
    assert '/dev/bootstrap' not in paths and '/dev/reset' not in paths and '/dev/change/{change}' not in paths
    assert '/dev/candidates/select' in paths
