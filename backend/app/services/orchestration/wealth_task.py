"""持久化任务图与控制请求；不读取或修改银行账本ORM。"""
import hashlib
import json
import os
from uuid import uuid4
from sqlalchemy import select, update
from ...domain.time import utc_now, utc_iso
from ...domain.wealth_task import validate_graph, ready
from ...models.wealth import (WealthTaskORM as Task, WealthTaskNodeORM as Node,
    WealthTaskControlORM as Control, WealthAuthorizationORM as Authorization)
from ...integrations.wealth_authorization import lock_profile, WealthPolicyError


TERMINAL = {'COMPLETED', 'CANCELLED', 'FAILED'}


def operator_allowed(user_id: str) -> bool:
    return user_id in {value.strip() for value in os.getenv('WEALTH_OPERATOR_USER_IDS', '').split(',') if value.strip()}


async def owned(db, user_id, task_id):
    task = await db.get(Task, task_id, populate_existing=True)
    if not task or task.user_id != user_id:
        raise WealthPolicyError('任务不存在', 'NOT_FOUND', 404)
    return task


async def view(db, task):
    nodes = (await db.scalars(select(Node).where(Node.task_id == task.task_id).order_by(Node.node_id))).all()
    return {'task_id':task.task_id, 'session_id':task.session_id, 'context_id':task.context_id,
        'account_id':task.account_id, 'version':task.version, 'status':task.status,
        'control_mode':task.control_mode, 'owner_id':task.owner_id, 'waiting_reason':task.waiting_reason,
        'goal':task.data.get('goal'), 'notes':task.data.get('notes', []),
        'nodes':[{'node_id':n.node_id, 'kind':n.kind, 'depends_on':n.depends_on, 'status':n.status,
            'operation_id':n.operation_id, 'attempts':n.attempts, **n.data} for n in nodes]}


async def create_command(db, user_id, session_id, context_id, command, *, sell_then_buy=False):
    await lock_profile(db, user_id)
    active = await db.scalar(select(Task).where(Task.user_id == user_id,
        Task.context_id == context_id, Task.status.not_in(TERMINAL)))
    if active:
        raise WealthPolicyError('已有进行中的交易任务，请先选择继续、暂停或取消未提交任务', 'ACTIVE_TASK_SELECTION_REQUIRED', 409)
    key = 'WT'+uuid4().hex
    task = Task(task_id=key, user_id=user_id, session_id=session_id, context_id=context_id,
        account_id=command.account_id, version=1, status='WAITING', control_mode='AGENT',
        waiting_reason='PERSONAL_CONFIRMATION', data={'goal':'sell_then_buy' if sell_then_buy else command.direction})
    db.add(task); await db.flush()
    sequence = ['READ_FACTS', 'CONFIRM', 'VERIFY', 'SUBMIT', 'SETTLEMENT']
    if sell_then_buy: sequence += ['REREAD_CASH', 'BUY_PLAN']
    else: sequence += ['RESULT']
    graph = []
    for index, kind in enumerate(sequence):
        nid = f'{key}:{index:02}'
        graph.append({'node_id':nid, 'kind':kind, 'depends_on':[] if index == 0 else [f'{key}:{index-1:02}'],
            'completion_condition': 'BANK_SETTLED' if kind == 'SETTLEMENT' else 'TRUSTED_FACT_OR_USER_ACTION'})
    validate_graph(graph)
    for item in graph:
        db.add(Node(node_id=item['node_id'], task_id=key, kind=item['kind'], depends_on=item['depends_on'],
            status='COMPLETED' if item['kind']=='READ_FACTS' else 'READY',
            operation_id=command.operation_id if item['kind']=='SUBMIT' else None,
            attempts=0, data={'completion_condition':item['completion_condition'],
                'command':command.model_dump() if item['kind']=='SUBMIT' else None}))
    await db.commit()
    return await view(db, task)


