"""A new manual workspace retires drafts, never bank accounting (isolated stores)."""
import pytest
from sqlalchemy import select
from tests.integration.test_wealth_bank import store, command, assess, authorize
from backend.app.models.wealth import WealthTaskNodeORM, WealthAuthorizationORM
from backend.app.services.orchestration import wealth_task as tasks
from backend.app.services.orchestration.wealth_task_worker import tick
from backend.app.services.bank import wealth_service as bank
from backend.app.integrations.wealth_authorization import WealthPolicyError


@pytest.mark.asyncio
async def test_reset_discards_paused_draft_and_allows_new_task(store):
    async with store() as db:
        await assess(db)
        old=command('reset-old-draft')
        task=await tasks.create_command(db,'U1','old-window','legacy-v1',old)
        auth=await authorize(db,old)
        await tasks.control(db,'U1',task['task_id'],'pause',{'expected_version':1,'request_id':'pause-old-window'})
        # An independent preview has no graph yet; refresh must retire it too.
        orphan=await authorize(db,command('unlinked-old-preview'))
        result=await tasks.reset_workspace(db,'U1','legacy-v1')
        assert result['detached_task_ids']==[task['task_id']]
        assert (await db.get(WealthAuthorizationORM,auth)).status=='EXPIRED'
        assert (await db.get(WealthAuthorizationORM,orphan)).status=='EXPIRED'
        assert (await tasks.owned(db,'U1',task['task_id'])).status=='CANCELLED'
        assert (await tasks.reset_workspace(db,'U1','legacy-v1'))['detached_task_ids']==[]
        new=await tasks.create_command(db,'U1','new-window','legacy-v1',command('reset-new-draft'))
        assert new['task_id']!=task['task_id'] and new['status']=='WAITING'
        with pytest.raises(WealthPolicyError) as error:
            await tasks.require_write_control(db,'U1',old)
        assert error.value.code=='ACTIVE_TASK_SELECTION_REQUIRED'


@pytest.mark.asyncio
async def test_reset_preserves_accepted_order_holds_and_other_users(store):
    async with store() as db:
        await assess(db)
        old=command('reset-accepted-order')
        task=await tasks.create_command(db,'U1','accepted-window','legacy-v1',old)
        aid=await authorize(db,old)
        await bank.accept_many(db,'U1',[(old,aid)])
        before=await bank.assets(db,'U1')
        peer=await tasks.create_command(db,'U2','peer-window','legacy-v1',command('peer-draft').model_copy(update={'account_id':'A2'}))
        await tasks.reset_workspace(db,'U1','legacy-v1')
        after=await bank.assets(db,'U1')
        for field in ('cash','orders','lots'):
            assert before[field]==after[field]
        for field in ('cash_balance_fen','held_fen','available_cash_fen','total_assets_fen'):
            assert before['asset_summary'][field]==after['asset_summary'][field]
        assert (await db.get(WealthAuthorizationORM,aid)).status=='CONSUMED'
        assert (await tasks.owned(db,'U2',peer['task_id'])).version==1
        await tasks.create_command(db,'U1','next-window','legacy-v1',command('next-order'))
        assert (await bank.owned_order(db,'U1',old.operation_id)).status!='CANCELLED'


@pytest.mark.asyncio
async def test_unknown_submission_remains_query_only_without_locking_new_workspace(store):
    async with store() as db:
        task=await tasks.create_command(db,'U1','unknown-window','legacy-v1',command('unknown-window-order'))
        node=await db.scalar(select(WealthTaskNodeORM).where(WealthTaskNodeORM.task_id==task['task_id'],WealthTaskNodeORM.kind=='SUBMIT'))
        node.status='UNKNOWN';await db.commit()
        await tasks.reset_workspace(db,'U1','legacy-v1')
        await tasks.create_command(db,'U1','fresh-window','legacy-v1',command('fresh-window-order'))
        class QueryOnly:
            async def query(self,user,operation):
                assert operation=='unknown-window-order'
                from backend.app.integrations.bank_api import BankApiError
                raise BankApiError('暂未查到','NOT_FOUND',404)
            async def submit(self,*args):pytest.fail('closed workspace must never resubmit')
        await tick(db,task['task_id'],QueryOnly())
        assert (await tasks.owned(db,'U1',task['task_id'])).status not in tasks.TERMINAL
