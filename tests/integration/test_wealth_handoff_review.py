"""Handoff uses original task/identity and never reuses pre-handoff authorization."""
import pytest
from sqlalchemy import select,func
from tests.integration.test_wealth_bank import store,http_store,command,assess,authorize
from backend.app.services.orchestration import wealth_task as tasks
from backend.app.services.bank import wealth_service as bank
from backend.app.models.wealth import WealthAuthorizationORM,WealthOrderORM,WealthCashHoldORM,WealthPlanORM
from backend.app.integrations.wealth_authorization import WealthPolicyError

@pytest.mark.asyncio
async def test_task_redemption_repreview_returns_bank_holding_ratio_without_submission(store,http_store):
    from tests.integration.test_wealth_bank import move
    client,_,_=http_store
    async with store() as db:
        await assess(db)
        buy=command('review-holding-buy')
        await bank.accept_many(db,'U1',[(buy,await authorize(db,buy))])
        await move(db,12)
        await bank.settle(db,buy.operation_id)
        sell=command('review-holding-sell',direction='redeem',amount=0,quantity='10.0000')
        task=await tasks.create_command(db,'U1','review-holding-session','legacy-v1',sell)
    response=await client.post(f"/api/v1/wealth/tasks/{task['task_id']}/repreview",
        json={'request_id':'review-holding-repreview','expected_version':1})
    assert response.status_code==200,response.text
    result=response.json();quote=result['redemption']
    assert quote['resolved_units']=='10.0000'
    assert 0<float(quote['holding_ratio'])<=1
    assert quote['estimated_market_value_fen']>0 and '不代表实际到账金额' in quote['notice']
    assert result['preview']['authorization_status']=='AWAITING_CONFIRMATION'
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==1
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM).where(WealthCashHoldORM.operation_id==sell.operation_id))==0

@pytest.mark.asyncio
@pytest.mark.parametrize('control',['pause','handoff'])
async def test_control_expires_unsubmitted_authorization_and_fresh_personal_flow_required(store,monkeypatch,control):
    monkeypatch.setenv('WEALTH_OPERATOR_USER_IDS','OP1')
    async with store() as db:
        await assess(db);cmd=command('handoff-unsubmitted-original')
        task=await tasks.create_command(db,'U1','handoff-fresh-session','legacy-v1',cmd)
        aid=await authorize(db,cmd)
        controlled=await tasks.control(db,'U1',task['task_id'],control,{'request_id':'handoff-control-initial','expected_version':1})
        assert (await db.get(WealthAuthorizationORM,aid)).status=='EXPIRED'
        if control=='handoff':
            claimed=await tasks.control(db,'U1',task['task_id'],'claim',{'request_id':'handoff-owner-claim','expected_version':controlled['version']},actor_id='OP1')
            noted=await tasks.control(db,'U1',task['task_id'],'note',{'request_id':'handoff-owner-note','expected_version':claimed['version'],'reason':'核对完成，请本人重新预览'},actor_id='OP1')
            controlled=await tasks.control(db,'U1',task['task_id'],'release',{'request_id':'handoff-owner-release','expected_version':noted['version'],'reason':'交还本人核验'},actor_id='OP1')
            assert controlled['control_mode']=='AGENT' and controlled['status']=='PAUSED'
        resumed=await tasks.control(db,'U1',task['task_id'],'resume',{'request_id':'handoff-personal-resume','expected_version':controlled['version']})
        with pytest.raises(WealthPolicyError) as expired:await bank.accept_many(db,'U1',[(cmd,aid)])
        assert expired.value.code=='AUTHORIZATION_EXPIRED';await db.rollback()
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM))==0
        # An unsubmitted replacement uses a fresh operation identity and personal confirmation.
        fresh=command('handoff-fresh-operation')
        revised=await tasks.revise_command(db,'U1',task['task_id'],resumed['version'],fresh)
        assert revised['control_mode']=='AGENT'
        assert (await bank.accept_many(db,'U1',[(fresh,await authorize(db,fresh))]))[0]['status']=='ACCEPTED'
        assert (await db.get(WealthAuthorizationORM,aid)).status=='EXPIRED'

