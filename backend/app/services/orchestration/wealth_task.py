"""持久化任务图与控制请求；不读取或修改银行账本ORM。"""
import hashlib
import json
import os
from uuid import uuid4
from sqlalchemy import select, update
from ...domain.time import utc_now, utc_iso
from ...domain.wealth_task import validate_graph, ready
from ...models.wealth import (WealthTaskORM as Task, WealthTaskNodeORM as Node,
    WealthTaskControlORM as Control, WealthAuthorizationORM as Authorization, WealthPlanORM)
from ...integrations.wealth_authorization import lock_profile, WealthPolicyError


TERMINAL = {'COMPLETED', 'PARTIAL_COMPLETED', 'CANCELLED', 'FAILED'}


def operator_allowed(user_id: str) -> bool:
    return user_id in {value.strip() for value in os.getenv('WEALTH_OPERATOR_USER_IDS', '').split(',') if value.strip()}


async def owned(db, user_id, task_id):
    task = await db.get(Task, task_id, populate_existing=True)
    if not task or task.user_id != user_id:
        from ...models.wealth_research import WealthDemoReceiptORM
        receipts=(await db.scalars(select(WealthDemoReceiptORM).where(WealthDemoReceiptORM.user_id==user_id,
            WealthDemoReceiptORM.action=='delete'))).all()
        if any(task_id in receipt.result.get('deleted_task_ids',[]) for receipt in receipts):
            raise WealthPolicyError('旧归档任务已物理删除','GONE',410)
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
                'operation_ref':command.operation_id,
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
    if len(req_key)>96:req_key=hashlib.sha256(req_key.encode()).hexdigest()
    digest = hashlib.sha256(json.dumps([task_id, action, body], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    previous = await db.get(Control, req_key)
    if previous:
        if previous.digest != digest: raise WealthPolicyError('控制请求编号冲突', 'IDEMPOTENCY_CONFLICT', 409)
        return previous.result
    if task.version != body['expected_version']:
        raise WealthPolicyError('任务已更新，请刷新', 'STALE_TASK', 409)
    if task.waiting_reason=='ARCHIVED_RUN' and action in {'resume','rollback'}:
        raise WealthPolicyError('旧测试轮次已归档，不能恢复写入', 'ARCHIVED_RUN', 409)
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
            submitted_refs={n.operation_id for n in nodes if n.kind=='SUBMIT' and n.status in {'RUNNING','COMPLETED','UNKNOWN'}}
            for n in nodes:
                if n.kind=='SETTLEMENT' and n.data.get('operation_ref') in submitted_refs:
                    continue
                if n.status not in {'COMPLETED','RUNNING','UNKNOWN'}:
                    n.status = 'CANCELLED' if action=='cancel' else 'WAITING'
            operations = [n.operation_id for n in nodes if n.operation_id]
            await db.execute(update(Authorization).where(Authorization.user_id==user_id,
                Authorization.operation_id.in_(operations), Authorization.status.in_(['AWAITING_CONFIRMATION','AWAITING_MFA','VALID'])).values(status='EXPIRED'))
            task.status = ('RECONCILING' if submitted else 'CANCELLED') if action=='cancel' else 'PAUSED'
            if action=='cancel' and submitted:
                task.data={**task.data,'stop_new_submissions':True}
            task.waiting_reason = 'SUBMITTED_RESULTS_PRESERVED' if submitted else 'NEW_PREVIEW_REQUIRED'
            if action=='cancel' and not submitted and task.data.get('plan_id'):
                plan=await db.get(WealthPlanORM,task.data['plan_id'])
                if plan and plan.user_id==user_id: plan.status='CANCELLED'
        else: raise WealthPolicyError('控制动作不支持', 'INVALID_REQUEST', 422)
    task.version += 1
    task.data = {**task.data, 'notes':[*task.data.get('notes', []),
        {'actor_id':actor, 'action':action, 'reason':body.get('reason', ''), 'occurred_at':utc_iso(utc_now())}]}
    result = await view(db, task)
    db.add(Control(request_key=req_key, task_id=task_id, actor_id=actor, action=action,
        digest=digest, result=result, occurred_at=utc_now()))
    await db.commit()
    return result


async def revise_command(db, user_id, task_id, expected_version, command):
    """只更新未提交子图；旧操作号与确认留在历史，不修改银行订单。"""
    await lock_profile(db, user_id)
    task=await owned(db,user_id,task_id)
    was_paused=task.status=='PAUSED'
    nodes=(await db.scalars(select(Node).where(Node.task_id==task_id))).all()
    if task.version!=expected_version or task.control_mode!='AGENT' or task.status in TERMINAL:
        raise WealthPolicyError('任务已变化或不归Agent处理', 'STALE_TASK', 409)
    submit=next(n for n in nodes if n.kind=='SUBMIT')
    if submit.status in {'RUNNING','COMPLETED','UNKNOWN'}:
        raise WealthPolicyError('原笔已提交或结果未知，先按原号核对，不能直接改单', 'RECONCILIATION_REQUIRED', 409)
    old=submit.operation_id
    await db.execute(update(Authorization).where(Authorization.user_id==user_id,
        Authorization.operation_id==old,Authorization.status.in_(['AWAITING_CONFIRMATION','AWAITING_MFA','VALID'])).values(status='EXPIRED'))
    task.data={**task.data,'revision_history':[*task.data.get('revision_history',[]),
        {'version':task.version,'operation_id':old,'command':submit.data['command']}]}
    task.account_id=command.account_id; task.version+=1; task.status='PAUSED' if was_paused else 'WAITING'; task.waiting_reason='PERSONAL_CONFIRMATION'
    for node in nodes:
        if node.kind!='READ_FACTS': node.status='READY'
        node.lease_version=None; node.lease_until=None
        node.data={**node.data,'operation_ref':command.operation_id,'output':None}
        if node.kind=='SUBMIT':
            node.operation_id=command.operation_id; node.data={**node.data,'command':command.model_dump()}
    await db.commit()
    return await view(db,task)


async def register_plan(db, user_id, session_id, context_id, plan):
    """方案逐笔节点持久化；申购仍由可信整份方案原子执行入口提交。"""
    from sqlalchemy import delete
    await lock_profile(db,user_id)
    task=await db.scalar(select(Task).where(Task.user_id==user_id,Task.context_id==context_id,Task.status.not_in(TERMINAL)))
    if task and task.data.get('plan_id')!=plan['plan_id'] and not (task.data.get('goal')=='sell_then_buy' and not task.data.get('plan_id')):
        raise WealthPolicyError('已有交易任务，请明确选择当前任务或取消未提交部分','ACTIVE_TASK_SELECTION_REQUIRED',409)
    was_paused=bool(task and task.status=='PAUSED')
    continuation=False
    previous_nodes=[]
    if task:
        old_nodes=(await db.scalars(select(Node).where(Node.task_id==task.task_id))).all()
        if any(n.kind=='SUBMIT' and n.status in {'RUNNING','UNKNOWN','COMPLETED'} for n in old_nodes):
            buy_gate=next((n for n in old_nodes if n.kind=='BUY_PLAN'),None)
            reread=next((n for n in old_nodes if n.kind=='REREAD_CASH'),None)
            settled=[n for n in old_nodes if n.kind=='SETTLEMENT']
            continuation=bool(plan['stage']=='BUY' and buy_gate and reread and reread.status=='COMPLETED'
                and settled and all(n.status=='COMPLETED' for n in settled))
            if not continuation:
                raise WealthPolicyError('须按原号确认实际到账并重读资金后，才能建立新买入子图','RECONCILIATION_REQUIRED',409)
            buy_gate.status='COMPLETED'
            previous_nodes=old_nodes
        task.data={**task.data,'revision_history':[*task.data.get('revision_history',[]),
            {'version':task.version,'plan_version':task.data.get('plan_version'),
                'commands':[n.data.get('command') for n in old_nodes if n.kind=='SUBMIT']}]}
        task.version+=1
        if not continuation:
            await db.execute(delete(Node).where(Node.task_id==task.task_id))
    else:
        task=Task(task_id='WT'+uuid4().hex,user_id=user_id,session_id=session_id,context_id=context_id,
            account_id=plan['request']['account_id'],version=1,status='WAITING',control_mode='AGENT',data={})
        db.add(task);await db.flush()
    task.data={**task.data,'goal':task.data.get('goal',plan['request']['kind']),
        'plan_id':plan['plan_id'],'plan_version':plan['version'],'stage':plan['stage']}
    task.status='PAUSED' if was_paused else 'WAITING';task.waiting_reason='PERSONAL_CONFIRMATION'
    prefix=task.task_id+f':v{plan["version"]:04}' if continuation else task.task_id
    graph=[{'node_id':prefix+':00','kind':'READ_FACTS','depends_on':[buy_gate.node_id] if continuation else [], 'completion_condition':'CURRENT_BANK_FACTS'}]
    settles=[]
    for index,item in enumerate(plan['items']):
        previous=graph[0]['node_id']
        for step,kind in enumerate(['CONFIRM','VERIFY','SUBMIT','SETTLEMENT']):
            nid=f'{prefix}:{index+1:02}:{step:02}'
            graph.append({'node_id':nid,'kind':kind,'depends_on':[previous],
                'completion_condition':'BANK_SETTLED' if kind=='SETTLEMENT' else 'TRUSTED_FACT_OR_USER_ACTION',
                'command':item['command'] if kind=='SUBMIT' else None, 'operation_ref':item['command']['operation_id']})
            previous=nid
        settles.append(previous)
    if plan['stage']=='SELL':
        graph.append({'node_id':prefix+':90','kind':'REREAD_CASH','depends_on':settles,'completion_condition':'CURRENT_ACCOUNT_CASH'})
        graph.append({'node_id':prefix+':91','kind':'BUY_PLAN','depends_on':[prefix+':90'],'completion_condition':'NEW_PLAN_AND_CONFIRMATION'})
    else: graph.append({'node_id':prefix+':99','kind':'RESULT','depends_on':settles,'completion_condition':'ALL_BANK_RESULTS'})
    validate_graph([{'node_id':n.node_id,'kind':n.kind,'depends_on':n.depends_on,
        'completion_condition':n.data['completion_condition']} for n in previous_nodes]+graph)
    for row in graph:
        db.add(Node(node_id=row['node_id'],task_id=task.task_id,kind=row['kind'],depends_on=row['depends_on'],
            status='COMPLETED' if row['kind']=='READ_FACTS' else 'READY',attempts=0,
            operation_id=row.get('operation_ref') if row['kind']=='SUBMIT' else None,
            data={k:v for k,v in row.items() if k not in {'node_id','kind','depends_on'}}))
    await db.commit()
    return await view(db,task)


async def linked_plan_task(db,user_id,plan_id):
    rows=(await db.scalars(select(Task).where(Task.user_id==user_id,Task.status.not_in(TERMINAL)))).all()
    return next((row for row in rows if row.data.get('plan_id')==plan_id),None)


async def require_plan_edit(db,user_id,plan_id):
    task=await linked_plan_task(db,user_id,plan_id)
    if task and task.control_mode!='AGENT':
        raise WealthPolicyError('方案正在等待或由人工接管，不能从旧接口修改','TASK_WRITE_BLOCKED',409)
    return task


async def require_write_control(db, user_id, command, authorization=None):
    """银行调用的控制策略；调用方持有与控制请求相同的用户锁。"""
    tasks = (await db.scalars(select(Task).where(Task.user_id==user_id,
        Task.account_id==command.account_id, Task.status.not_in(TERMINAL)))).all()
    for task in tasks:
        if task.control_mode != 'AGENT' or task.status == 'PAUSED' or task.data.get('stop_new_submissions'):
            raise WealthPolicyError('任务暂停或人工接管中，禁止新银行写入', 'TASK_WRITE_BLOCKED', 409)
        nodes = (await db.scalars(select(Node).where(Node.task_id==task.task_id))).all()
        submit = next((n for n in nodes if n.operation_id==command.operation_id), None)
        if not submit:
            raise WealthPolicyError('请先处理当前交易任务，不可并行提交新交易', 'ACTIVE_TASK_SELECTION_REQUIRED', 409)
        # 最终银行授权已核验后，允许同步可信确认/验证节点；不能凭模型文本推进。
        if authorization:
            completed = {n.node_id for n in nodes if n.status=='COMPLETED'}
            for node in sorted(nodes, key=lambda n:n.node_id):
                if (node.kind in {'CONFIRM','VERIFY'} and node.data.get('operation_ref')==command.operation_id
                        and set(node.depends_on)<=completed):
                    node.status='COMPLETED'; completed.add(node.node_id)
        completed = {n.node_id for n in nodes if n.status=='COMPLETED'}
        if not set(submit.depends_on) <= completed or submit.status=='CANCELLED':
            raise WealthPolicyError('任务依赖未完成，禁止提交', 'TASK_DEPENDENCY_PENDING', 409)
        if submit.lease_version is not None and submit.lease_version != task.version:
            raise WealthPolicyError('任务控制版本已变化', 'STALE_TASK', 409)
