from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select,func
from tests.integration.test_wealth_bank import store
from tests.integration.test_nova_holdings_continuation import seed
from tests.integration.test_nova_wealth import context
from backend.app.services.ai import task_flow as flow,nova
from backend.app.models.wealth import WealthConversationORM,WealthPlanORM,WealthOrderORM,WealthCashHoldORM
from backend.app.models.nova_task import NovaSessionORM
from backend.app.services.bank import nova_conversations as chats
from datetime import datetime,timezone,timedelta
from backend.app.domain.time import utc_now


@pytest.mark.asyncio
async def test_auto_focus_without_supplement_button_and_child_step(store):
    async with store() as db:
        await seed(db)
        ctx=await flow.focus(context(db))
        assert ctx.wealth_task_id=='NW-holdings' and ctx.wealth_stage=='redemption_input'
        assert not await flow.public(ctx)


@pytest.mark.asyncio
async def test_proposal_cancel_and_replacement_preserve_original_until_confirmation(store):
    async with store() as db:
        await seed(db)
        ctx=replace(context(db),original_message='改为做理财规划，总投资金额20000')
        proposal=await flow.propose(ctx,'理财规划')
        token=proposal['task_switch']['token']
        assert not (await db.get(WealthConversationORM,'NW-holdings')).data.get('closed')
        assert (await flow.public(ctx))['token']==token
        _,cancelled=await flow.decide(replace(ctx,task_switch_token=token,accept_task_switch=False))
        assert '保留' in cancelled['reply'] and not await flow.public(ctx)
        p=await flow.propose(ctx,'理财规划')
        next_ctx,result=await flow.decide(replace(ctx,task_switch_token=p['task_switch']['token'],accept_task_switch=True))
        assert result is None and next_ctx.task_switch_approved
        assert next_ctx.original_message==ctx.original_message and not next_ctx.wealth_task_id
        assert (await db.get(WealthConversationORM,'NW-holdings')).data['closed']
        # Replay cannot restart the target or undo the old task.
        _,replay=await flow.decide(replace(ctx,task_switch_token=p['task_switch']['token'],accept_task_switch=True))
        assert replay and not replay.get('task_switch')
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM))==0


@pytest.mark.asyncio
async def test_stale_inputs_require_new_confirmation_even_without_version_change(store):
    async with store() as db:
        await seed(db)
        ctx=context(db);p=await flow.propose(ctx,'转账')
        card=await db.get(WealthConversationORM,'NW-holdings')
        card.data={**card.data,'view_context':{**card.data['view_context'],'selected_products':[]}}
        await db.commit()
        _,result=await flow.decide(replace(ctx,task_switch_token=p['task_switch']['token'],accept_task_switch=True))
        assert '已变化' in result['reply']
        assert not (await db.get(WealthConversationORM,'NW-holdings')).data.get('closed')


@pytest.mark.asyncio
async def test_settled_or_unknown_boundary_does_not_cancel_or_start_other_operation(store):
    async with store() as db:
        await seed(db)
        card=await db.get(WealthConversationORM,'NW-holdings')
        card.data={**card.data,'committed_stage_ids':['already-sold']}
        db.add(WealthPlanORM(plan_id='already-sold',user_id='U1',version=1,status='COMPLETED',data={'items':[{'status':'SETTLED'}]}))
        await db.commit()
        ctx=context(db);p=await flow.propose(ctx,'转账')
        _,result=await flow.decide(replace(ctx,task_switch_token=p['task_switch']['token'],accept_task_switch=True))
        assert '无法安全结束' in result['reply']
        assert not (await db.get(WealthConversationORM,'NW-holdings')).data.get('closed')
        assert (await db.get(WealthPlanORM,'already-sold')).status=='COMPLETED'


@pytest.mark.asyncio
async def test_foreign_session_cannot_read_or_confirm_switch(store):
    async with store() as db:
        await seed(db);ctx=context(db)
        await flow.propose(ctx,'转账')
        with pytest.raises(chats.ConversationError):await flow.public(replace(ctx,user_id='U2'))
        with pytest.raises(chats.ConversationError):await flow.decide(replace(ctx,user_id='U2'))


