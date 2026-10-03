"""持久化依赖、控制与旧银行入口约束的双库验证。"""
import pytest
from sqlalchemy import select
from tests.integration.test_wealth_bank import store, http_store, command, assess, authorize, move
from backend.app.models.wealth import WealthTaskORM, WealthTaskNodeORM, WealthAuthorizationORM
from backend.app.services.orchestration import wealth_task as tasks
from backend.app.services.orchestration.wealth_task_worker import tick
from backend.app.services.bank import wealth_service as bank
from backend.app.integrations.wealth_authorization import WealthPolicyError
from backend.app.integrations.bank_api import BankApiError
from backend.app.domain.wealth_task import validate_graph
from backend.app.services.bank.transfer_service import BankError


@pytest.mark.asyncio
async def test_terminal_handoff_cannot_resurrect_or_block_unrelated_cancellation(store,monkeypatch):
    monkeypatch.setenv('WEALTH_OPERATOR_USER_IDS','OP1')
    async with store() as db:
        await assess(db)
        task=await tasks.create_command(db,'U1','terminal-scope','legacy-v1',command('terminal-command'))
        row=await tasks.owned(db,'U1',task['task_id'])
        row.status='COMPLETED';await db.commit()
        with pytest.raises(WealthPolicyError) as error:
            await tasks.control(db,'U1',task['task_id'],'handoff',{'request_id':'terminal-handoff','expected_version':1})
        assert error.value.code=='TASK_TERMINAL';await db.rollback()
        # A pre-migration terminal HUMAN row is retained, but cannot freeze the account forever.
        row=await tasks.owned(db,'U1',task['task_id']);row.control_mode='HUMAN';row.owner_id='OP1';await db.commit()
        cmd=command('unrelated-m1-cancel',product='MF001')
        await bank.accept_many(db,'U1',[(cmd,await authorize(db,cmd))])
        assert (await bank.cancel(db,'U1',cmd.operation_id))['status']=='CANCELLED'
        released=await tasks.control(db,'U1',task['task_id'],'release',{'request_id':'terminal-release','expected_version':1},actor_id='OP1')
        assert released['status']=='COMPLETED' and released['control_mode']=='AGENT'


def test_graph_rejects_cycles_missing_dependencies_unregistered_tools():
    valid=[{'node_id':'a','kind':'READ_FACTS','depends_on':[],'completion_condition':'fact'}]
    validate_graph(valid)
    for nodes in [[{**valid[0],'depends_on':['missing']}], [{**valid[0],'depends_on':['a']}],
                  [{**valid[0],'kind':'EXEC_SQL'}], [valid[0],valid[0]]]:
        with pytest.raises(ValueError): validate_graph(nodes)


class Bank:
    async def query(self, user, op):
        # 真正HTTP结果失联时不假造订单，后续同号提交测试另行覆盖。
        raise BankApiError('无订单', 'NOT_FOUND', 404)


class DirectBank:
    def __init__(self, db): self.db=db; self.submissions=0
    async def query(self, user, op):
        try: return bank.order_view(await bank.owned_order(self.db,user,op))
        except BankError as error: raise BankApiError(error.message,error.code,error.status_code) from error
    async def submit(self, user, cmd, aid):
        self.submissions+=1
        return (await bank.accept_many(self.db,user,[(cmd,aid)]))[0]

    async def call(self, method, path, payload):
        assert method=='POST' and path=='/assets'
        return await bank.assets(self.db,payload['subject_user_id'],account_id=payload['account_id'])