async def control(db, user_id, task_id, action, body, *, actor_id=None):
    actor = actor_id or user_id
    if action in {'claim', 'note', 'release'} and not actor_id:
        raise WealthPolicyError('需要服务端操作员角色', 'OPERATOR_REQUIRED', 403)
    await lock_profile(db, user_id)
    task = await owned(db, user_id, task_id)
    req_key = actor+':'+body['request_id']
    digest = hashlib.sha256(json.dumps([task_id, action, body], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    previous = await db.get(Control, req_key)
    if previous:
        if previous.digest != digest: raise WealthPolicyError('控制请求编号冲突', 'IDEMPOTENCY_CONFLICT', 409)
        return previous.result
    if task.version != body['expected_version']:
        raise WealthPolicyError('任务已更新，请刷新', 'STALE_TASK', 409)
    if actor_id and not operator_allowed(actor_id):
        raise WealthPolicyError('需要服务端操作员角色', 'OPERATOR_REQUIRED', 403)
    nodes = (await db.scalars(select(Node).where(Node.task_id == task_id))).all()
    if action == 'claim':
        if task.control_mode != 'HANDOFF_PENDING' or task.owner_id:
            raise WealthPolicyError('任务已被领取或未申请接管', 'HANDOFF_CONFLICT', 409)
        task.control_mode, task.owner_id = 'HUMAN', actor
    elif action in {'note', 'release'}:
        if task.control_mode != 'HUMAN' or task.owner_id != actor:
            raise WealthPolicyError('仅领取操作员可处理或交还', 'HANDOFF_CONFLICT', 409)
        if action == 'release':
            task.control_mode, task.owner_id, task.status = 'AGENT', None, 'PAUSED'
            task.waiting_reason = 'REVALIDATION_REQUIRED'
    else:
        if task.control_mode == 'HUMAN':
            raise WealthPolicyError('操作员接管中，请等待交还', 'HUMAN_CONTROLLED', 409)
        if action == 'handoff': task.control_mode = 'HANDOFF_PENDING'
        elif task.status in TERMINAL:
            raise WealthPolicyError('已完成任务不能回退账务', 'TASK_TERMINAL', 409)
        elif action == 'pause': task.status = 'PAUSED'
        elif action == 'resume':
            if task.control_mode != 'AGENT': raise WealthPolicyError('等待人工接管', 'HANDOFF_PENDING', 409)
            task.status, task.waiting_reason = 'WAITING', 'REVALIDATION_REQUIRED'
        elif action in {'rollback', 'cancel'}:
            submitted = any(n.kind=='SUBMIT' and n.status in {'RUNNING','COMPLETED','UNKNOWN'} for n in nodes)
            if submitted and action=='rollback':
                raise WealthPolicyError('银行提交或未知结果须按原号核对，不能回退账务', 'RECONCILIATION_REQUIRED', 409)
            for n in nodes:
                if n.status not in {'COMPLETED','RUNNING','UNKNOWN'}:
                    n.status = 'CANCELLED' if action=='cancel' else 'WAITING'
            operations = [n.operation_id for n in nodes if n.operation_id]
            await db.execute(update(Authorization).where(Authorization.user_id==user_id,
                Authorization.operation_id.in_(operations), Authorization.status.in_(['AWAITING_CONFIRMATION','AWAITING_MFA','VALID'])).values(status='EXPIRED'))
            task.status = 'CANCELLED' if action=='cancel' else 'PAUSED'
            task.waiting_reason = 'SUBMITTED_RESULTS_PRESERVED' if submitted else 'NEW_PREVIEW_REQUIRED'
        else: raise WealthPolicyError('控制动作不支持', 'INVALID_REQUEST', 422)
    task.version += 1
    task.data = {**task.data, 'notes':[*task.data.get('notes', []),
        {'actor_id':actor, 'action':action, 'reason':body.get('reason', ''), 'occurred_at':utc_iso(utc_now())}]}
    result = await view(db, task)
    db.add(Control(request_key=req_key, task_id=task_id, actor_id=actor, action=action,
        digest=digest, result=result, occurred_at=utc_now()))
    await db.commit()
    return result


async def require_write_control(db, user_id, command):
    """银行调用的控制策略；调用方持有与控制请求相同的用户锁。"""
    tasks = (await db.scalars(select(Task).where(Task.user_id==user_id,
        Task.account_id==command.account_id, Task.status.not_in(TERMINAL)))).all()
    for task in tasks:
        if task.control_mode != 'AGENT' or task.status == 'PAUSED':
            raise WealthPolicyError('任务暂停或人工接管中，禁止新银行写入', 'TASK_WRITE_BLOCKED', 409)
        nodes = (await db.scalars(select(Node).where(Node.task_id==task.task_id))).all()
        submit = next((n for n in nodes if n.operation_id==command.operation_id), None)
        if not submit:
            raise WealthPolicyError('请先处理当前交易任务，不可并行提交新交易', 'ACTIVE_TASK_SELECTION_REQUIRED', 409)
        completed = {n.node_id for n in nodes if n.status=='COMPLETED'}
        if not set(submit.depends_on) <= completed or submit.status=='CANCELLED':
            raise WealthPolicyError('任务依赖未完成，禁止提交', 'TASK_DEPENDENCY_PENDING', 409)
        if submit.lease_version is not None and submit.lease_version != task.version:
            raise WealthPolicyError('任务控制版本已变化', 'STALE_TASK', 409)