@pytest.mark.asyncio
async def test_switch_draft_transfer_refreshes_version_before_target(store):
    async with store() as db:
        from tests.integration.test_nova_wealth import conversation
        await conversation(db)
        db.add(NovaSessionORM(session_id='NOVA-test-session',user_id='U1',version=1,state={
            'focus_task_id':'draft-one','tasks':[{'chat_task_id':'draft-one','status':'COLLECTING',
                'slots':{'recipient':'李四'},'missing_fields':['amount'],'candidates':[]}]}))
        await db.commit()
        ctx=replace(context(db),original_message='改成理财规划')
        p=await flow.propose(ctx,'理财规划')
        assert '李四' in p['task_switch']['current_label']
        next_ctx,result=await flow.decide(replace(ctx,task_switch_token=p['task_switch']['token'],accept_task_switch=True))
        assert result is None and next_ctx.session_version==2
        row=await db.get(NovaSessionORM,ctx.session_id)
        assert row.state['tasks'][0]['status']=='CANCELLED'


@pytest.mark.asyncio
@pytest.mark.parametrize('action',['new','read','continue'])
async def test_router_distinguishes_new_read_and_continuation(store,monkeypatch,action):
    async with store() as db:
        await seed(db)
        ctx=await flow.focus(context(db))
        monkeypatch.setattr(nova,'get_mode',lambda:'model')
        monkeypatch.setattr(nova,'route_request',AsyncMock(return_value=nova.RequestRoute(
            mode='direct_react',domains=['wealth' if action!='read' else 'time'],has_dependencies=False,
            task_action=action,target_label='理财规划' if action=='new' else None)))
        tool=AsyncMock(return_value={'reply':'原任务补充','wealth_cards':[]})
        monkeypatch.setattr(nova,'execute_tool',tool)
        monkeypatch.setattr(nova.llm,'generate_with_tools',AsyncMock(return_value=SimpleNamespace(data={
            'role':'assistant','content':None,'tool_calls':[{'id':'call-one','type':'function','function':{
                'name':'manage_wealth_request','arguments':'{}'}}]})))
        # For read, return final structured response so no mutation can run.
        if action=='read':
            nova.llm.generate_with_tools.return_value=SimpleNamespace(data={'role':'assistant','content':'{}','tool_calls':[]})
        result=await nova._chat('另做规划' if action=='new' else '补充',[],context=ctx)
        if action=='new':
            assert tool.await_count==1 and tool.await_args.args[1].wealth_task_id is None
        elif action=='continue':assert tool.await_count==1
        else:assert tool.await_count==0 and not await flow.public(ctx)
        assert not (await db.get(WealthConversationORM,'NW-holdings')).data.get('closed')


@pytest.mark.asyncio
async def test_pending_switch_pauses_serial_advance_without_starting_next_goal(store,monkeypatch):
    from tests.integration.test_nova_execution_queue import setup,plan,fake_buy
    from backend.app.services.ai import execution_queue as queue
    async with store() as db:
        ctx=await setup(db,'申购B017，100元，然后查看我的持仓')
        run=AsyncMock(side_effect=lambda *a,**kw:None)
        async def first(*args,**kwargs):return await fake_buy(kwargs['context'])
        run.side_effect=first;monkeypatch.setattr(nova,'chat',run)
        await queue.start(ctx,plan())
        await flow.propose(ctx,'转账')
        q=await queue.load(ctx);version=q.version
        result=await queue.advance(ctx)
        assert run.await_count==1 and (await queue.load(ctx)).version==version
        assert result['execution_queue']['steps'][1]['status']=='pending'


@pytest.mark.asyncio
async def test_card_preview_can_be_continued_and_unconfirmed_only_can_switch(store):
    from backend.app.models.card import CardOperationORM
    from tests.integration.test_nova_wealth import conversation
    async with store() as db:
        await conversation(db)
        op=CardOperationORM(operation_id='card-preview',user_id='U1',action='card_set_transaction_limits',
            card_id='card-one',params={},preview={},param_digest='original',permission_level='yellow',
            status='AWAITING_CONFIRMATION',expires_at=datetime.now(timezone.utc)+timedelta(minutes=5))
        db.add(op);await db.commit()
        ctx=context(db)
        await flow.observe(ctx,{'tool_calls':[{'name':'preview_card_operation','result':{'data':{'operation_id':op.operation_id}}}]})
        focused=await flow.focus(ctx)
        assert focused.page.cards.selected_card_id=='card-one'
        p=await flow.propose(focused,'理财规划')
        next_ctx,response=await flow.decide(replace(focused,task_switch_token=p['task_switch']['token'],accept_task_switch=True))
        assert response is None and next_ctx.task_switch_approved
        assert (await db.get(CardOperationORM,'card-preview')).status=='CANCELLED'