@pytest.mark.asyncio
async def test_handoff_preserves_accepted_original_order_and_hold(store,monkeypatch):
    monkeypatch.setenv('WEALTH_OPERATOR_USER_IDS','OP1')
    async with store() as db:
        await assess(db);cmd=command('handoff-already-accepted')
        task=await tasks.create_command(db,'U1','handoff-accepted-session','legacy-v1',cmd)
        aid=await authorize(db,cmd);await bank.accept_many(db,'U1',[(cmd,aid)])
        before=await tasks.view(db,await tasks.owned(db,'U1',task['task_id']))
        assert next(n for n in before['nodes'] if n['kind']=='SUBMIT')['status']=='READY'
        # Real bank receipt arrived, but the worker has not observed SUBMIT yet.
        handoff=await tasks.control(db,'U1',task['task_id'],'handoff',{'request_id':'accepted-handoff','expected_version':1})
        claimed=await tasks.control(db,'U1',task['task_id'],'claim',{'request_id':'accepted-claim','expected_version':handoff['version']},actor_id='OP1')
        assert [n['status'] for n in claimed['nodes'] if n['kind'] in {'CONFIRM','VERIFY'}]==[n['status'] for n in before['nodes'] if n['kind'] in {'CONFIRM','VERIFY'}]
        assert (await db.get(WealthAuthorizationORM,aid)).status=='CONSUMED'
        assert (await bank.owned_order(db,'U1',cmd.operation_id)).status=='ACCEPTED'
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM).where(WealthCashHoldORM.status=='HELD'))==1

@pytest.mark.asyncio
async def test_operator_context_is_bound_sanitized_and_other_owner_cannot_read(store,http_store,monkeypatch):
    client,local,app=http_store
    from backend.app.middleware.auth import get_current_user
    async with store() as db:
        cmd=command('review-bound-original')
        task=await tasks.create_command(db,'U1','review-bound-session','legacy-v1',cmd)
        row=await tasks.owned(db,'U1',task['task_id']);row.data={**row.data,'plan_id':'review-plan','plan_version':1}
        db.add(WealthPlanORM(plan_id='review-plan',user_id='U1',version=1,status='AWAITING_CONFIRMATION',data={'context_id':'legacy-v1','request':{'account_id':'A1'},'items':[{'command':cmd.model_dump(),'status':'AWAITING_CONFIRMATION','authorization_id':'must-not-share'}],'verification_reference_id':'must-not-share'}));await db.commit()
    root=f"/api/v1/wealth/operator/tasks/{task['task_id']}"
    await client.post(f"/api/v1/wealth/tasks/{task['task_id']}/handoff",json={'request_id':'review-http-handoff','expected_version':1})
    monkeypatch.setenv('WEALTH_OPERATOR_USER_IDS','U1,U2')
    app.dependency_overrides[get_current_user]=lambda:{'user_id':'U2'}
    assert (await client.post(root+'/claim',json={'request_id':'review-http-claim','expected_version':2})).status_code==200
    context=(await client.get(root+'/context')).json();assert context['plan']['data']['items'][0]['command']['operation_id']==cmd.operation_id
    assert 'must-not-share' not in str(context)
    app.dependency_overrides[get_current_user]=lambda:{'user_id':'U1'}
    assert (await client.get(root+'/context')).status_code==403
    assert (await client.get(root+'/facts')).status_code==403
    assert (await client.get('/api/v1/wealth/operator/tasks')).json()['items']==[]
    app.dependency_overrides[get_current_user]=lambda:{'user_id':'U2'}
    original_call=local.call
    async def changed_scope(method,path,payload):
        result=await original_call(method,path,payload)
        return {**result,'context_id':'different-context'} if path=='/assets' else result
    monkeypatch.setattr(local,'call',changed_scope)
    assert (await client.get(root+'/facts')).status_code==409
    async with store() as db:
        plan=await db.get(WealthPlanORM,'review-plan');plan.version=2;await db.commit()
    assert (await client.get(root+'/context')).status_code==409