@pytest.mark.asyncio
async def test_paused_unknown_submission_remains_queryable_after_not_found(store):
    """未知受理的404不能终止原号核对；暂停后绝不补发提交。"""
    async with store() as db:
        task = await tasks.create_command(db, 'U1', 'unknown-paused', 'legacy-v1',
            command('unknown-original-operation'))
        row = await tasks.owned(db, 'U1', task['task_id'])
        row.status = 'PAUSED'
        node = await db.scalar(select(WealthTaskNodeORM).where(
            WealthTaskNodeORM.task_id == row.task_id, WealthTaskNodeORM.kind == 'SUBMIT'))
        node.status = 'UNKNOWN'
        await db.commit()
        class MissingThenKnown:
            def __init__(self): self.queries = 0
            async def query(self, user, operation):
                assert operation == 'unknown-original-operation'
                self.queries += 1
                if self.queries < 2: raise BankApiError('暂未查到', 'NOT_FOUND', 404)
                return {'operation_id': operation, 'status': 'ACCEPTED'}
            async def submit(self, *args):
                pytest.fail('暂停的未知提交禁止补发')
        api = MissingThenKnown()
        await tick(db, row.task_id, api)
        await db.refresh(node)
        assert node.status == 'UNKNOWN' and row.status == 'PAUSED'
        await tick(db, row.task_id, api)
        await db.refresh(node)
        assert node.status == 'COMPLETED' and api.queries == 2


@pytest.mark.asyncio
async def test_cancel_submitted_task_keeps_settlement_observation(store):
    async with store() as db:
        await assess(db)
        cmd=command('dag-cancel-submitted-01')
        task=await tasks.create_command(db,'U1','cancel-observation','legacy-v1',cmd)
        await authorize(db,cmd)
        api=DirectBank(db)
        for _ in range(3):await tick(db,task['task_id'],api)
        assert api.submissions==1
        cancelled=await tasks.control(db,'U1',task['task_id'],'cancel',
            {'request_id':'cancel-observation-01','expected_version':1})
        assert cancelled['status']=='RECONCILING'
        assert next(n for n in cancelled['nodes'] if n['kind']=='SETTLEMENT')['status']!='CANCELLED'
        await tick(db,task['task_id'],api)
        assert api.submissions==1
        await move(db,10)
        assert (await bank.settle(db,cmd.operation_id))['status']=='SETTLED'
    async with store() as restarted:
        api=DirectBank(restarted)
        for _ in range(3):await tick(restarted,task['task_id'],api)
        result=await tasks.view(restarted,await tasks.owned(restarted,'U1',task['task_id']))
        assert result['status']=='PARTIAL_COMPLETED'
        assert next(n for n in result['nodes'] if n['kind']=='SETTLEMENT')['output']['status']=='SETTLED'
        assert api.submissions==0


@pytest.mark.asyncio
async def test_sell_then_buy_requires_actual_cash_reread_and_fresh_authorization(store):
    async with store() as db:
        await assess(db)
        initial=command('dag-holding-seed')
        await bank.accept_many(db,'U1',[(initial,await authorize(db,initial))])
        await move(db,12);assert (await bank.settle(db,initial.operation_id))['status']=='SETTLED'
        sell=command('dag-sell-then-buy',direction='redeem',amount=0,quantity='10.0000')
        task=await tasks.create_command(db,'U1','sell-buy-session','legacy-v1',sell,sell_then_buy=True)
        api=DirectBank(db);await authorize(db,sell)
        for _ in range(4):await tick(db,task['task_id'],api)
        assert api.submissions==1
        buy=command('dag-following-new-buy',amount=10000).model_copy(update={'plan_id':'WPdag-cash','plan_version':1})
        plan={'plan_id':'WPdag-cash','version':1,'stage':'BUY','request':{'kind':'new_cash','account_id':'A1'},'items':[{'command':buy.model_dump()}]}
        with pytest.raises(WealthPolicyError):await tasks.register_plan(db,'U1','sell-buy-session','legacy-v1',plan)
        await db.rollback()
        await move(db,13);assert (await bank.settle(db,sell.operation_id))['status']=='PENDING_CASH'
        await tick(db,task['task_id'],api)
        state=await tasks.view(db,await tasks.owned(db,'U1',task['task_id']))
        assert next(n for n in state['nodes'] if n['kind']=='REREAD_CASH')['status']!='COMPLETED'
        await move(db,14);assert (await bank.settle(db,sell.operation_id))['status']=='SETTLED'
        for _ in range(3):await tick(db,task['task_id'],api)
        next_task=await tasks.register_plan(db,'U1','sell-buy-session','legacy-v1',plan)
        assert next_task['task_id']==task['task_id'] and next_task['version']==2
        assert any(n['kind']=='REREAD_CASH' and n['status']=='COMPLETED' for n in next_task['nodes'])
        with pytest.raises(WealthPolicyError):await bank.accept_many(db,'U1',[(buy,'old-sell-authorization')])
        await db.rollback()
        from backend.app.models.wealth import WealthPlanORM
        db.add(WealthPlanORM(plan_id=plan['plan_id'],user_id='U1',version=1,status='AWAITING_CONFIRMATION',
            data={**plan,'profile_version':1,'goal_versions':{}}))
        await db.commit()
        aid=await authorize(db,buy)
        assert (await bank.accept_many(db,'U1',[(buy,aid)]))[0]['status']=='ACCEPTED'
        for _ in range(4):await tick(db,task['task_id'],api)
        assert api.submissions==1  # 买入由可信批量入口受理，worker只核对原号。


