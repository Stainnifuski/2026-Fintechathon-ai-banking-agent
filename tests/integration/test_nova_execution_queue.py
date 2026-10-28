"""Serial queue: real persisted states, isolated SQLite/PostgreSQL; no live bank."""
from dataclasses import replace
from unittest.mock import AsyncMock
import asyncio
import pytest
from sqlalchemy import select,func
from backend.app.models.wealth import WealthConversationORM,WealthPlanORM,WealthOrderORM,WealthCashHoldORM
from backend.app.models.nova_execution import NovaExecutionORM
from backend.app.services.ai import execution_queue as queue,nova
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.schemas.nova import PageContext
from backend.app.services.bank import nova_conversations as chats
from tests.integration.test_wealth_bank import store,http_store


def plan(first='申购B017，100元',second='查看我的持仓',completion='settled'):
    return {'execution_mode':'sequential','goals':[
        {'goal_id':'buy','objective':'模型说明','source_text':first,'depends_on':[],
         'completion_condition':'成功','completion':completion},
        {'goal_id':'read','objective':'持仓','source_text':second,'depends_on':['buy'],
         'completion_condition':'查询完成','completion':'result'}]}


async def setup(db,message):
    chat=await chats.create(db,'U1')
    return ToolContext('U1',db,PageContext(page='wealth'),original_message=message,
                       session_id=chat['session_id'],request_id='queue-start-request')


async def fake_buy(context,kind='purchase',status='AWAITING_CONFIRMATION'):
    db=context.db
    db.add(WealthPlanORM(plan_id='plan-one',user_id='U1',status=status,data={'stage':'BUY'}))
    data={'nova_session_id':context.session_id,'request_id':context.request_id,'command':{'kind':kind},
          'closed':False,'execution_plan_id':'plan-one'}
    db.add(WealthConversationORM(session_id='NW-first',user_id='U1',version=1,data=data))
    await db.commit()
    return {'reply':'请在卡片确认与MFA','mode':'model','tool_calls':[{'name':'manage_wealth_request','result':{'reply':'请核对'}}],
            'wealth_cards':[{'task_id':'NW-first',**data}]}


def read_reply(name='get_financial_overview'):
    return {'reply':'查询完成','mode':'model','tool_calls':[{'name':name,'result':{'data':{'ok':True}}}]}


@pytest.mark.asyncio
async def test_wealth_serial_only_advances_after_bank_completion_and_is_replay_safe(store,monkeypatch):
    async with store() as db:
        ctx=await setup(db,'申购B017，100元，然后查看我的持仓')
        async def run(message,history,**kwargs):
            if '申购' in message:return await fake_buy(kwargs['context'])
            return read_reply('manage_wealth_request')
        execute=AsyncMock(side_effect=run);monkeypatch.setattr(nova,'chat',execute)
        result=await queue.start(ctx,plan())
        assert result['execution_queue']['status']=='waiting' and execute.await_count==1
        assert [s['status'] for s in result['execution_queue']['steps']]==['waiting','pending']
        row=await queue.load(ctx);version=row.version
        await queue.advance(ctx);assert execute.await_count==1
        assert (await queue.load(ctx)).version==version # idle polling never writes
        await queue.start(ctx,plan());assert execute.await_count==1
        active=await queue.focus(ctx);assert active.wealth_task_id=='NW-first' and active.serial_focus_only
        bank_plan=await db.get(WealthPlanORM,'plan-one');bank_plan.status='COMPLETED';await db.commit()
        result=await queue.advance(ctx)
        assert result['execution_queue']['status']=='completed' and execute.await_count==2
        assert execute.await_args_list[1].kwargs['context'].original_message=='查看我的持仓'
        await queue.advance(ctx);assert execute.await_count==2
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM))==0


@pytest.mark.asyncio
async def test_cancelled_unsubmitted_review_waits_for_replacement_instead_of_failing_queue(store,monkeypatch):
    async with store() as db:
        ctx=await setup(db,'申购B017，100元，然后查看我的持仓')
        async def run(message,history,**kwargs):
            return await fake_buy(kwargs['context']) if '申购' in message else read_reply()
        execute=AsyncMock(side_effect=run);monkeypatch.setattr(nova,'chat',execute)
        await queue.start(ctx,plan())
        old=await db.get(WealthPlanORM,'plan-one');old.status='CANCELLED';old.data={'stage':'BUY','items':[{'status':'AWAITING_CONFIRMATION'}]};await db.commit()
        result=await queue.advance(ctx);assert result['execution_queue']['status']=='waiting' and execute.await_count==1
        db.add(WealthPlanORM(plan_id='replacement',user_id='U1',status='AWAITING_CONFIRMATION',data={'stage':'BUY'}))
        card=await db.get(WealthConversationORM,'NW-first');card.data={**card.data,'execution_plan_id':'replacement'};await db.commit()
        await queue.advance(ctx);assert execute.await_count==1
        new=await db.get(WealthPlanORM,'replacement');new.status='COMPLETED';await db.commit()
        result=await queue.advance(ctx);assert result['execution_queue']['status']=='completed' and execute.await_count==2


