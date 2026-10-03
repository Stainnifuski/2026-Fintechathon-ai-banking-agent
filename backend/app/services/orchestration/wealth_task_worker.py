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
    submit = next((n for n in nodes if n.kind=='SUBMIT'), None)
    auth = await db.scalar(select(Authorization).where(Authorization.user_id==user_id,
        Authorization.operation_id==submit.operation_id)) if submit else None
    runnable = ready([{'node_id':n.node_id, 'status':n.status, 'depends_on':n.depends_on} for n in nodes])
    eligible_ids = {r['node_id'] for r in runnable}
    now = utc_now()
    # 租约到期只恢复原节点，后续执行先按原操作号查银行。
    for n in nodes:
        if n.kind=='SUBMIT' and n.status=='RUNNING' and n.lease_until and as_utc(n.lease_until, legacy_utc=True)<=now:
            n.status='UNKNOWN'; eligible_ids.add(n.node_id)
        elif n.kind=='SUBMIT' and n.status=='UNKNOWN':
            eligible_ids.add(n.node_id)
    node = next((n for n in sorted(nodes, key=lambda n:n.node_id) if n.node_id in eligible_ids), None)
    if node is None:
        await db.rollback(); return
    controlled = task.control_mode!='AGENT' or task.status=='PAUSED'
    query_only = controlled and node.kind=='SUBMIT' and node.status=='UNKNOWN'
    if controlled and node.kind not in {'SETTLEMENT', 'RESULT'} and not query_only:
        await db.rollback(); return
    if node.kind in {'CONFIRM','VERIFY'}:
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
            outcome=await bank.assets(user_id); status='COMPLETED'
        else:
            try: outcome=await bank.query(user_id, command.operation_id)
            except BankApiError as error:
                if query_only or node.kind!='SUBMIT' or error.status_code!=404 or isinstance(error,BankResponseUnknown): raise
                if not authorization_id: raise
                outcome=await bank.submit(user_id, command, authorization_id)
            status='COMPLETED' if node.kind=='SUBMIT' or outcome.get('status')=='SETTLED' else 'WAITING'
            if outcome.get('status') in {'REJECTED','CANCELLED'}: status='FAILED'
    except BankApiError as error:
        status='UNKNOWN' if isinstance(error,BankResponseUnknown) or node.kind=='SETTLEMENT' else 'WAITING'
        outcome={'code':error.code, 'operation_id':command.operation_id}
    await lock_profile(db, user_id)
    task = await db.get(Task, task_id, populate_existing=True)
    node = await db.get(Node, node_id, populate_existing=True)
    # 控制请求的版本不被worker覆盖；已取得银行事实仍按原号保存。
    node.status=status; node.lease_until=None; node.data={**node.data, 'output':outcome}
    if task.version==task_version and task.status!='PAUSED':
        task.status='RECONCILING' if status=='UNKNOWN' else 'FAILED' if status=='FAILED' else 'WAITING'
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
