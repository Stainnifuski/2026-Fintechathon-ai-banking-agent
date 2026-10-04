"""C02–C07: session drafts are disposable, financial facts are not."""
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from tests.integration.test_wealth_bank import store,http_store,assess,authorize,command,move
from backend.app.models import AccountORM,TransactionORM
from backend.app.models.wealth import (WealthConversationORM as Conversation,WealthPlanORM as Plan,
    WealthProfileORM,WealthGoalORM,
    WealthTaskORM as Task,WealthTaskNodeORM as Node,WealthOrderORM as Order,WealthLotORM as Lot,
    WealthAuthorizationORM as Authorization,WealthSettlementORM as Settlement)
from backend.app.services.orchestration import wealth_lifecycle as lifecycle,wealth_task as tasks
from backend.app.services.orchestration.wealth_task_worker import tick
from backend.app.services.bank import wealth_service as bank
from backend.app.domain.time import utc_now


async def facts(db):
    return {m.__tablename__:[{c.name:getattr(r,c.name) for c in m.__table__.columns}
        for r in await db.scalars(select(m).order_by(list(m.__table__.primary_key)[0]))]
        for m in (AccountORM,TransactionORM,Order,Lot,Settlement)}


@pytest.mark.asyncio
async def test_window_patch_dirty_close_expiry_and_financial_isolation(store,http_store):
    client,_,_=http_store
    async with store() as db:await assess(db);before=await facts(db)
    session=(await client.post('/api/v1/wealth/sessions',json={})).json()
    semantic={'patches':[
        {'field':'investment_budget','op':'set','quote':'表格金额','budget':{'mode':'proportion','ratio':'0.5','base':'available_cash'}},
        {'field':'horizon','op':'set','quote':'表格期限','horizon':{'unit':'months','count':6,'text':'半年后用'}},
        {'field':'investment_goal','op':'set','quote':'表格目标','goal':'balanced'}]}
    base='/api/v1/wealth/sessions/'+session['session_id']
    reply=await client.post(base+'/requirements',json={'version':session['version'],'client_message_id':uuid4().hex,'semantic':semantic})
    assert reply.status_code==200,reply.text
    reply=reply.json();plan=reply['plan'];assert plan['request']['budget_fen']==1000000
    assert 'spending_purpose' not in reply['requirement_card']['fields']
    preview=await client.post('/api/v1/wealth/plans/'+plan['plan_id']+'/preview',json={'version':plan['version']})
    assert preview.status_code==200,preview.text
    assert (await client.post(base+'/dirty',json={})).status_code==200
    assert (await client.post('/api/v1/wealth/plans/'+plan['plan_id']+'/preview',json={'version':plan['version']})).status_code==409
    async with store() as db:
        assert all(a.status=='EXPIRED' for a in await db.scalars(select(Authorization)))
        assert await facts(db)==before
    # Typed edits don't call a model, update the same state and preserve other fields.
    semantic={'patches':[{'field':'investment_budget','op':'set','quote':'修改金额','budget':{'mode':'fixed','amount_fen':800000}}]}
    updated=await client.post(base+'/requirements',json={'version':reply['version'],'client_message_id':uuid4().hex,'semantic':semantic})
    assert updated.status_code==200,updated.text
    updated=updated.json();assert updated['plan']['request']['budget_fen']==800000
    assert updated['requirement_card']['fields']['horizon']['resolved_days']>180
    invalid={'patches':[{'field':'investment_budget','op':'set','quote':'负数','budget':{'mode':'fixed','amount_fen':-1}}]}
    assert (await client.post(base+'/requirements',json={'version':updated['version'],'client_message_id':uuid4().hex,'semantic':invalid})).status_code==422
    fresh=(await client.post('/api/v1/wealth/sessions',json={})).json()
    assert fresh['session_id']!=session['session_id']
    assert (await client.get(base)).status_code==404
    assert (await client.get('/api/v1/wealth/messages/history')).json() is None
    assert (await client.get('/api/v1/wealth/messages/archives')).json()=={'items':[]}
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(Plan))==0
        assert await facts(db)==before
        current=await db.get(Conversation,fresh['session_id']);current.updated_at=utc_now()-timedelta(hours=25);await db.commit()
        await lifecycle.expire(db)
        assert await db.get(Conversation,fresh['session_id']) is None
        assert await facts(db)==before


@pytest.mark.asyncio
async def test_accepted_single_is_compacted_bank_still_settles_and_replays(store,http_store):
    _,local,_=http_store
    async with store() as db:
        await assess(db);session=await lifecycle.start(db,'U1','legacy-v1')
        cmd=command('workspace-single-buy',product='MF001')
        task=await tasks.create_command(db,'U1',session['session_id'],'legacy-v1',cmd)
        auth=await authorize(db,cmd)
        await bank.accept_many(db,'U1',[(cmd,auth)])
        before=await facts(db)
        await lifecycle.close(db,'U1',session['session_id'])
        assert await facts(db)==before
        row=await db.get(Task,task['task_id']);assert row.data['compacted']
        assert await db.scalar(select(func.count()).select_from(Node).where(Node.task_id==task['task_id']))==0
        assert (await local.query('U1',cmd.operation_id))['status']=='ACCEPTED'
        await move(db,12);assert (await bank.settle(db,cmd.operation_id))['status']=='SETTLED'
        assert (await bank.accept_many(db,'U1',[(cmd,auth)]))[0]['status']=='SETTLED'
        assert await db.scalar(select(func.count()).select_from(TransactionORM).where(TransactionORM.operation_id==cmd.operation_id))==1
        settled=await facts(db)
        minimal=dict((await db.get(Task,task['task_id'])).data)
        await lifecycle.clean_archives(db,apply=True);await lifecycle.clean_archives(db,apply=True)
        assert await facts(db)==settled
        assert (await db.get(Task,task['task_id'])).data==minimal
        assert await db.scalar(select(func.count()).select_from(Conversation))==0