@pytest.mark.asyncio
async def test_sell_planning_waits_for_independent_buy_and_failed_buy_stops_queue(store,monkeypatch):
    async with store() as db:
        ctx=await setup(db,'申购B017，100元，然后查看我的持仓')
        async def run(*args,**kwargs):return await fake_buy(kwargs['context'],'planning')
        execute=AsyncMock(side_effect=run);monkeypatch.setattr(nova,'chat',execute)
        # A completed SELL cannot unlock a separate goal before its continuation.
        await queue.start(ctx,plan())
        p=await db.get(WealthPlanORM,'plan-one');p.status='COMPLETED';p.data={'stage':'SELL','portfolio_plan':{'planning':True}};await db.commit()
        row=await queue.load(ctx);data=queue.unpack(row);data['status']='waiting';data['steps'][0]['status']='waiting';data['steps'][1]['status']='pending'
        await queue.store(ctx,row,data)
        result=await queue.advance(ctx);assert result['execution_queue']['status']=='waiting'
        db.add(WealthPlanORM(plan_id='buy-child',user_id='U1',status='AWAITING_CONFIRMATION',data={'stage':'BUY'}))
        p.data={**p.data,'incremental_continuation':{'prepared':{'execution_plan_id':'buy-child'}}};await db.commit()
        await queue.advance(ctx);assert execute.await_count==1
        child=await db.get(WealthPlanORM,'buy-child');child.status='FAILED';await db.commit()
        result=await queue.advance(ctx);assert result['execution_queue']['status']=='blocked' and execute.await_count==1


@pytest.mark.asyncio
async def test_cross_scenario_order_original_evidence_and_stop_delete_owner(store,monkeypatch):
    async with store() as db:
        ctx=await setup(db,'查看可用现金，再看现在北京时间')
        p=plan('查看可用现金','现在北京时间','result')
        # Input plan listing order may be inverted, dependencies still determine execution.
        p['goals'].reverse()
        execute=AsyncMock(side_effect=[read_reply(),read_reply('get_current_time')]);monkeypatch.setattr(nova,'chat',execute)
        result=await queue.start(ctx,p)
        assert result['execution_queue']['status']=='completed'
        assert [c.args[0] for c in execute.await_args_list]==['查看可用现金','现在北京时间']
        row=await queue.load(ctx)
        assert '查看可用现金' not in row.payload_ciphertext
        with pytest.raises(chats.ConversationError):await queue.load(replace(ctx,user_id='U2'))
        p['goals'][0]['source_text']='不存在的100000元转账'
        invalid=await queue.start(replace(ctx,request_id='request-other'),p)
        assert invalid['execution_blocked'] and execute.await_count==2
        await chats.remove(db,'U1',ctx.session_id)
        assert await db.get(NovaExecutionORM,ctx.session_id) is None


@pytest.mark.asyncio
async def test_live_lease_expiry_failure_and_mutation_without_receipt_never_replay(store,monkeypatch):
    async with store() as db:
        ctx=await setup(db,'申购B017，100元，然后查看我的持仓')
        execute=AsyncMock(return_value=read_reply());monkeypatch.setattr(nova,'chat',execute)
        result=await queue.start(ctx,plan())
        assert result['execution_queue']['status']=='blocked' # no actual bank reference
        await queue.advance(ctx);assert execute.await_count==1
        row=await queue.load(ctx);data=queue.unpack(row);data.update(status='running',lease_until=1)
        row=await queue.store(ctx,row,data)
        result=await queue.advance(ctx);assert result['execution_queue']['status']=='blocked'
        assert execute.await_count==1
        stopped=await queue.stop(ctx,result['execution_queue']['version']);assert stopped['status']=='stopped'


@pytest.mark.asyncio
async def test_queue_rejects_parallel_new_goal_and_terminal_does_not_need_consent(store,monkeypatch):
    async with store() as db:
        ctx=await setup(db,'申购B017，100元，然后查看我的持仓')
        async def run(*a,**k):return await fake_buy(k['context'])
        execute=AsyncMock(side_effect=run);monkeypatch.setattr(nova,'chat',execute)
        await queue.start(ctx,plan())
        ctx=await queue.focus(ctx)
        result=await queue.start(replace(ctx,request_id='request-another'),plan())
        assert '已有未结束' in result['reply'] and execute.await_count==1