@pytest.mark.asyncio
async def test_cancelled_sell_blocks_buy_dependency_and_human_direct_cancel(store, monkeypatch):
    """已提交赎回撤单后不生成新买入；人工接管期间旧银行撤单入口受限。"""
    monkeypatch.setenv('WEALTH_OPERATOR_USER_IDS', 'OP1')
    async with store() as db:
        await assess(db)
        initial=command('cancel-dependency-opening',product='MF001')
        await bank.accept_many(db,'U1',[(initial,await authorize(db,initial))])
        await move(db,12)
        assert (await bank.settle(db,initial.operation_id))['status']=='SETTLED'
        sell=command('cancel-dependency-sell',product='MF001',direction='redeem',amount=0,quantity='10.0000')
        task=await tasks.create_command(db,'U1','cancel-dependency-session','legacy-v1',sell,sell_then_buy=True)
        api=DirectBank(db);await authorize(db,sell)
        for _ in range(4):await tick(db,task['task_id'],api)
        assert api.submissions==1
        handoff=await tasks.control(db,'U1',task['task_id'],'handoff',
            {'request_id':'cancel-dependency-handoff','expected_version':1})
        claimed=await tasks.control(db,'U1',task['task_id'],'claim',
            {'request_id':'cancel-dependency-claim','expected_version':handoff['version']},actor_id='OP1')
        with pytest.raises(BankError) as error:await bank.cancel(db,'U1',sell.operation_id)
        assert error.value.code=='TASK_WRITE_BLOCKED';await db.rollback()
        released=await tasks.control(db,'U1',task['task_id'],'release',
            {'request_id':'cancel-dependency-release','expected_version':claimed['version']},actor_id='OP1')
        await tasks.control(db,'U1',task['task_id'],'resume',
            {'request_id':'cancel-dependency-resume','expected_version':released['version']})
        assert (await bank.cancel(db,'U1',sell.operation_id))['status']=='CANCELLED'
        for _ in range(6):await tick(db,task['task_id'],api)
        state=await tasks.view(db,await tasks.owned(db,'U1',task['task_id']))
        assert next(node for node in state['nodes'] if node['kind']=='REREAD_CASH')['status']=='CANCELLED'
        assert next(node for node in state['nodes'] if node['kind']=='BUY_PLAN')['status']=='CANCELLED'
        assert state['status']=='CANCELLED' and api.submissions==1
        facts=await api.call('POST','/assets',{'subject_user_id':'U1','account_id':'A1'})
        replacement=command('cancelled-sell-following-buy',product='MF001').model_copy(update={'plan_id':'WPcancelled-dependency','plan_version':1})
        assert replacement.amount_fen<=facts['cash'][0]['available_fen']
        plan={'plan_id':'WPcancelled-dependency','version':1,'stage':'BUY','request':{'kind':'new_cash','account_id':'A1'},
              'items':[{'command':replacement.model_dump()}]}
        fresh=await tasks.register_plan(db,'U1','cancel-dependency-session','legacy-v1',plan)
        assert fresh['task_id']!=task['task_id']
        assert next(node for node in fresh['nodes'] if node['kind']=='CONFIRM')['status']=='READY'
        assert next(node for node in fresh['nodes'] if node['kind']=='VERIFY')['status']=='READY'
        assert not any(node['node_id'] in {old['node_id'] for old in state['nodes']} for node in fresh['nodes'])
        with pytest.raises(WealthPolicyError):await bank.accept_many(db,'U1',[(replacement,'old-sell-authorization')])
        await db.rollback()
        assert api.submissions==1


