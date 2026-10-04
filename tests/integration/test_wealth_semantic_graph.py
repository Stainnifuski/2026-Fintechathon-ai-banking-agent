"""N05/N06/D01: semantic dependencies use the actual durable graph, not a UI diagram."""
import pytest
from uuid import uuid4
from sqlalchemy import select,func
from tests.integration.test_wealth_bank import store,http_store,assess
from backend.app.models.wealth import WealthOrderORM
from backend.app.domain.wealth_task import validate_graph


@pytest.mark.asyncio
async def test_ambiguity_assessment_resume_research_plan_and_advice_only(store,http_store):
    client,_,_=http_store
    session={}
    async def send(text,semantic,intent='allocate'):
        request={'context_id':'legacy-v1','request_id':uuid4().hex,'raw_text':text,
            'parsed_intent':{'intent':intent,'semantic':semantic},**session}
        response=await client.post('/api/v1/wealth/subtasks',json=request)
        assert response.status_code==200,response.text
        result=response.json();session.update(session_id=result['session_id'],version=result['session_version'])
        return result
    confused=await send('一半，可能是上次的一半，也可能账户的一半',{
        'ambiguities':['一半是当前可用现金，还是原规划预算？'],'ambiguity_kind':'reference'})
    assert confused['waiting_reason']=='USER_INPUT'
    assert any(n['kind']=='REQUIRE_INPUT' and n['status']=='WAITING' for n in confused['result']['nodes'])
    text='先研究再配置，投入一万，六个月不用，兼顾两者'
    frame={'intents':[{'action':'research','relation':'sequential'},{'action':'plan','relation':'sequential'}],
        'patches':[{'field':'investment_budget','op':'set','quote':'投入一万','budget':{'mode':'fixed','amount_fen':1000000}},
            {'field':'horizon','op':'set','quote':'六个月不用','horizon':{'unit':'months','count':6,'text':'六个月不用'}},
            {'field':'investment_goal','op':'set','quote':'兼顾两者','goal':'balanced'}]}
    waiting=await send(text,frame)
    assert waiting['waiting_reason']=='ASSESSMENT_REQUIRED',waiting
    assert any(n['kind']=='ASSESS' and n['status']=='WAITING' for n in waiting['result']['nodes'])
    async with store() as db:await assess(db)
    planned=await send('继续',{},'allocate')
    assert planned['cards']['kind']=='plan',planned
    nodes=planned['result']['nodes'];research=[n for n in nodes if n['kind']=='RESEARCH' and n['status']=='COMPLETED']
    assert research and any(n['kind']=='READ_FACTS' and research[-1]['node_id'] in n['depends_on'] for n in nodes)
    validate_graph(nodes)
    advice=await send('金额还拿不准，先展示有条件选项别下单',{'patches':[
        {'field':'investment_budget','op':'mark_unknown','quote':'金额还拿不准'},
        {'field':'advice_only','op':'set','quote':'先展示有条件选项别下单','flag':True}]})
    assert advice['cards']['conditional'] is True,advice
    assert not any(n['kind'] in {'CONFIRM','VERIFY','SUBMIT'} for n in advice['result']['nodes'])
    async with store() as db:assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
