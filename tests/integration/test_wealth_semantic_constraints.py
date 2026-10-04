"""Supplemental scenarios in the existing N03/N04/N06 matrix; recorded typed NLU."""
import pytest
from uuid import uuid4
from sqlalchemy import select,func
from tests.integration.test_wealth_bank import store,http_store,assess
from backend.app.models.wealth import WealthOrderORM,WealthTaskNodeORM
from backend.app.services.ai import wealth_dialogue
from backend.app.services.ai.wealth_model import Intent
from backend.app.domain.wealth_requirements import SemanticFrame
from backend.app.domain.wealth_task import validate_graph


@pytest.mark.asyncio
async def test_unknown_goal_is_conditional_no_loop_and_keeps_explicit_horizon(store,http_store,monkeypatch):
    client,_,_=http_store;monkeypatch.setenv('WEALTH_NLU_MODE','model')
    async with store() as db:await assess(db)
    replies=[Intent(intent='allocate',semantic=SemanticFrame.model_validate({'patches':[
        {'field':'investment_budget','op':'set','quote':'拨一万元','budget':{'mode':'fixed','amount_fen':1000000}},
        {'field':'horizon','op':'set','quote':'六个月内不动','horizon':{'unit':'months','count':6,'text':'六个月内不动'}}]})),
        Intent(intent='clarify',semantic=SemanticFrame.model_validate({'patches':[
            {'field':'investment_goal','op':'mark_unknown','quote':'没主意'}]})),
        Intent(intent='assets',semantic=SemanticFrame())]
    async def recorded(text,context):return replies.pop(0),{'live':False,'provider':'typed-fixture'}
    monkeypatch.setattr(wealth_dialogue,'understand',recorded)
    session={}
    async def send(text):
        result=(await client.post('/api/v1/wealth/messages',json={'message':text,'client_message_id':uuid4().hex,**session})).json()
        session.update(session_id=result['session_id'],version=result['version']);return result
    asked=await send('拨一万元做投资，六个月内不动')
    assert '增长' in asked['message'] and '期限' not in asked['message'],asked
    uncertain=await send('没主意，先给我条件选项')
    assert uncertain['conditional'] and uncertain['kind']=='products',uncertain
    assert uncertain['requirement_card']['fields']['investment_goal']['state']=='unknown'
    assert uncertain['requirement_card']['fields']['horizon']['value']['count']==6
    assert uncertain['recommendation']['constraints']['investment_goal']['state']=='unknown'
    assert uncertain['task']['status']=='COMPLETED'
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        nodes=(await db.scalars(select(WealthTaskNodeORM).where(WealthTaskNodeORM.task_id==asked['task']['task_id']))).all()
        assert any(n.kind=='REQUIRE_INPUT' for n in nodes) and any(n.kind=='RESEARCH' for n in nodes)
        validate_graph([{'node_id':n.node_id,'kind':n.kind,'depends_on':n.depends_on,'completion_condition':n.data['completion_condition']} for n in nodes])


@pytest.mark.asyncio
async def test_member_two_typed_create_and_idempotency_are_scoped(store,http_store,monkeypatch):
    client,_,_=http_store
    async def forbidden(*args):raise AssertionError('typed subtask must not call model')
    monkeypatch.setattr(wealth_dialogue,'understand',forbidden)
    request={'context_id':'legacy-v1','request_id':uuid4().hex,'parent_task_id':'parent-2',
        'raw_text':'拨一万元，来年春天再用，追求缓慢增长',
        'parsed_intent':{'intent':'allocate','semantic':{'patches':[
            {'field':'investment_budget','op':'set','quote':'拨一万元','budget':{'mode':'fixed','amount_fen':1000000}},
            {'field':'horizon','op':'set','quote':'来年春天再用','horizon':{'unit':'date','use_date':'2027-03-01','text':'来年春天再用'}},
            {'field':'investment_goal','op':'set','quote':'追求缓慢增长','goal':'growth'}]}}}
    async with store() as db:await assess(db)
    response=await client.post('/api/v1/wealth/subtasks',json=request)
    assert response.status_code==200,response.text
    created=response.json();assert created['schema_version']=='wealth-subtask-v1' and created['parent_task_id']=='parent-2'
    repeat=await client.post('/api/v1/wealth/subtasks',json=request)
    assert repeat.json()==created
    changed=await client.post('/api/v1/wealth/subtasks',json={**request,'parent_task_id':'other-parent'})
    assert changed.status_code==409
    task=(await client.get('/api/v1/wealth/subtasks/'+created['child_task_id'])).json()
    assert task['child_task_id']==created['child_task_id']
    wrong=await client.post('/api/v1/wealth/subtasks',json={**request,'request_id':uuid4().hex,'context_id':'other-user'})
    assert wrong.status_code==409