@pytest.mark.asyncio
async def test_scheduler_actual_dependency_submit_wait_bank_fact_and_restart(store):
    async with store() as db:
        await assess(db)
        cmd=command('dag-settlement-01')
        task=await tasks.create_command(db,'U1','session-dag-test','legacy-v1',cmd)
        api=DirectBank(db)
        await tick(db,task['task_id'],api)
        assert api.submissions==0
        await authorize(db,cmd)
        for _ in range(3): await tick(db,task['task_id'],api)
        assert api.submissions==1
        await tick(db,task['task_id'],api)
        result=await tasks.view(db,await tasks.owned(db,'U1',task['task_id']))
        assert result['status']=='WAITING' and result['waiting_reason']=='BANK_SETTLEMENT'
        assert next(n for n in result['nodes'] if n['kind']=='SETTLEMENT')['status']=='WAITING'
        await move(db,10)
        assert (await bank.settle(db,cmd.operation_id))['status']=='SETTLED'
    async with store() as restarted:
        api=DirectBank(restarted)
        await tick(restarted,task['task_id'],api); await tick(restarted,task['task_id'],api)
        restored=await tasks.view(restarted,await tasks.owned(restarted,'U1',task['task_id']))
        assert restored['status']=='COMPLETED'
        assert all(n['status']=='COMPLETED' for n in restored['nodes'])
        await tick(restarted,task['task_id'],api)
        assert api.submissions==0


@pytest.mark.asyncio
async def test_pause_handoff_replay_owner_and_old_bank_write_guard(store, monkeypatch):
    monkeypatch.setenv('WEALTH_OPERATOR_USER_IDS', 'OP1,OP2')
    async with store() as db:
        await assess(db)
        cmd=command('task-controlled-01')
        task=await tasks.create_command(db, 'U1', 'session-task-test', 'legacy-v1', cmd)
        aid=await authorize(db, cmd)
        # 顺序依赖必须完成；存在有效授权不代表整个任务依赖已满足。
        with pytest.raises(WealthPolicyError) as error: await tasks.require_write_control(db,'U1',cmd)
        assert error.value.code=='TASK_DEPENDENCY_PENDING'
        await db.rollback()
        await tick(db, task['task_id'], Bank()); await tick(db, task['task_id'], Bank())
        nodes=(await tasks.view(db, await tasks.owned(db,'U1',task['task_id'])))['nodes']
        assert all(n['status']=='COMPLETED' for n in nodes if n['kind'] in {'CONFIRM','VERIFY'})
        paused=await tasks.control(db,'U1',task['task_id'],'pause',{'request_id':'task-pause-01','expected_version':1})
        assert paused['status']=='PAUSED'
        assert await tasks.control(db,'U1',task['task_id'],'pause',{'request_id':'task-pause-01','expected_version':1})==paused
        with pytest.raises(WealthPolicyError) as error: await bank.accept_many(db,'U1',[(cmd,aid)])
        assert error.value.code=='TASK_WRITE_BLOCKED'
        await db.rollback()
        handoff=await tasks.control(db,'U1',task['task_id'],'handoff',{'request_id':'task-handoff-01','expected_version':2})
        assert handoff['control_mode']=='HANDOFF_PENDING'
        claimed=await tasks.control(db,'U1',task['task_id'],'claim',{'request_id':'task-claim-01','expected_version':3},actor_id='OP1')
        assert claimed['owner_id']=='OP1' and claimed['control_mode']=='HUMAN'
        with pytest.raises(WealthPolicyError):
            await tasks.control(db,'U1',task['task_id'],'claim',{'request_id':'task-claim-02','expected_version':claimed['version']},actor_id='OP2')
        await db.rollback()
        with pytest.raises(WealthPolicyError) as error: await bank.accept_many(db,'U1',[(cmd,aid)])
        assert error.value.code=='TASK_WRITE_BLOCKED'
        await db.rollback()
        released=await tasks.control(db,'U1',task['task_id'],'release',{'request_id':'task-release-01','expected_version':4},actor_id='OP1')
        assert released['status']=='PAUSED' and released['waiting_reason']=='REVALIDATION_REQUIRED'
        async with store() as restarted:
            restored=await tasks.view(restarted,await tasks.owned(restarted,'U1',task['task_id']))
            assert restored['control_mode']=='AGENT' and restored['version']==5
        with pytest.raises(WealthPolicyError): await tasks.owned(db,'U2',task['task_id'])


