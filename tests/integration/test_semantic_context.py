from dataclasses import replace
from datetime import datetime,timezone,timedelta
import json
import pytest
from backend.app.models.wealth import WealthConversationORM
from backend.app.models.nova_reference_context import NovaReferenceContextORM
from backend.app.services.ai import wealth_agent as agent
from backend.app.services.ai import nova
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.services.ai.card_subscription_tools import CardArguments
from backend.app.schemas.nova import PageContext
from backend.app.services.bank import nova_reference_context as refs,nova_conversations as chats
from tests.integration.test_wealth_bank import store
from tests.integration.test_nova_wealth import conversation,context
from tests.integration.test_nova_wealth_cards import fake_model
from types import SimpleNamespace
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_owner_scoped_reference_context_retains_no_money_and_expires(store):
    async with store() as db:
        session=await chats.create(db,'U1');sid=session['session_id']
        await refs.save(db,'U1',sid,[{'name':'list_cards','result':{'data':{'items':[
            {'card_id':'C1','card_number':'****0001','balance_fen':999999,'secret':'not-kept'}]}}}]);await db.commit()
        saved=await refs.load(db,'U1',sid)
        assert saved[0]['result']['data']['items']==[{'card_id':'C1','card_number':'****0001'}]
        with pytest.raises(chats.ConversationError):await refs.load(db,'U2',sid)
        row=await db.get(NovaReferenceContextORM,sid)
        assert not row.payload_ciphertext.startswith('[')
        assert json.loads(chats._decrypt(row.payload_ciphertext))==saved
        saved[0]['saved_at']=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        row.payload_ciphertext=chats._encrypt(json.dumps(saved));await db.commit()
        assert await refs.load(db,'U1',sid)==[]
        await chats.remove(db,'U1',sid)
        assert await db.get(NovaReferenceContextORM,sid) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('field',['reserve','investment','total'])
async def test_manual_budget_then_omitted_natural_correction_uses_view_and_updates_it(store,monkeypatch,field):
    fake_model(monkeypatch,agent.WealthIntent(kind='planning',cash_yuan='20000',cash_quote='改成20000'))
    async with store() as db:
        await conversation(db)
        db.add(WealthConversationORM(session_id='NW-budget',user_id='U1',version=1,data={
            'nova_session_id':'NOVA-test-session','context_id':'test','closed':False,
            'command':{'kind':'planning','products':[],'amounts_yuan':[],'cash_field':None,'cash_yuan':None},
            'view_context':{'cash_field':field,'cash_yuan':'30000','selected_products':[],'completed_stages':[]}}));await db.commit()
        result=await agent.manage(agent.EmptyArguments(),replace(context(db),original_message='改成20000',wealth_task_id='NW-budget',wealth_stage='budget'))
        assert not result.get('error'),result
        card=result['wealth_cards'][0]
        assert card['command']['cash_field']==('invest' if field=='investment' else field)
        assert card['command']['cash_yuan']==card['view_context']['cash_yuan']=='20000'
        assert card['view_context']['cash_field']==field


@pytest.mark.asyncio
async def test_two_chinese_ordinal_amounts_bind_to_visible_products(store,monkeypatch):
    intent=agent.WealthIntent(kind='recommend',task_id='NW-amounts',candidate_indices=[1,2],
        amounts_yuan=['100','200'],amount_quotes=['第一只100元','第二支200元'])
    fake_model(monkeypatch,intent)
    products=[{'product_id':'B1','product_version':1,'name':'甲债券A'},
              {'product_id':'B2','product_version':1,'name':'乙债券C'}]
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(
        assets=AsyncMock(return_value={'context_id':'test'}),
        call=AsyncMock(return_value=[{'product_id':p['product_id'],'version':1,'name':p['name']} for p in products])))
    async with store() as db:
        await conversation(db)
        db.add(WealthConversationORM(session_id='NW-amounts',user_id='U1',version=1,data={
            'nova_session_id':'NOVA-test-session','context_id':'test','closed':False,
            'command':{'kind':'recommend','category':'bond_fund','products':[],'amounts_yuan':[]},
            'view_context':{'visible_products':products,'selected_products':[],'completed_stages':[]}}));await db.commit()
        result=await agent.manage(agent.EmptyArguments(),replace(context(db),original_message='第一只100元，第二支200元',wealth_task_id='NW-amounts',wealth_stage='candidates'))
        assert not result.get('error'),result
        command=result['wealth_cards'][0]['command']
        assert command['amounts_yuan']==['100','200']
        assert [p['product_id'] for p in command['products']]==['B1','B2']


@pytest.mark.asyncio
async def test_final_visible_turn_saves_references_and_next_tool_uses_same_order(store,monkeypatch):
    from backend.app.routers.nova import _finish,ChatRequest
    async with store() as db:
        conversation_data=await chats.create(db,'U1');sid=conversation_data['session_id']
        reply={'reply':'两张卡片','tool_calls':[{'name':'list_cards','result':{'data':{'items':[
            {'card_id':'C1','card_number':'****0001'},{'card_id':'C2','card_number':'****0002'}]}}}]}
        await _finish(ChatRequest(message='查看卡片',session_id=sid,request_id='reference-turn-1'),'U1',db,reply)
        handler=AsyncMock(return_value={'data':{'card_id':'C2'}})
        monkeypatch.setitem(nova.TOOL_REGISTRY,'get_card',{'arguments':CardArguments,'requires_identity':True,'execute':handler})
        ctx=ToolContext('U1',db,PageContext(),original_message='看第二张',session_id=sid,
            reference_results=await refs.load(db,'U1',sid))
        result=await nova.execute_tool({'function':{'name':'get_card','arguments':'{"card_id":"第二张"}'}},ctx)
        assert result['data']['card_id']=='C2'
        assert handler.await_args.args[0].card_id=='C2'
        # An internal refresh must not silently change the order the user saw.
        reordered=[{'name':'list_cards','result':{'data':{'items':[{'card_id':'C2'},{'card_id':'C1'}]}}}]
        await nova.execute_tool({'function':{'name':'get_card','arguments':'{"card_id":"第二张"}'}},
            replace(ctx,current_tool_results=reordered))
        assert handler.await_args.args[0].card_id=='C2'
        await refs.save(db,'U1',sid,[{'name':'list_cards','result':{'error':'BANK_TIMEOUT'}}]);await db.commit()
        result=await nova.execute_tool({'function':{'name':'get_card','arguments':'{"card_id":"第二张"}'}},
            replace(ctx,reference_results=await refs.load(db,'U1',sid)))
        assert result['error']=='MISSING_SELECTION' and handler.await_count==2
