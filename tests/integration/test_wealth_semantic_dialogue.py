"""Typed recorded responses through real HTTP/database orchestration (not live NLU)."""
import pytest
from uuid import uuid4
from sqlalchemy import select,func
from tests.integration.test_wealth_bank import store,http_store,assess
from backend.app.models import AccountORM
from backend.app.models.wealth import WealthOrderORM,WealthTaskORM,WealthConversationORM
from backend.app.services.ai import wealth_dialogue
from backend.app.services.ai.wealth_model import Intent
from backend.app.domain.wealth_requirements import SemanticFrame


@pytest.mark.asyncio
async def test_pending_purchase_does_not_become_planning_dependency(store,http_store,monkeypatch):
    client,_,_=http_store
    monkeypatch.setenv('WEALTH_NLU_MODE','model')
    responses=[Intent(intent='purchase',product_refs=['MF002'],amount_fen=100000),
        Intent(intent='allocate',semantic=SemanticFrame()),
        Intent(intent='allocate',semantic=SemanticFrame.model_validate({'patches':[
            {'field':'investment_budget','op':'set','quote':'投入5成','budget':{'mode':'proportion','ratio':'0.5','base':'available_cash'}}]})),
        Intent(intent='allocate',semantic=SemanticFrame.model_validate({'patches':[
            {'field':'horizon','op':'set','quote':'半年','horizon':{'unit':'months','count':6,'text':'半年'}}]})),
        Intent(intent='modify',semantic=SemanticFrame.model_validate({'patches':[
            {'field':'declared_assets','op':'set','quote':'我有2万','amount_fen':2000000}]})),
        Intent(intent='assets')]
    async def recorded(text,context):
        assert 'recent_turns' in context and 'last_question' in context
        return responses.pop(0),{'live':False,'provider':'recorded-typed-fixture'}
    monkeypatch.setattr(wealth_dialogue,'understand',recorded)
    async with store() as db:
        await assess(db)
        account=await db.get(AccountORM,'A1');account.balance_fen=1922900
        await db.commit()
    session={}
    async def send(text):
        result=await client.post('/api/v1/wealth/messages',json={'message':text,'client_message_id':uuid4().hex,**session})
        assert result.status_code==200,result.text
        reply=result.json();session.update(session_id=reply['session_id'],version=reply['version'])
        return reply
    purchase=await send('申购MF002一千元')
    assert purchase['kind']=='command',purchase
    planning=await send('规划现有资金')
    assert planning['kind']=='clarification' and '投入' in planning['message'],planning
    ratio=await send('投入5成')
    assert ratio['requirement_card']['fields']['investment_budget']['resolved_fen']==961450
    assert '随时' in ratio['message'],ratio
    plan=await send('半年')
    assert plan['kind']=='plan',plan
    assert plan['plan']['request']['reserve_fen'] is None
    assert plan['plan']['request']['need'] is None
    assert plan['task']['task_id']!=purchase['task']['task_id']
    stated=await send('我有2万')
    assert stated['requirement_card']['fields']['investment_budget']['resolved_fen']==961450,stated
    queried=await send('先查持仓')
    assert queried['kind']=='assets'
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        old=await db.get(WealthTaskORM,purchase['task']['task_id'])
        assert old.status=='WAITING' and old.data['goal']=='purchase'
        conversation=await db.get(WealthConversationORM,session['session_id'])
        assert conversation.data['active_task']['task_id']==stated['task']['task_id']