@pytest.mark.asyncio
async def test_real_purchase_confirmation_mfa_settlement_then_cross_scenario_read(store,http_store,monkeypatch):
    from tests.integration.test_wealth_target_purchase import selected,confirm_verify
    payload=await selected(store,monkeypatch)
    client,_,app=http_store
    from backend.app.routers import nova as router
    app.include_router(router.router)
    async with store() as db:
        ctx=await setup(db,'申购B017，100元，然后查看我的持仓')
        async def run(message,history,**kwargs):
            if '申购' not in message:return read_reply('list_subscriptions')
            result=await client.post('/api/v1/wealth/target-purchases/prepare',json=payload)
            assert result.status_code==200,result.text
            prepared=result.json()
            data={'nova_session_id':ctx.session_id,'request_id':kwargs['context'].request_id,
                'context_id':payload['context_id'],'command':{'kind':'purchase'},'closed':False,'execution_plan_id':prepared['plan']['plan_id']}
            db.add(WealthConversationORM(session_id='NW-real',user_id='U1',version=1,data=data));await db.commit()
            run.prepared=prepared
            return {'reply':'需手动确认与MFA','tool_calls':[{'name':'manage_wealth_request','result':{'reply':'需核对'}}],
                    'wealth_cards':[{'task_id':'NW-real',**data}]}
        execute=AsyncMock(side_effect=run);monkeypatch.setattr(nova,'chat',execute)
        result=await queue.start(ctx,plan())
        assert result['execution_queue']['status']=='waiting' and execute.await_count==1
        base=f"/api/v1/wealth/plans/{run.prepared['plan']['plan_id']}"
        assert (await client.post(base+'/execute',json={'version':1})).status_code==409
        assert (await client.post('/api/v1/nova/execution-queues/'+ctx.session_id+'/advance',json={
            'version':result['execution_queue']['version'],'confirmed':True})).status_code==422
        await queue.advance(ctx);assert execute.await_count==1
        # App bootstrap on reload resets only the manual workbench, not this
        # owned NOVA plan or its live preview/authorization.
        reset=await client.post('/api/v1/wealth/workspace/reset',json={})
        assert reset.status_code==200,reset.text
        assert (await client.get(base+'/progress')).json()['status']=='AWAITING_CONFIRMATION'
        await queue.advance(ctx);assert execute.await_count==1
        base=await confirm_verify(client,run.prepared)
        await queue.advance(ctx);assert execute.await_count==1 # MFA alone still isn't settlement
        executed=await client.post(base+'/execute',json={'version':1})
        assert executed.status_code==200 and executed.json()['status']=='COMPLETED'
        result=await queue.advance(ctx)
        assert result['execution_queue']['status']=='completed' and execute.await_count==2
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==2
        assert await db.scalar(select(func.count()).select_from(WealthCashHoldORM))==0
        status=await client.get('/api/v1/nova/execution-queues/'+ctx.session_id)
        assert status.status_code==200 and status.json()['status']=='completed'
        assert (await client.post('/api/v1/nova/execution-queues/'+ctx.session_id+'/stop',json={'version':1})).status_code==409


@pytest.mark.asyncio
async def test_parallel_advance_claim_runs_one_step_once(store,monkeypatch):
    async with store() as db:
        ctx=await setup(db,'申购B017，100元，然后查看我的持仓')
        began=asyncio.Event();release=asyncio.Event()
        async def run(*args,**kwargs):
            began.set();await release.wait();return await fake_buy(kwargs['context'])
        execute=AsyncMock(side_effect=run);monkeypatch.setattr(nova,'chat',execute)
        worker=asyncio.create_task(queue.start(ctx,plan()))
        await asyncio.wait_for(began.wait(),5)
        async with store() as other:
            second=await queue.advance(replace(ctx,db=other))
            assert second['execution_queue']['status']=='running' and execute.await_count==1
        release.set();result=await worker
        assert result['execution_queue']['status']=='waiting' and execute.await_count==1


def test_duplicate_or_overlapping_original_spans_cannot_schedule_two_payments():
    p=plan();p['goals'][1]['source_text']=p['goals'][0]['source_text']
    with pytest.raises(ValueError):queue.ordered(p,'申购B017，100元，然后查看我的持仓')