@pytest.mark.asyncio
async def test_manual_http_repreview_after_handoff_needs_fresh_personal_authority(store,http_store,monkeypatch):
    client,_,app=http_store
    from backend.app.middleware.auth import get_current_user
    async with store() as db:
        await assess(db);cmd=command('http-renew-original')
        task=await tasks.create_command(db,'U1','http-renew-session','legacy-v1',cmd)
        aid=await authorize(db,cmd)
    base=f"/api/v1/wealth/tasks/{task['task_id']}"
    handoff=(await client.post(base+'/handoff',json={'request_id':'renew-handoff','expected_version':1})).json()
    assert (await client.post(base+'/repreview',json={'request_id':'renew-blocked','expected_version':handoff['version']})).status_code==409
    monkeypatch.setenv('WEALTH_OPERATOR_USER_IDS','U2')
    app.dependency_overrides[get_current_user]=lambda:{'user_id':'U2'}
    operator=f"/api/v1/wealth/operator/tasks/{task['task_id']}"
    claimed=(await client.post(operator+'/claim',json={'request_id':'renew-claim','expected_version':handoff['version']})).json()
    assert (await client.post(base+'/repreview',json={'request_id':'renew-forged','expected_version':claimed['version']})).status_code==404
    assert (await client.post(f'/api/v1/wealth/previews/{aid}/confirm',json={'digest':'0'*64,'disclosure_version':1,'accepted':True})).status_code==404
    assert (await client.post(operator+'/note',json={'request_id':'renew-forged-note','expected_version':claimed['version'],'subject_user_id':'U1'})).status_code==422
    released=(await client.post(operator+'/release',json={'request_id':'renew-release','expected_version':claimed['version'],'reason':'请本人重新核对'})).json()
    assert released['status']=='PAUSED'
    app.dependency_overrides[get_current_user]=lambda:{'user_id':'U1'}
    resumed=(await client.post(base+'/resume',json={'request_id':'renew-resume','expected_version':released['version']})).json()
    payload={'request_id':'renew-personal-preview','expected_version':resumed['version']}
    response=await client.post(base+'/repreview',json=payload)
    assert response.status_code==200,response.text
    result=response.json();preview=result['preview'];fresh=preview['command']
    assert preview['authorization_status']=='AWAITING_CONFIRMATION'
    assert fresh['operation_id']!=cmd.operation_id and fresh['amount_fen']==cmd.amount_fen
    assert fresh['product_id']==cmd.product_id and fresh['account_id']==cmd.account_id
    assert result['task']['version']==resumed['version']+1
    assert (await client.post(base+'/repreview',json=payload)).status_code==409
    async with store() as db:
        assert (await db.get(WealthAuthorizationORM,aid)).status=='EXPIRED'
        assert (await db.get(WealthAuthorizationORM,preview['authorization_id'])).confirmed_at is None
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM))==0
    assert (await client.post(f"/api/v1/wealth/previews/{preview['authorization_id']}/execute",json={})).status_code==403

@pytest.mark.asyncio
async def test_manual_repreview_rejects_unknown_or_existing_original_and_whole_plan(store,http_store):
    client,_,_=http_store
    from backend.app.models.wealth import WealthTaskNodeORM
    async with store() as db:
        cmd=command('no-replacement-unknown')
        task=await tasks.create_command(db,'U1','no-replacement-session','legacy-v1',cmd)
        node=await db.scalar(select(WealthTaskNodeORM).where(WealthTaskNodeORM.task_id==task['task_id'],WealthTaskNodeORM.kind=='SUBMIT'))
        node.status='UNKNOWN';await db.commit()
    path=f"/api/v1/wealth/tasks/{task['task_id']}/repreview"
    body={'request_id':'no-replacement-preview','expected_version':1}
    assert (await client.post(path,json=body)).json()['detail']['code']=='RECONCILIATION_REQUIRED'
    async with store() as db:
        await assess(db)
        node=await db.scalar(select(WealthTaskNodeORM).where(WealthTaskNodeORM.task_id==task['task_id'],WealthTaskNodeORM.kind=='SUBMIT'))
        node.status='READY';await db.commit()
        await bank.accept_many(db,'U1',[(cmd,await authorize(db,cmd))])
    assert (await client.post(path,json=body)).json()['detail']['code']=='RECONCILIATION_REQUIRED'
    async with store() as db:
        row=await tasks.owned(db,'U1',task['task_id']);row.data={**row.data,'plan_id':'whole-plan'};await db.commit()
    assert (await client.post(path,json=body)).json()['detail']['code']=='PLAN_REVIEW_REQUIRED'
