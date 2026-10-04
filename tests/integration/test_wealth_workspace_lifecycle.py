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
async def test_window_natural_edit_close_expiry_and_financial_isolation(store,http_store,monkeypatch):
    from backend.app.services.ai import wealth_dialogue
    from backend.app.services.ai.wealth_model import Intent
    from backend.app.domain.wealth_requirements import SemanticFrame
    client,_,_=http_store
    monkeypatch.setenv('WEALTH_NLU_MODE','model')
    async def parsed(text,context):
        amount=100000 if text=='我要投资1000元' else 200000
        return Intent(intent='allocate',semantic=SemanticFrame.model_validate({'patches':[
            {'field':'investment_budget','op':'set','quote':text,'budget':{'mode':'fixed','amount_fen':amount}}]})),{'live':False,'provider':'deterministic-semantic-fixture'}
    monkeypatch.setattr(wealth_dialogue,'understand',parsed)
    async with store() as db:await assess(db);before=await facts(db)
    session=(await client.post('/api/v1/wealth/sessions',json={})).json()
    base='/api/v1/wealth/sessions/'+session['session_id']
    async def send(text,version):
        result=await client.post('/api/v1/wealth/messages',json={'message':text,'session_id':session['session_id'],'version':version,'client_message_id':uuid4().hex})
        assert result.status_code==200,result.text
        return result.json()
    reply=await send('我要投资1000元',session['version'])
    assert reply['kind']=='plan',reply
    plan=reply['plan'];assert sum(i['command']['amount_fen'] for i in plan['items'])==100000
    assert 'spending_purpose' not in reply['requirement_card']['fields']
    assert 'horizon' not in reply['requirement_card']['fields']
    preview=await client.post('/api/v1/wealth/plans/'+plan['plan_id']+'/preview',json={'version':plan['version']})
    assert preview.status_code==200,preview.text
    updated=await send('把投资金额改为2000元',reply['version'])
    assert updated['kind']=='plan',updated
    assert updated['plan']['plan_id']==plan['plan_id'] and updated['plan']['version']==plan['version']+1
    assert sum(i['command']['amount_fen'] for i in updated['plan']['items'])==200000
    assert (await client.post('/api/v1/wealth/plans/'+plan['plan_id']+'/preview',json={'version':plan['version']})).status_code==409
    async with store() as db:
        assert all(a.status=='EXPIRED' for a in await db.scalars(select(Authorization)))
        assert await facts(db)==before
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
        closed_at=(await db.get(Conversation,session['session_id'])).data['closed_at']
        await lifecycle.close(db,'U1',session['session_id'])
        assert (await db.get(Conversation,session['session_id'])).data['closed_at']==closed_at
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


@pytest.mark.asyncio
async def test_amount_only_assessment_resume_and_repeatable_selection(store,http_store,monkeypatch):
    from backend.app.services.ai import wealth_dialogue
    from backend.app.services.ai.wealth_model import Intent
    from backend.app.domain.wealth_requirements import SemanticFrame
    client,_,_=http_store
    monkeypatch.setenv('WEALTH_NLU_MODE','model')
    async def parsed(text,context):
        frame={} if text=='继续' else {'patches':[{'field':'investment_budget','op':'set','quote':text,
            'budget':{'mode':'fixed','amount_fen':100000}}]}
        return Intent(intent='allocate',semantic=SemanticFrame.model_validate(frame)),{'live':False,'provider':'deterministic-semantic-fixture'}
    monkeypatch.setattr(wealth_dialogue,'understand',parsed)
    session=(await client.post('/api/v1/wealth/sessions',json={})).json()
    async def send(text,session):
        result=await client.post('/api/v1/wealth/messages',json={'message':text,'client_message_id':uuid4().hex,**session})
        assert result.status_code==200,result.text
        reply=result.json();session.update(session_id=reply['session_id'],version=reply['version'])
        return reply
    waiting=await send('想拿一千块做投资',session)
    assert waiting['kind']=='assessment_required',waiting
    async with store() as db:await assess(db)
    planned=await send('继续',session);assert planned['kind']=='plan',planned
    expected=[(i['command']['product_id'],i['command']['amount_fen']) for i in planned['plan']['items']]
    fresh=(await client.post('/api/v1/wealth/sessions',json={})).json()
    repeated=await send('想拿一千块做投资',fresh)
    assert repeated['kind']=='plan'
    assert [(i['command']['product_id'],i['command']['amount_fen']) for i in repeated['plan']['items']]==expected
    await client.post('/api/v1/wealth/sessions/'+fresh['session_id']+'/close',json={})
    integrated=await client.post('/api/v1/wealth/subtasks',json={'context_id':'legacy-v1',
        'request_id':uuid4().hex,'raw_text':'安排1000元投资','parsed_intent':{'intent':'allocate','semantic':{'patches':[
            {'field':'investment_budget','op':'set','quote':'1000元','budget':{'mode':'fixed','amount_fen':100000}}]}}})
    assert integrated.status_code==200,integrated.text
    assert integrated.json()['cards']['kind']=='plan'
    assert sum(i['command']['amount_fen'] for i in integrated.json()['cards']['plan']['items'])==100000
