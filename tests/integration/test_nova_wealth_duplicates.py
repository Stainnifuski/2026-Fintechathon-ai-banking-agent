from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select,func
from backend.app.services.ai import wealth_agent as agent,wealth_steps
from backend.app.services.bank import nova_conversations as conversations
from backend.app.models.wealth import WealthConversationORM
from backend.app.models.nova_conversation import NovaConversationORM
from tests.integration.test_wealth_bank import store
from tests.integration.test_nova_wealth import context,add_assessment


@pytest.mark.asyncio
async def test_category_label_and_parallel_wealth_tasks(store,monkeypatch):
    model=AsyncMock(return_value=SimpleNamespace(data=agent.IntentBatch(intents=[
        agent.WealthIntent(kind='recommend',product_queries=['债券基金'])])))
    monkeypatch.setattr(agent.llm,'generate_structured',model)
    catalogue=AsyncMock()
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(
        assets=AsyncMock(return_value={'context_id':'test'}),call=catalogue))
    async with store() as db:
        first_session=await conversations.create(db,'U1')
        add_assessment(db)
        row=await db.get(NovaConversationORM,first_session['session_id'])
        row.title_ciphertext=conversations._encrypt('债券基金选择');await db.commit()
        first=await agent.manage(agent.EmptyArguments(),replace(context(db),session_id=first_session['session_id']))
        assert first['wealth_cards'][0]['command']['kind']=='recommend'
        assert first['wealth_cards'][0]['command']['category']=='bond_fund'
        again=await agent.manage(agent.EmptyArguments(),replace(context(db,'again'),session_id=first_session['session_id']))
        assert len(again['wealth_cards'])==2
        second_session=await conversations.create(db,'U1')
        other=await agent.manage(agent.EmptyArguments(),replace(context(db,'other'),session_id=second_session['session_id']))
        assert len(other['wealth_cards'])==1
        assert await db.scalar(select(func.count()).select_from(WealthConversationORM))==3
        assert catalogue.await_count==0


def test_category_repair_does_not_change_real_names_or_purchase_requests():
    for query,kind in [('某某债券基金A','recommend'),('债券基金','purchase')]:
        intent=agent.WealthIntent(kind=kind,product_queries=[query])
        assert agent.category_intent(intent)==intent
    assert wealth_steps.sequence('positions')==['redemption_input','trade']
