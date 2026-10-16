"""B5 actions use real HTTP handlers and disposable bank data, never NLP."""
import pytest
from sqlalchemy import select,func
from tests.integration.test_wealth_bank import store,http_store
from tests.integration.test_wealth_redemption_input import setup_holding
from backend.app.models.wealth import WealthAuthorizationORM,WealthUnitHoldORM,WealthTaskORM
from backend.app.middleware.auth import get_current_user,create_access_token

def action(name='list_positions',params=None,**extra):
    return dict(schema_version='wealth-action-v1',request_id='actions-request-0001',context_id='legacy-v1',
                action=name,params=params if params is not None else {'page':1,'page_size':5},**extra)

@pytest.fixture(autouse=True)
def no_nlp(monkeypatch):
    from backend.app.services.ai import wealth_model,wealth_dialogue
    async def fail(*args,**kwargs):raise AssertionError('Structured actions must not call NLP')
    monkeypatch.setattr(wealth_dialogue,'message',fail)
    monkeypatch.setattr(wealth_model,'understand',fail)
    monkeypatch.setattr(wealth_model,'explain',fail)

@pytest.mark.asyncio
async def test_strict_session_replay_scope_and_page(store,http_store):
    await setup_holding(store)
    client,_,app=http_store
    payload=action()
    reply=await client.post('/api/v1/wealth/actions',json=payload)
    assert reply.status_code==200,reply.text
    data=reply.json();assert data['status']=='ok' and data['data']['total']==1
    assert data['data']['table_data']['object_type']=='positions'
    assert data['data']['table_data']['scope_key']==data['data']['scope_key']
    assert data['data']['snapshot_id'].startswith('WASNAP-')
    assert data['data']['table_data']['rows']==data['data']['rows']
    row=data['data']['rows'][0]
    assert row['position_key']=='A1:MF001:1' and row['units']=='1000.0000'
    assert (await client.post('/api/v1/wealth/actions',json=payload)).json()==data
    assert (await client.post('/api/v1/wealth/actions',json={**payload,'params':{'page':2,'page_size':5}})).status_code==409
    session={'session_id':data['session_id'],'expected_session_version':data['session_version']}
    next_req={**action('list_orders',{'page':1,'page_size':1},**session),'request_id':'actions-request-0002'}
    next_reply=await client.post('/api/v1/wealth/actions',json=next_req)
    assert next_reply.status_code==200 and next_reply.json()['data']['total']==1
    stale=await client.post('/api/v1/wealth/actions',json={**next_req,'request_id':'actions-request-stale'})
    assert stale.status_code==409 and stale.json()['detail']['code']=='STALE_SESSION'
    for invalid in [
        {**payload,'action':'execute'}, {**payload,'params':{'page':True,'page_size':5}},
        {**payload,'params':{'page':1,'page_size':5,'accepted':True}},
        {**payload,'user_id':'U2'}, {**payload,'session_id':data['session_id']},
        {**payload,'schema_version':'wealth-subtask-v1'},
        {**payload,'params':{'raw_text':'buy now'}},
    ]:
        assert (await client.post('/api/v1/wealth/actions',json=invalid)).status_code==422
    assert (await client.post('/api/v1/wealth/subtasks',json=payload)).status_code==422
    foreign={**payload,'request_id':'actions-foreign-0001','selection_context':{'object_type':'positions','ids':['A2:MF001:1']}}
    assert (await client.post('/api/v1/wealth/actions',json=foreign)).status_code==409
    assert (await client.post('/api/v1/wealth/actions',json={**foreign,'context_id':'other'})).status_code==409
    app.dependency_overrides.pop(get_current_user)
    client.headers['Authorization']='Bearer '+create_access_token({'sub':'U2'})
    assert (await client.post('/api/v1/wealth/actions',json={**next_req,'request_id':'actions-other-user'})).status_code==404

@pytest.mark.asyncio
async def test_clarify_three_filters_and_injected_A_adapter(store,http_store):
    client,_,app=http_store
    for i,(name,params) in enumerate([
        ('plan_portfolio',{}),('recommend_products',{'filter':{'mode':'general'}}),
        ('recommend_products',{'filter':{'mode':'category','category':'bond_fund'}}),
        ('recommend_products',{'filter':{'mode':'theme','theme_key':'high_dividend'}}),
    ]):
        result=await client.post('/api/v1/wealth/actions',json={**action(name,params),'request_id':f'actions-shortcut-{i}'})
        assert result.status_code==200,result.text
        assert result.json()['status']=='blocked' and result.json()['data']['code']=='PORTFOLIO_PROVIDER_UNAVAILABLE'
    missing=await client.post('/api/v1/wealth/actions',json={**action('preview_redemption',{}),'request_id':'actions-no-object'})
    assert missing.json()['status']=='clarify'
    assert {r['field'] for r in missing.json()['required_fields']}=={'redemption.position_key','redemption.input'}
    bad=action('recommend_products',{'filter':{'mode':'general','category':'bond_fund'}})
    assert (await client.post('/api/v1/wealth/actions',json=bad)).status_code==422
    calls=[]
    async def adapter(**kwargs):
        calls.append(kwargs['params']['filter'])
        return {'status':'no_theme_match','items':[],'filter':kwargs['params']['filter']}
    app.state.wealth_action_handlers={'recommend_products':adapter}
    for i,category in enumerate(['bond_fund','equity_fund']):
        reply=await client.post('/api/v1/wealth/actions',json={**action('recommend_products',{'filter':{'mode':'category','category':category}}),'request_id':f'actions-adapter-{i}'})
        assert reply.json()['status']=='ok'
        assert reply.json()['data']['filter']['category']==category
    assert len(calls)==2

