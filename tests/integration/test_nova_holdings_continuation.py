from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select,func
from tests.integration.test_wealth_bank import store
from tests.integration.test_nova_wealth import conversation,context
from backend.app.models.wealth import WealthConversationORM,WealthOrderORM,WealthCashHoldORM
from backend.app.services.ai import wealth_agent as agent,execution_queue as queue,nova
from backend.app.services.bank import nova_conversations as chats


async def seed(db,selected=True):
    await conversation(db)
    products=[{'product_id':'P1','product_version':1,'name':'甲债券A'},{'product_id':'P2','product_version':1,'name':'乙债券A'}]
    db.add(WealthConversationORM(session_id='NW-holdings',user_id='U1',version=1,data={
        'nova_session_id':'NOVA-test-session','request_id':'old-read','context_id':'test','command':{'kind':'positions','products':[]},
        'closed':False,'view_context':{'selection_kind':'positions','visible_products':products,'selected_products':products if selected else []}}))
    await db.commit()
    return products


def fake(monkeypatch,products,held=True):
    monkeypatch.setattr(agent.llm,'generate_structured',AsyncMock(return_value=SimpleNamespace(data=agent.IntentBatch(intents=[agent.WealthIntent(kind='redeem')]))))
    monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(assets=AsyncMock(return_value={
        'context_id':'test','lots':[{'product_id':p['product_id'],'version_id':f"{p['product_id']}:1"} for p in products] if held else []}),
        call=AsyncMock(return_value=[{'product_id':p['product_id'],'version':1,'name':p['name']} for p in products])))


@pytest.mark.asyncio
async def test_redemption_is_subtask_of_focused_holdings_not_a_new_task(store,monkeypatch):
    async with store() as db:
        products=await seed(db);fake(monkeypatch,products)
        other=await chats.create(db,'U1')
        db.add(WealthConversationORM(session_id='NW-old-planning',user_id='U1',version=1,data={
            'nova_session_id':other['session_id'],'context_id':'test','command':{'kind':'planning','products':[]},'closed':False}))
        await db.commit()
        result=await agent.manage(agent.EmptyArguments(),replace(context(db,'redeem-selected-one'),
            original_message='赎回已选持仓',wealth_task_id='NW-holdings',wealth_stage='redemption_input'))
        assert not result.get('error'),result
        assert len(result['wealth_cards'])==1
        card=result['wealth_cards'][0]
        assert card['task_id']=='NW-holdings' and card['origin_kind']=='positions'
        assert card['command']['kind']=='redeem' and card['command']['products']==products
        assert card['command']['redemption_value'] is None and not card['command']['auto_prepare']
        assert '已有相同' not in result['reply']
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM))==0


@pytest.mark.asyncio
async def test_model_cannot_turn_focused_redemption_into_an_unrelated_plan(store,monkeypatch):
    async with store() as db:
        products=await seed(db);fake(monkeypatch,products)
        monkeypatch.setattr(agent.llm,'generate_structured',AsyncMock(return_value=SimpleNamespace(data=agent.IntentBatch(intents=[agent.WealthIntent(kind='planning')]))))
        result=await agent.manage(agent.EmptyArguments(),replace(context(db),original_message='赎回已选持仓',
            wealth_task_id='NW-holdings',wealth_stage='redemption_input'))
        assert result['error']=='HOLDINGS_TASK_CONTEXT'
        assert (await db.get(WealthConversationORM,'NW-holdings')).data['command']['kind']=='positions'
        assert await db.scalar(select(func.count()).select_from(WealthConversationORM))==1


@pytest.mark.asyncio
@pytest.mark.parametrize('selected,held',[(False,True),(True,False)])
async def test_empty_or_stale_holdings_never_switch_to_other_task(store,monkeypatch,selected,held):
    async with store() as db:
        products=await seed(db,selected);fake(monkeypatch,products,held)
        ctx=replace(context(db),original_message='赎回这些',wealth_task_id='NW-holdings',wealth_stage='redemption_input')
        if selected:
            from backend.app.integrations.wealth_authorization import WealthPolicyError
            with pytest.raises(WealthPolicyError):await agent.manage(agent.EmptyArguments(),ctx)
        else:
            result=await agent.manage(agent.EmptyArguments(),ctx)
            assert '当前持仓卡勾选' in result['reply'] and result['wealth_cards'][0]['command']['kind']=='positions'
        assert await db.scalar(select(func.count()).select_from(WealthConversationORM))==1


@pytest.mark.asyncio
async def test_serial_holdings_then_redemption_waits_in_same_task_when_no_selection(store,monkeypatch):
    async with store() as db:
        products=await seed(db,False);fake(monkeypatch,products)
        ctx=replace(context(db,'serial-holdings-one'),original_message='先查看持仓，然后赎回已选持仓')
        async def run(message,history,**kw):
            child=kw['context']
            if '查看' in message:
                row=await db.get(WealthConversationORM,'NW-holdings')
                row.data={**row.data,'request_id':child.request_id};await db.commit()
                return {'reply':'持仓已展示','tool_calls':[{'name':'manage_wealth_positions','result':{'reply':'持仓已展示'}}],
                    'wealth_cards':await agent.cards(db,'U1',ctx.session_id)}
            assert child.wealth_task_id=='NW-holdings'
            result=await agent.manage(agent.EmptyArguments(),child)
            return {**result,'tool_calls':[{'name':'manage_wealth_positions','result':{'reply':result['reply']}}]}
        monkeypatch.setattr(nova,'chat',run)
        plan={'execution_mode':'sequential','goals':[
            {'goal_id':'positions','objective':'持仓','source_text':'先查看持仓','completion_condition':'查询返回','completion':'result'},
            {'goal_id':'redeem','objective':'赎回','source_text':'赎回已选持仓','completion_condition':'实际赎回','completion':'settled','depends_on':['positions']}]}
        result=await queue.start(ctx,plan)
        assert [s['status'] for s in result['execution_queue']['steps']]==['completed','waiting']
        assert len(result['wealth_cards'])==1 and result['wealth_cards'][0]['task_id']=='NW-holdings'
        assert '当前持仓卡勾选' in result['reply']
        assert queue.unpack(await queue.load(ctx))['steps'][1]['refs']==[{'type':'wealth','id':'NW-holdings'}]
