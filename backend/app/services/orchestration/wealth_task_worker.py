"""短事务DAG调度；执行工具固定，未知结果始终保留原号。"""
import asyncio
import logging
from datetime import timedelta
from sqlalchemy import select
from ...domain.time import utc_now, as_utc
from ...domain.wealth_task import ready
from ...models.wealth import WealthTaskORM as Task, WealthTaskNodeORM as Node, WealthAuthorizationORM as Authorization
from ...schemas.wealth import InvestmentCommand
from ...integrations.wealth_authorization import lock_profile
from ...integrations.bank_api import BankApiError, BankResponseUnknown
from ...integrations.wealth_bank_api import HttpWealthBankApi
from .wealth_task import TERMINAL

logger = logging.getLogger(__name__)


async def tick(db, task_id, bank):
    initial = await db.get(Task, task_id)
    if initial is None or initial.status in TERMINAL:
        return
    user_id = initial.user_id
    await lock_profile(db, user_id)
    task = await db.get(Task, task_id, populate_existing=True)
    nodes = (await db.scalars(select(Node).where(Node.task_id==task_id))).all()
    runnable = ready([{'node_id':n.node_id, 'status':n.status, 'depends_on':n.depends_on} for n in nodes])
    eligible_ids = {r['node_id'] for r in runnable}
    now = utc_now()
    controlled = task.control_mode!='AGENT' or task.status=='PAUSED' or task.data.get('stop_new_submissions',False)
    # 租约到期只恢复原节点，后续执行先按原操作号查银行。
    for n in nodes:
        if n.kind=='SUBMIT' and n.status=='RUNNING' and n.lease_until and as_utc(n.lease_until, legacy_utc=True)<=now:
            n.status='UNKNOWN'; eligible_ids.add(n.node_id)
        elif n.kind=='SUBMIT' and n.status=='UNKNOWN':
            eligible_ids.add(n.node_id)
        elif n.kind in {'SETTLEMENT','REREAD_CASH'} and (n.status=='UNKNOWN' or
                n.status=='RUNNING' and n.lease_until and as_utc(n.lease_until,legacy_utc=True)<=now):
            n.status='READY';eligible_ids.add(n.node_id)
        elif n.status=='RUNNING' and n.lease_until and as_utc(n.lease_until,legacy_utc=True)<=now:
            n.status='READY'
            if all(next(p for p in nodes if p.node_id==dep).status=='COMPLETED' for dep in n.depends_on):
                eligible_ids.add(n.node_id)
    if task.data.get('stop_new_submissions'):
        outstanding=any(n.status in {'RUNNING','UNKNOWN'} or n.kind=='SETTLEMENT' and n.status in {'READY','WAITING'} and
            all(next(p for p in nodes if p.node_id==dep).status=='COMPLETED' for dep in n.depends_on) for n in nodes)
        if not outstanding:
            task.status='PARTIAL_COMPLETED' if any(n.kind=='SETTLEMENT' and n.status=='COMPLETED' for n in nodes) else 'CANCELLED'
            task.waiting_reason='SUBMITTED_RESULTS_PRESERVED'
            await db.commit();return
    node = next((n for n in sorted(nodes, key=lambda n:(n.attempts,n.node_id)) if n.node_id in eligible_ids and
        (not controlled or n.kind in {'SETTLEMENT','RESULT'} or n.kind=='SUBMIT' and n.status=='UNKNOWN')), None)
    if node is None:
        await db.rollback(); return
    submit=next((n for n in nodes if n.kind=='SUBMIT' and
        n.operation_id==node.data.get('operation_ref',node.operation_id)),None)
    auth=await db.scalar(select(Authorization).where(Authorization.user_id==user_id,
        Authorization.operation_id==submit.operation_id)) if submit else None
    query_only = controlled and node.kind=='SUBMIT' and node.status=='UNKNOWN'
    if controlled and node.kind not in {'SETTLEMENT', 'RESULT'} and not query_only:
        await db.rollback(); return
    if node.kind in {'CONFIRM','VERIFY'}:
        node.attempts+=1
        permitted = auth and (auth.confirmed_at if node.kind=='CONFIRM' else auth.status=='VALID' and auth.verified_at)
        if not permitted:
            node.status='WAITING'; task.waiting_reason='PERSONAL_CONFIRMATION' if node.kind=='CONFIRM' else 'SIMULATED_AUTHORIZATION'
        else: node.status='COMPLETED'
        await db.commit(); return
    if node.kind=='RESULT':
        node.status='COMPLETED'; task.status='COMPLETED'; task.waiting_reason=None
        await db.commit(); return
    if node.kind=='BUY_PLAN':
        node.status='WAITING'; task.status='WAITING'; task.waiting_reason='NEW_BUY_PLAN_AND_CONFIRMATION'
        await db.commit(); return
    if node.kind not in {'SUBMIT', 'SETTLEMENT', 'REREAD_CASH'}:
        await db.rollback(); return
    node.status='RUNNING'; node.attempts+=1; node.lease_version=task.version
    node.lease_until=now+timedelta(seconds=90)
    task_version, node_id = task.version, node.node_id
    command = InvestmentCommand.model_validate(submit.data['command']) if submit else None
    authorization_id = auth.authorization_id if auth else None
    await db.commit()
    outcome, status = {}, 'WAITING'
    try:
        if node.kind=='REREAD_CASH':
            outcome=await bank.call('POST','/assets',{'subject_user_id':user_id,'account_id':task.account_id}); status='COMPLETED'
        else:
            try: outcome=await bank.query(user_id, command.operation_id)
            except BankApiError as error:
                if query_only or node.kind!='SUBMIT' or error.status_code!=404 or isinstance(error,BankResponseUnknown): raise
                if not authorization_id: raise
                if command.plan_id:
                    # 整份申购仍由既有可信批量入口原子占款，不按节点偷偷拆成单笔。
                    status='WAITING';outcome={'code':'TRUSTED_PLAN_EXECUTION_REQUIRED','operation_id':command.operation_id}
                    raise BankApiError('请在整份方案可信确认页执行','TRUSTED_PLAN_EXECUTION_REQUIRED',409)
                outcome=await bank.submit(user_id, command, authorization_id)
            status='COMPLETED' if node.kind=='SUBMIT' or outcome.get('status')=='SETTLED' else 'WAITING'
            if outcome.get('status') in {'REJECTED','CANCELLED'}: status='FAILED'
    except BankApiError as error:
        # 暂停/接管时原号暂未查到，不能把未知提交降为WAITING并停止核对。
        status='UNKNOWN' if query_only or isinstance(error,BankResponseUnknown) or node.kind=='SETTLEMENT' else 'WAITING'
        outcome={'code':error.code, 'operation_id':command.operation_id if command else None}
    await lock_profile(db, user_id)
    task = await db.get(Task, task_id, populate_existing=True)
    node = await db.get(Node, node_id, populate_existing=True)
    # 控制请求的版本不被worker覆盖；已取得银行事实仍按原号保存。
    node.status=status; node.lease_until=None; node.data={**node.data, 'output':outcome}
    if task.version==task_version and task.status!='PAUSED':
        task.status='RECONCILING' if status in {'UNKNOWN','FAILED'} else 'WAITING'
        if status=='FAILED':
            task.data={**task.data,'stop_new_submissions':True}
            for pending in (await db.scalars(select(Node).where(Node.task_id==task_id))).all():
                if pending.status in {'READY','WAITING'} and pending.kind not in {'SETTLEMENT','RESULT'}:
                    pending.status='CANCELLED'
        task.waiting_reason='ORIGINAL_OPERATION_QUERY' if status=='UNKNOWN' else 'BANK_SETTLEMENT' if node.kind=='SETTLEMENT' and status!='COMPLETED' else None
    await db.commit()


async def loop(sessions):
    while True:
        await asyncio.sleep(3)
        async with sessions() as db:
            ids=list(await db.scalars(select(Task.task_id).where(Task.status.not_in(TERMINAL))))
        for key in ids:
            async with sessions() as db:
                try: await tick(db, key, HttpWealthBankApi())
                except Exception:
                    await db.rollback(); logger.warning('理财任务待核对，保留原节点和操作号')
