from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import AsyncMock
from datetime import datetime,timedelta,timezone

import pytest
from sqlalchemy import select,func

from tests.integration.test_wealth_bank import store
from tests.integration.test_nova_wealth import conversation,context
from backend.app.services.ai import wealth_agent as agent
from backend.app.services.bank import nova_conversations as chats
from backend.app.models.wealth import WealthConversationORM,WealthOrderORM,WealthCashHoldORM
from backend.app.models.wealth_research import WealthChatRequestORM
from backend.app.models.nova_conversation import NovaConversationORM


def fake_model(monkeypatch,intent):
    monkeypatch.setattr(agent.llm,'generate_structured',AsyncMock(return_value=SimpleNamespace(data=agent.IntentBatch(intents=[intent]))))
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(assets=AsyncMock(return_value={
        'context_id':'test','cash':[{'available_fen':123456,'account_type':'savings'}],
        'asset_summary':{'positions_market_value_fen':0,'total_assets_fen':123456}})))


@pytest.mark.asyncio
async def test_assets_and_valid_assessment_are_text_without_card(store,monkeypatch):
    fake_model(monkeypatch,agent.WealthIntent(kind='assets'))
    async with store() as db:
        await conversation(db)
        result=await agent.manage(agent.EmptyArguments(),context(db))
        assert '1,234.56' in result['reply'] and not result['wealth_cards']
        from backend.app.integrations import wealth_authorization as policy
        monkeypatch.setattr(policy,'latest_assessment',AsyncMock(return_value=SimpleNamespace(
            confirmed_at=datetime.now(timezone.utc),expires_at=datetime.now(timezone.utc)+timedelta(days=1),result={'capacity_level':3})))
        fake_model(monkeypatch,agent.WealthIntent(kind='assessment'))
        result=await agent.manage(agent.EmptyArguments(),context(db,'request-assessment'))
        assert 'R3' in result['reply'] and not result['wealth_cards']


@pytest.mark.asyncio
@pytest.mark.parametrize('model_action',['confirm','continue','prepare'])
async def test_natural_next_keeps_draft_but_cannot_confirm(store,monkeypatch,model_action):
    fake_model(monkeypatch,agent.WealthIntent(kind='purchase',advance='prepare'))
    async with store() as db:
        await conversation(db)
        db.add(WealthConversationORM(session_id='NW-focused',user_id='U1',version=1,data={
            'nova_session_id':'NOVA-test-session','context_id':'test','command':{'kind':'purchase'},'closed':False}))
        await db.commit()
        ctx=replace(context(db),wealth_task_id='NW-focused',wealth_stage='purchase_review',original_message='下一步')
        result=await agent.manage(agent.EmptyArguments(),ctx)
        assert result['wealth_cards'][0]['instruction']['action']=='prepare'
        assert result['wealth_cards'][0]['command']['kind']=='purchase'
        fake_model(monkeypatch,agent.WealthIntent(kind='purchase',advance=model_action))
        result=await agent.manage(agent.EmptyArguments(),replace(ctx,request_id='request-no-auto-consent',original_message='确认',wealth_stage='trade'))
        assert '手动' in result['reply']
        assert result['wealth_cards'][0]['instruction']['id']!= 'request-no-auto-consent'
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM))==0


@pytest.mark.asyncio
async def test_focused_single_amount_keeps_selection_and_updates_saved_value(store,monkeypatch):
    fake_model(monkeypatch,agent.WealthIntent(kind='recommend',task_id='NW-amount',amounts_yuan=['100']))
    async with store() as db:
        await conversation(db)
        product={'product_id':'B017','product_version':1,'name':'测试活钱'}
        db.add(WealthConversationORM(session_id='NW-amount',user_id='U1',version=1,data={
            'nova_session_id':'NOVA-test-session','context_id':'test','closed':False,
            'command':{'kind':'recommend','category':'quick_redeem','products':[product]},
            'view_context':{'selected_products':[product],'amounts_yuan':{'B017':''}}}))
        await db.commit()
        result=await agent.manage(agent.EmptyArguments(),replace(context(db),
            wealth_task_id='NW-amount',wealth_stage='amounts',original_message='这只产品申购金额填100元，先不提交。'))
        card=result['wealth_cards'][0]
        assert card['command']['products']==[product]
        assert card['command']['amounts_yuan']==['100']
        assert card['view_context']['amounts_yuan']=={'B017':'100'}
        assert not card.get('execution_plan_id')
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0


@pytest.mark.asyncio
async def test_delete_is_owner_scoped_purges_linked_cache_and_preserves_bank_facts(store):
    async with store() as db:
        await conversation(db)
        db.add_all([
            WealthConversationORM(session_id='NW-delete',user_id='U1',version=1,data={'nova_session_id':'NOVA-test-session','action_session_ids':['WC-delete']}),
            WealthConversationORM(session_id='WC-delete',user_id='U1',version=1,data={'structured_actions':True}),
            WealthConversationORM(session_id='WC-keep',user_id='U1',version=1,data={'structured_actions':True}),
            WealthChatRequestORM(request_key='NWREQ-delete',user_id='U1',digest='x',status='COMPLETED',response={'session_id':'NOVA-test-session'},started_at=datetime.now(timezone.utc)),
        ])
        await db.commit()
        with pytest.raises(chats.ConversationError):await chats.remove(db,'U2','NOVA-test-session')
        await db.rollback()
        await chats.remove(db,'U1','NOVA-test-session')
        assert await db.get(NovaConversationORM,'NOVA-test-session') is None
        assert await db.get(WealthConversationORM,'NW-delete') is None
        assert await db.get(WealthConversationORM,'WC-delete') is None
        assert await db.get(WealthChatRequestORM,'NWREQ-delete') is None
        assert await db.get(WealthConversationORM,'WC-keep') is not None
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        with pytest.raises(Exception):await agent.manage(agent.EmptyArguments(),context(db))