def test_dependency_order_can_precede_original_clause_order_without_reorder_input():
    p=plan('转账100元','赎回100元','settled')
    p['goals'][0]['depends_on']=['read'];p['goals'][1]['depends_on']=[]
    assert [g['source_text'] for g in queue.ordered(p,'转账100元，需要先赎回100元')]==['赎回100元','转账100元']


@pytest.mark.asyncio
async def test_read_services_rollback_does_not_expire_queue_cas_between_goals(store,monkeypatch):
    async with store() as db:
        ctx=await setup(db,'查看可用现金，再看现在北京时间')
        async def run(*args,**kwargs):
            await kwargs['context'].db.rollback()
            # Bank reads/wealth services both rollback and commit themselves.
            await kwargs['context'].db.commit()
            return read_reply()
        execute=AsyncMock(side_effect=run);monkeypatch.setattr(nova,'chat',execute)
        result=await queue.start(ctx,plan('查看可用现金','现在北京时间','result'))
        assert result['execution_queue']['status']=='completed' and execute.await_count==2


@pytest.mark.asyncio
async def test_followup_router_cannot_recreate_original_queue_from_history(store,monkeypatch):
    from tests.unit.test_nova_runtime import FakeModel
    from backend.app.services.ai import nova_runtime
    async with store() as db:
        ctx=await setup(db,'申购B017，100元，然后查看我的持仓')
        original_chat=nova.chat
        async def create(*args,**kwargs):return await fake_buy(kwargs['context'])
        monkeypatch.setattr(nova,'chat',create)
        await queue.start(ctx,plan())
        monkeypatch.setattr(nova,'chat',original_chat)
        monkeypatch.setattr(nova,'get_mode',lambda:'model')
        model=FakeModel(True,['manage_wealth_request'],[
            nova_runtime.PlannedCall(name='manage_wealth_request',arguments={})])
        handler=AsyncMock(return_value={'reply':'已补充当前卡片','wealth_cards':[]})
        monkeypatch.setitem(nova.TOOL_REGISTRY,'manage_wealth_request',{
            **nova.TOOL_REGISTRY['manage_wealth_request'],'execute':handler})
        result=await nova.chat('金额改成200元',[],model=model,
                               context=replace(ctx,original_message='金额改成200元',request_id='followup-one'))
        assert handler.await_count==1
        assert any(item['name']=='manage_wealth_request' and item['result']['reply']=='已补充当前卡片'
                   for item in result['tool_calls'])
        assert handler.await_args.args[1].wealth_task_id=='NW-first'
        data=queue.unpack(await queue.load(ctx));assert len(data['steps'])==2


@pytest.mark.asyncio
async def test_serial_name_normalization_and_clarification_can_be_filled_without_new_task(store,monkeypatch):
    from types import SimpleNamespace
    from backend.app.services.ai import wealth_agent as agent
    async with store() as db:
        ctx=await setup(db,'申购模拟远山·现金快取01，100元')
        model=AsyncMock(return_value=SimpleNamespace(data=agent.IntentBatch(intents=[agent.WealthIntent(
            kind='purchase',product_queries=['模拟远山现金快取01'],amounts_yuan=['100'],amount_quotes=['100元'])])))
        monkeypatch.setattr(agent.llm,'generate_structured',model)
        monkeypatch.setattr(agent,'HttpWealthBankApi',lambda:SimpleNamespace(
            assets=AsyncMock(return_value={'context_id':'test'}),call=AsyncMock(return_value=[
                {'product_id':'B017','version':1,'name':'模拟远山·现金快取01'}])))
        result=await agent.manage(agent.EmptyArguments(),replace(ctx,execution_step=True))
        assert not result.get('error'),result
        card=result['wealth_cards'][0]
        assert card['command']['kind']=='purchase' and card['command']['products'][0]['product_id']=='B017'
        row=await db.get(WealthConversationORM,card['task_id'])
        row.data={**row.data,'command':{**row.data['command'],'kind':'clarify','products':[]},
                  'view_context':{'visible_products':[],'selected_products':[],'completed_stages':[]}};await db.commit()
        model.return_value=SimpleNamespace(data=agent.IntentBatch(intents=[agent.WealthIntent(kind='purchase',
            product_queries=['B017'],amounts_yuan=['100'],amount_quotes=['100元'])]))
        result=await agent.manage(agent.EmptyArguments(),replace(ctx,original_message='产品是B017，100元',
            request_id='clarification-request',serial_focus_only=True,wealth_task_id=card['task_id']))
        assert len(result['wealth_cards'])==1 and result['wealth_cards'][0]['task_id']==card['task_id']
        assert result['wealth_cards'][0]['command']['kind']=='purchase'
