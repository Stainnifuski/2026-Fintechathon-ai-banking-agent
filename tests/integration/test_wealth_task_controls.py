"""持久化依赖、控制与旧银行入口约束的双库验证。"""
import pytest
from sqlalchemy import select
from tests.integration.test_wealth_bank import store, command, assess, authorize, move
from backend.app.models.wealth import WealthTaskORM, WealthTaskNodeORM, WealthAuthorizationORM
from backend.app.services.orchestration import wealth_task as tasks
from backend.app.services.orchestration.wealth_task_worker import tick
from backend.app.services.bank import wealth_service as bank
from backend.app.integrations.wealth_authorization import WealthPolicyError
from backend.app.integrations.bank_api import BankApiError
from backend.app.domain.wealth_task import validate_graph
from backend.app.services.bank.transfer_service import BankError


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
        with pytest.raises(WealthPolicyError) as error: await bank.accept_many(db,'U1',[(cmd,aid)])
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