@pytest.mark.asyncio
async def test_expired_switch_does_not_trap_conversation(store,monkeypatch):
    async with store() as db:
        await seed(db);ctx=context(db)
        await flow.propose(ctx,'转账')
        monkeypatch.setattr(flow.time,'time',lambda:9999999999)
        assert await flow.public(ctx) is None
        _,result=await flow.decide(ctx)
        assert result is None and not (await flow.load(ctx))[1].get('pending')


@pytest.mark.asyncio
async def test_multiple_legacy_tasks_can_still_select_explicit_focus(store):
    async with store() as db:
        await seed(db)
        db.add(WealthConversationORM(session_id='NW-other',user_id='U1',version=1,data={
            'nova_session_id':'NOVA-test-session','command':{'kind':'planning'},'closed':False}))
        await db.commit();ctx=context(db)
        assert (await flow.active(ctx))['type']=='ambiguous'
        assert (await flow.active(replace(ctx,wealth_task_id='NW-holdings')))['id']=='NW-holdings'


@pytest.mark.asyncio
async def test_transfer_submodel_new_intent_keeps_existing_draft(store):
    from tests.integration.test_nova_wealth import conversation
    from backend.app.services.ai.transfer_manager import process_message
    from backend.app.services.ai.nlu import TransferTurnExtraction,TransferTaskExtraction
    async with store() as db:
        await conversation(db)
        db.add(NovaSessionORM(session_id='NOVA-test-session',user_id='U1',version=1,state={
            'focus_task_id':'one','tasks':[{'chat_task_id':'one','status':'COLLECTING','slots':{'recipient':'李四'},
                'missing_fields':['amount'],'candidates':[]}]}))
        await db.commit()
        ctx=replace(context(db),original_message='另给王五转200元',focus_task_id='one')
        nlu=SimpleNamespace(mode='model',extract_tasks=AsyncMock(return_value=TransferTurnExtraction(tasks=[
            TransferTaskExtraction(intent='transfer',task_action='create',recipient='王五',amount_text='200元')])))
        result=await process_message(db,None,nlu,user_id='U1',message=ctx.original_message,request_id=ctx.request_id,
            session_id=ctx.session_id,session_version=1,focus_task_id='one',context=ctx)
        assert not result.get('task_switch')
        row=await db.get(NovaSessionORM,ctx.session_id)
        assert row.version==2 and len(row.state['tasks'])==2
        assert row.state['tasks'][0]['status']=='COLLECTING'


@pytest.mark.asyncio
async def test_replacing_card_preview_invalidates_previous_and_keeps_manual_confirmation(store):
    from backend.app.models.card import CardOperationORM
    from tests.integration.test_nova_wealth import conversation
    async with store() as db:
        await conversation(db);ctx=context(db)
        for key in ['original','updated']:
            db.add(CardOperationORM(operation_id=key,user_id='U1',action='card_set_transaction_limits',
                params={},preview={},param_digest=key,permission_level='red',status='AWAITING_CONFIRMATION',
                expires_at=utc_now()+timedelta(minutes=5)))
        await db.commit()
        def result(key):return {'tool_calls':[{'name':'preview_card_operation','result':{'data':{'operation_id':key}}}]}
        await flow.observe(ctx,result('original'));await flow.observe(ctx,result('updated'))
        assert (await db.get(CardOperationORM,'original')).status=='CANCELLED'
        updated=await db.get(CardOperationORM,'updated')
        assert updated.status=='AWAITING_CONFIRMATION' and updated.confirmed_at is None


@pytest.mark.asyncio
async def test_deleting_chat_removes_task_flow_cache(store):
    from backend.app.models.nova_task_flow import NovaTaskFlowORM
    async with store() as db:
        await seed(db);ctx=context(db)
        await flow.propose(ctx,'转账')
        await chats.remove(db,ctx.user_id,ctx.session_id)
        assert await db.get(NovaTaskFlowORM,ctx.session_id) is None
