from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select,func
from backend.app.services.ai import wealth_agent as agent,wealth_steps
from backend.app.services.bank import nova_conversations as conversations
from backend.app.models.wealth import WealthConversationORM,WealthPlanORM
from backend.app.models.nova_conversation import NovaConversationORM
from tests.integration.test_wealth_bank import store
from tests.integration.test_nova_wealth import context


@pytest.mark.asyncio
async def test_category_label_and_duplicate_report_original_conversation_without_new_task(store,monkeypatch):
    model=AsyncMock(return_value=SimpleNamespace(data=agent.IntentBatch(intents=[
        agent.WealthIntent(kind='recommend',product_queries=['债券基金'])])))
    monkeypatch.setattr(agent.llm,'generate_structured',model)
    catalogue=AsyncMock()
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(
        assets=AsyncMock(return_value={'context_id':'test'}),call=catalogue))
    async with store() as db:
        first_session=await conversations.create(db,'U1')
        row=await db.get(NovaConversationORM,first_session['session_id'])
        row.title_ciphertext=conversations._encrypt('债券基金选择');await db.commit()
        first=await agent.manage(agent.EmptyArguments(),replace(context(db),session_id=first_session['session_id']))
        assert first['wealth_cards'][0]['command']['kind']=='recommend'
        assert first['wealth_cards'][0]['command']['category']=='bond_fund'
        again=await agent.manage(agent.EmptyArguments(),replace(context(db,'again'),session_id=first_session['session_id']))
        assert len(again['wealth_cards'])==1
        assert '相同理财任务尚未结束' in again['reply']
        second_session=await conversations.create(db,'U1')
        other=await agent.manage(agent.EmptyArguments(),replace(context(db,'other'),session_id=second_session['session_id']))
        assert other['wealth_cards']==[]
        assert '债券基金选择' in other['reply'] and f'#nova-conversation={first_session["session_id"]}' in other['reply']
        assert await db.scalar(select(func.count()).select_from(WealthConversationORM))==1
        assert catalogue.await_count==0


@pytest.mark.asyncio
@pytest.mark.parametrize('state',['open','paused','closed','completed','other_user','other_scope','other_category'])
async def test_duplicate_scope_and_lifecycle(store,state):
    command={'kind':'recommend','category':'bond_fund','products':[]}
    async with store() as db:
        original=await conversations.create(db,'U2' if state=='other_user' else 'U1')
        data={'nova_session_id':original['session_id'],'context_id':'other' if state=='other_scope' else 'test',
              'command':{**command,'category':'mixed_fund' if state=='other_category' else 'bond_fund'},
              'closed':state=='closed','paused':state=='paused'}
        if state=='completed':
            db.add(WealthPlanORM(plan_id='done',user_id='U1',version=1,status='COMPLETED',data={'stage':'BUY'}))
            data['execution_plan_id']='done'
        db.add(WealthConversationORM(session_id='NW-existing',user_id='U2' if state=='other_user' else 'U1',version=1,data=data))
        await db.commit()
        result=await agent.unfinished_match(db,'U1','test',command)
        assert bool(result)==(state in {'open','paused'})


def test_category_repair_does_not_change_real_names_or_purchase_requests():
    for query,kind in [('某某债券基金A','recommend'),('债券基金','purchase')]:
        intent=agent.WealthIntent(kind=kind,product_queries=[query])
        assert agent.category_intent(intent)==intent
    assert wealth_steps.sequence('positions')==['redemption_input','trade']
    command={'kind':'recommend','category':'bond_fund','products':[]}
    assert agent.goal_key(command)==agent.goal_key({**command,'products':[{'product_id':'1','product_version':1}]})