@pytest.mark.asyncio
async def test_operator_http_role_owner_scope_and_no_impersonation(store,http_store,monkeypatch):
    client,_,app=http_store
    from backend.app.middleware.auth import get_current_user
    async with store() as db:
        task=await tasks.create_command(db,'U1','operator-session','legacy-v1',command('operator-bank-facts'))
    assert (await client.get('/api/v1/wealth/operator/tasks')).status_code==403
    assert (await client.post(f"/api/v1/wealth/tasks/{task['task_id']}/pause",json={
        'expected_version':1,'request_id':'forged-subject-1','subject_user_id':'U2'})).status_code==422
    handoff=await client.post(f"/api/v1/wealth/tasks/{task['task_id']}/handoff",json={'expected_version':1,'request_id':'http-handoff-01'})
    assert handoff.status_code==200
    monkeypatch.setenv('WEALTH_OPERATOR_USER_IDS','U2')
    app.dependency_overrides[get_current_user]=lambda:{'user_id':'U2'}
    assert (await client.get('/api/v1/wealth/operator/tasks')).json()['items'][0]['task_id']==task['task_id']
    base=f"/api/v1/wealth/operator/tasks/{task['task_id']}"
    assert (await client.get(base+'/facts')).status_code==403
    assert (await client.get(base+'/context')).status_code==403
    claimed=await client.post(base+'/claim',json={'expected_version':2,'request_id':'http-claim-01'})
    assert claimed.status_code==200
    facts=await client.get(base+'/facts')
    assert facts.status_code==200 and [a['account_id'] for a in facts.json()['cash']]==['A1']
    original=await client.get(base+'/context')
    assert original.status_code==200 and original.json()['task']['account_id']=='A1'
    assert original.json()['conversation'] is None
    assert (await client.get('/api/v1/wealth/tasks/'+task['task_id'])).status_code==404


@pytest.mark.asyncio
async def test_two_operators_concurrently_claim_only_one_owner(store, monkeypatch):
    import asyncio
    monkeypatch.setenv('WEALTH_OPERATOR_USER_IDS','OP1,OP2')
    async with store() as db:
        task=await tasks.create_command(db,'U1','race-claim-session','legacy-v1',command('race-claim-original'))
        handoff=await tasks.control(db,'U1',task['task_id'],'handoff',{'request_id':'race-claim-handoff','expected_version':1})
    async def claim(actor):
        async with store() as db:
            try:
                result=await tasks.control(db,'U1',task['task_id'],'claim',
                    {'request_id':'race-claim-'+actor,'expected_version':handoff['version']},actor_id=actor)
                return result['owner_id']
            except WealthPolicyError as error:
                await db.rollback()
                assert error.code in {'STALE_TASK','HANDOFF_CONFLICT'}
                return None
    results=await asyncio.gather(claim('OP1'),claim('OP2'))
    winners=[owner for owner in results if owner]
    assert len(winners)==1
    async with store() as db:
        row=await tasks.owned(db,'U1',task['task_id'])
        assert row.owner_id==winners[0] and row.control_mode=='HUMAN' and row.version==3