@pytest.mark.asyncio
async def test_revision_preserves_optional_growth_preference_for_A(store,http_store):
    client,_,app=http_store
    calls=[]
    async def adapter(**kwargs):
        calls.append(kwargs['params']['selection'])
        return {'selection':kwargs['params']['selection']}
    app.state.wealth_action_handlers={'revise_portfolio':adapter}
    selection={'portfolio_id':'portfolio-contract','snapshot_id':'snapshot-contract','context_id':'legacy-v1',
               'expected_version':1,'items':[{'product_id':'MF001','product_version':1,'share_class':None,'target_ratio_total':'1'}]}
    for i,preference in enumerate(['0.625',None,'0','1']):
        payload={**action('revise_portfolio',{'selection':{**selection,'growth_ratio_ex_cash':preference}}),
                 'request_id':f'actions-growth-valid-{i}'}
        response=await client.post('/api/v1/wealth/actions',json=payload)
        assert response.status_code==200,response.text
        assert response.json()['status']=='ok',response.text
        assert calls[-1]['growth_ratio_ex_cash']==preference
    for i,preference in enumerate([True,0.625,'-0.1','1.01','nan']):
        payload={**action('revise_portfolio',{'selection':{**selection,'growth_ratio_ex_cash':preference}}),
                 'request_id':f'actions-growth-invalid-{i}'}
        assert (await client.post('/api/v1/wealth/actions',json=payload)).status_code==422
    assert len(calls)==4

@pytest.mark.asyncio
async def test_redemption_and_cancellation_preview_never_execute(store,http_store):
    await setup_holding(store)
    client,_,_=http_store
    req=action('preview_redemption',{'redemption':{'context_id':'legacy-v1','input_mode':'market_value','market_value_fen':100000}},
               selection_context={'object_type':'positions','ids':['A1:MF001:1']})
    reply=await client.post('/api/v1/wealth/actions',json=req)
    assert reply.status_code==200,reply.text
    dto=reply.json();assert dto['status']=='ok' and dto['data']['resolved_units']=='500.0000'
    again=await client.post('/api/v1/wealth/actions',json=req);assert again.json()==dto
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthAuthorizationORM).where(WealthAuthorizationORM.status=='AWAITING_CONFIRMATION'))==1
        assert await db.scalar(select(func.count()).select_from(WealthUnitHoldORM))==0
        task=await db.scalar(select(WealthTaskORM));assert task.session_id==dto['session_id']
    auth=dto['data']['preview']['authorization_id']
    assert (await client.post(f'/api/v1/wealth/previews/{auth}/execute',json={})).status_code==403
    detail=await client.post('/api/v1/wealth/actions',json={**action('show_product_detail',{}),'request_id':'actions-position-detail','selection_context':{'object_type':'positions','ids':['A1:MF001:1']}})
    assert detail.status_code==200 and detail.json()['data']['product_id']=='MF001'
    mismatch={**req,'request_id':'actions-mismatch','params':{'redemption':{**req['params']['redemption'],'position_key':'A2:MF001:1'}}}
    assert (await client.post('/api/v1/wealth/actions',json=mismatch)).status_code==409
    cancel=await client.post('/api/v1/wealth/actions',json={**action('preview_order_cancellation',{'operation_ids':['redemption-input-opening']}),'request_id':'actions-cancel-query'})
    assert cancel.status_code==200 and cancel.json()['status']=='ok'
    assert cancel.json()['data']['items'][0]['eligibility']!='ALLOWED'
    returns=await client.post('/api/v1/wealth/actions',json={**action('show_wealth_returns',{'scope':'all','period':'day','anchor_date':'2026-10-12'}),'request_id':'actions-returns-0001'})
    assert returns.status_code==200,returns.text


@pytest.mark.asyncio
async def test_multi_position_amount_clarifies_and_human_task_blocks(store,http_store):
    from tests.integration.test_wealth_bank import command,authorize,move
    from backend.app.services.bank import wealth_service as bank
    await setup_holding(store)
    async with store() as db:
        buy=command('actions-second-holding',product='MF002',amount=100000)
        await bank.accept_many(db,'U1',[(buy,await authorize(db,buy))])
        await move(db,20);await bank.settle(db,buy.operation_id)
    client,_,_=http_store
    payload=action('preview_redemption',{'redemption':{'context_id':'legacy-v1','input_mode':'market_value','market_value_fen':100000}},
        selection_context={'object_type':'positions','ids':['A1:MF001:1','A1:MF002:1']})
    response=await client.post('/api/v1/wealth/actions',json=payload)
    assert response.status_code==200,response.text
    assert response.json()['status']=='clarify'
    assert len(response.json()['required_fields'][0]['options'])==2
    single={**payload,'request_id':'actions-single-0001','selection_context':{'object_type':'positions','ids':['A1:MF001:1']}}
    first=await client.post('/api/v1/wealth/actions',json=single)
    assert first.json()['status']=='ok',first.text
    async with store() as db:
        task=await db.scalar(select(WealthTaskORM));task.control_mode='HUMAN';await db.commit()
    blocked=await client.post('/api/v1/wealth/actions',json={**single,'request_id':'actions-human-0001'})
    assert blocked.json()['status']=='blocked' and blocked.json()['data']['code']=='TASK_CONTROL_BLOCKED'