@pytest.mark.asyncio
async def test_closed_complex_keeps_original_settlement_and_requires_fresh_buy(store,http_store):
    client,local,_=http_store
    async with store() as db:
        await assess(db);buy=command('workspace-opening',product='MF001')
        await bank.accept_many(db,'U1',[(buy,await authorize(db,buy))]);await move(db,12);await bank.settle(db,buy.operation_id)
        session=await lifecycle.start(db,'U1','legacy-v1')
        sell=command('workspace-complex-sell',product='MF001',direction='redeem',amount=0,quantity='100.0000')
        task=await tasks.create_command(db,'U1',session['session_id'],'legacy-v1',sell,sell_then_buy=True)
        await bank.accept_many(db,'U1',[(sell,await authorize(db,sell))]);before=await facts(db)
        await lifecycle.close(db,'U1',session['session_id']);assert await facts(db)==before
        row=await db.get(Task,task['task_id']);assert row.data['session_closed'] and row.status=='WAITING'
        await move(db,18);await bank.settle(db,sell.operation_id)
        for _ in range(3):await tick(db,task['task_id'],local)
        graph=await tasks.view(db,await tasks.owned(db,'U1',task['task_id']))
        assert any(n['kind']=='REREAD_CASH' and n['status']=='COMPLETED' for n in graph['nodes'])
        assert not any(o.direction=='purchase' and o.operation_id!='workspace-opening' for o in await db.scalars(select(Order)))
    fresh=(await client.post('/api/v1/wealth/sessions',json={})).json()
    resumed=await client.post('/api/v1/wealth/sessions/'+fresh['session_id']+'/continue',json={'task_id':task['task_id']})
    assert resumed.status_code==200,resumed.text
    assert resumed.json()['task']['task_id']==task['task_id']
    # Existing continuation graph is only admitted after actual settlement, then needs new auth.
    async with store() as db:
        new=buy.model_copy(update={'operation_id':'workspace-after-sell','plan_id':'WPworkspace-followup','plan_version':1})
        plan={'plan_id':new.plan_id,'version':1,'stage':'BUY','request':{'kind':'new_cash','account_id':'A1','parent_task_id':task['task_id']},
            'profile_version':(await db.get(WealthProfileORM,'U1')).version,
            'goal_versions':{g.goal_id:g.version for g in await db.scalars(select(WealthGoalORM).where(WealthGoalORM.user_id=='U1'))},
            'items':[{'command':new.model_dump(),'status':'AWAITING_CONFIRMATION'}]}
        continued=await tasks.register_plan(db,'U1',fresh['session_id'],'legacy-v1',plan)
        assert continued['task_id']==task['task_id']
        assert not (await db.get(Task,task['task_id'])).data['session_closed']
        db.add(Plan(plan_id=new.plan_id,user_id='U1',version=1,status='AWAITING_CONFIRMATION',data=plan));await db.commit()
    refused=await client.post('/api/v1/wealth/plans/'+new.plan_id+'/execute',json={'version':1})
    assert refused.status_code==409 and refused.json()['detail']['code']=='AUTHORIZATION_REQUIRED'
    async with store() as db:
        await bank.accept_many(db,'U1',[(new,await authorize(db,new))]);await move(db,22)
        assert (await bank.settle(db,new.operation_id))['status']=='SETTLED'
        for _ in range(8):await tick(db,task['task_id'],local)
        row=await db.get(Task,task['task_id'],populate_existing=True)
        assert row.status=='COMPLETED' and row.data['compacted']
        assert await db.scalar(select(func.count()).select_from(Node).where(Node.task_id==task['task_id']))==0
        assert await db.scalar(select(func.count()).select_from(Order))==3


@pytest.mark.asyncio
async def test_unknown_submission_is_not_deleted_or_resubmitted(store,http_store):
    _,local,_=http_store
    async with store() as db:
        await assess(db);session=await lifecycle.start(db,'U1','legacy-v1')
        cmd=command('workspace-unknown',product='MF001')
        task=await tasks.create_command(db,'U1',session['session_id'],'legacy-v1',cmd)
        node=await db.scalar(select(Node).where(Node.task_id==task['task_id'],Node.kind=='SUBMIT'))
        node.status='UNKNOWN';await db.commit()
        await lifecycle.close(db,'U1',session['session_id']);await lifecycle.clean_archives(db,apply=True)
        assert (await db.get(Node,node.node_id)).status=='UNKNOWN'
        before=await facts(db)
        await tick(db,task['task_id'],local)
        assert await facts(db)==before
        assert (await db.get(Node,node.node_id,populate_existing=True)).status=='UNKNOWN'
