"""Transient wealth workspace state; bank facts and unresolved dependencies survive."""
from ..audit.spans import audited
from datetime import datetime,timedelta
from uuid import uuid4
from sqlalchemy import select, delete
from ...domain.time import utc_now, as_utc
from ...models.wealth import (WealthConversationORM as Conversation, WealthPlanORM as Plan,
    WealthTaskORM as Task, WealthTaskNodeORM as Node, WealthTaskControlORM as Control,
    WealthAuthorizationORM as Authorization, WealthExecutionORM as Execution, WealthOrderORM as Order)
from ...models.wealth_research import WealthChatRequestORM as ChatRequest
from ...integrations.wealth_authorization import lock_profile, WealthPolicyError

ACCEPTED = {'ACCEPTED','PENDING_PRICING','PENDING_CASH','SETTLED','CANCELLED'}


@audited("wealth")
async def prune_tasks(db, user_id=None):
    """Keep only execution structure for complex work; terminal receipts have no graph."""
    rows=list(await db.scalars(select(Task).where(Task.user_id==user_id) if user_id else select(Task)))
    removed=0
    for task in rows:
        if task.data.get('compacted'):continue
        nodes=list(await db.scalars(select(Node).where(Node.task_id==task.task_id)))
        submits=[n for n in nodes if n.kind=='SUBMIT']
        orders=[await db.get(Order,n.operation_id) for n in submits]
        known=bool(submits) and all(o and o.user_id==task.user_id and o.status in ACCEPTED for o in orders)
        unresolved=any(n.status in {'RUNNING','UNKNOWN'} for n in nodes)
        complex_work=task.data.get('goal')=='sell_then_buy' or len(submits)>1 or any(n.kind=='BUY_PLAN' for n in nodes)
        unsettled=any(o and o.status not in {'SETTLED','CANCELLED'} for o in orders)
        terminal=task.status in {'COMPLETED','CANCELLED','PARTIAL_COMPLETED','FAILED'} and not unresolved and not (complex_work and unsettled)
        disposable_single=known and not complex_work and not unresolved
        if terminal or disposable_single:
            refs=[n.operation_id for n in submits if n.operation_id]
            await db.execute(delete(Node).where(Node.task_id==task.task_id))
            task.data={'goal':task.data.get('goal'),'compacted':True,'operation_ids':refs,
                'parent_task_id':task.data.get('parent_task_id'),'child_task_id':task.data.get('child_task_id')}
            if disposable_single:task.status='COMPLETED';task.waiting_reason='BANK_ORDER_QUERY'
            for receipt in await db.scalars(select(Control).where(Control.task_id==task.task_id)):
                receipt.result={k:v for k,v in receipt.result.items() if k in {
                    'schema_version','child_task_id','task_id','session_id','parent_task_id','request_id',
                    'version','session_version','status','waiting_reason','error','code'}}
                receipt.result={**receipt.result,'operation_ids':refs,'compacted':True}
            removed+=len(nodes)
    await db.execute(delete(Execution).where(Execution.status.in_(ACCEPTED),
        *([Execution.user_id==user_id] if user_id else [])))
    return removed


@audited("wealth")
async def close(db,user_id,session_id):
    await lock_profile(db,user_id)
    session=await db.get(Conversation,session_id,populate_existing=True)
    if session is None:return {'closed':True}
    if session.user_id!=user_id:raise WealthPolicyError('会话不存在','NOT_FOUND',404)
    context=session.data.get('context_id','legacy-v1')
    tasks=list(await db.scalars(select(Task).where(Task.user_id==user_id,Task.session_id==session_id)))
    plan_ids={t.data.get('plan_id') for t in tasks if t.data.get('plan_id')}
    if session.data.get('active_plan'):plan_ids.add(session.data['active_plan']['plan_id'])
    for task in tasks:
        if task.data.get('compacted'):continue
        nodes=list(await db.scalars(select(Node).where(Node.task_id==task.task_id)))
        accepted=False;unknown=False
        for node in nodes:
            if node.kind=='SUBMIT':
                order=await db.get(Order,node.operation_id)
                if order and order.user_id==user_id:
                    accepted=True;node.status='COMPLETED'
                elif node.status in {'RUNNING','UNKNOWN'}:unknown=True
                else:node.status='CANCELLED'
            elif node.kind in {'CONFIRM','VERIFY','REQUIRE_INPUT','PLAN','RESEARCH','READ_FACTS'}:
                if node.status!='COMPLETED':node.status='CANCELLED'
            # Strip text/whole cards, keep commands and facts needed for original-number recovery.
            node.data={k:v for k,v in node.data.items() if k in {'command','operation_ref','output',
                'completion_condition','waiting_reason','dependency_name','parent_task_id'}}
            if node.kind not in {'SUBMIT','SETTLEMENT','REREAD_CASH','REQUIRE_INPUT'}:
                node.data.pop('output',None)
        task.data={k:v for k,v in task.data.items() if k in {'goal','parent_task_id','child_task_id',
            'requirements','requirement_version','stop_new_submissions','deferred_request','external_processing'}}
        if task.data.get('requirements'):
            task.data['requirements']={**task.data['requirements'],'fields':{
                k:{a:b for a,b in v.items() if a not in {'quote','source_message','source_message_id','message_id','source_turn'}}
                for k,v in task.data['requirements'].get('fields',{}).items()}}
        if 'deferred_request' in task.data:
            # Registered external dependencies must remain usable; raw request is not a chat archive.
            task.data['deferred_request']={**task.data['deferred_request'],'session_id':None,'version':None}
        task.data={**task.data,'session_closed':True}
        if not accepted and not unknown and not task.data.get('deferred_request'):
            task.status='CANCELLED';task.waiting_reason=None
        else:task.waiting_reason='NEW_SESSION_REVALIDATION'
    for auth in await db.scalars(select(Authorization).where(Authorization.user_id==user_id,
            Authorization.status.not_in(['CONSUMED','EXPIRED']))):
        if (auth.command.get('plan_id') in plan_ids or auth.command.get('operation_id')==
                (session.data.get('active_command') or {}).get('operation_id') or
                auth.authorization_id in session.data.get('preview_ids',[])):
            if await db.get(Order,auth.operation_id) is None:auth.status='EXPIRED'
    for pid in plan_ids:
        row=await db.get(Plan,pid)
        if row and row.user_id==user_id:await db.delete(row)
    for task in tasks:
        if task.data.get('compacted'):continue
        for receipt in await db.scalars(select(Control).where(Control.task_id==task.task_id)):
            receipt.result={k:v for k,v in receipt.result.items() if k in {
                'schema_version','child_task_id','task_id','session_id','parent_task_id','request_id',
                'version','session_version','status','waiting_reason','error','code'}}
    # No history or requirements retained. Tombstone blocks late requests from this window.
    session.data={'closed':True,'window_session':True,'context_id':context,
        'closed_at':session.data.get('closed_at') or utc_now().isoformat()};session.version+=1
    await db.execute(delete(ChatRequest).where(ChatRequest.user_id==user_id,
        ChatRequest.response['session_id'].as_string()==session_id))
    await prune_tasks(db,user_id)
    await db.commit()
    return {'closed':True}


@audited("wealth")
async def start(db,user_id,context_id):
    await lock_profile(db,user_id)
    rows=list(await db.scalars(select(Conversation).where(Conversation.user_id==user_id)))
    for row in rows:
        if row.data.get('window_session'):await close(db,user_id,row.session_id)
    sid='WC'+uuid4().hex
    db.add(Conversation(session_id=sid,user_id=user_id,version=1,
        data={'window_session':True,'context_id':context_id,'history':[],'pending':{},'candidates':[]}))
    await db.commit()
    return {'session_id':sid,'version':1}


@audited("wealth")
async def clean_archives(db, *, apply=False):
    """Explicit admin cleanup, including confirmed wealth records on ordinary accounts."""
    from sqlalchemy import func
    counts={model.__tablename__:await db.scalar(select(func.count()).select_from(model))
        for model in (Conversation,Plan,ChatRequest,Task,Node,Execution)}
    if not apply:return {'preview':counts}
    for row in list(await db.scalars(select(Conversation))):
        await close(db,row.user_id,row.session_id)
    # Legacy orphan graphs have no conversation; use a temporary identity only to reuse guarded closure.
    for task in list(await db.scalars(select(Task))):
        if task.data.get('compacted'):continue
        if await db.get(Conversation,task.session_id) is None:
            db.add(Conversation(session_id=task.session_id,user_id=task.user_id,version=1,
                data={'context_id':task.context_id}));await db.commit()
            await close(db,task.user_id,task.session_id)
    await db.execute(delete(Conversation));await db.execute(delete(ChatRequest));await db.execute(delete(Plan))
    await prune_tasks(db)
    await db.commit()
    return {'before':counts,'after':{model.__tablename__:await db.scalar(select(func.count()).select_from(model))
        for model in (Conversation,Plan,ChatRequest,Task,Node,Execution)}}


@audited("wealth")
async def expire(db):
    cutoff=utc_now()-timedelta(hours=24)
    for row in list(await db.scalars(select(Conversation))):
        expired_at=datetime.fromisoformat(row.data['closed_at']) if row.data.get('closed_at') else row.updated_at
        if as_utc(expired_at,legacy_utc=True)<cutoff:
            if not row.data.get('closed'):await close(db,row.user_id,row.session_id)
            await db.delete(row)
    await prune_tasks(db)
    await db.execute(delete(ChatRequest).where(ChatRequest.started_at<cutoff))
    for plan in list(await db.scalars(select(Plan).where(Plan.updated_at<cutoff))):
        if plan.status not in {'EXECUTING','WAITING_SETTLEMENT'}:await db.delete(plan)
    await db.commit()


async def invalidate(db,user_id,data):
    """Semantic changes and table drafts invalidate the same unsubmitted authority."""
    plan_id=(data.get('active_plan') or {}).get('plan_id')
    await lock_profile(db,user_id)
    plan=await db.get(Plan,plan_id) if plan_id else None
    if plan and plan.user_id==user_id and plan.status not in {'EXECUTING','WAITING_SETTLEMENT','COMPLETED'}:
        plan.status='REQUIREMENTS_DIRTY'
    for auth in await db.scalars(select(Authorization).where(Authorization.user_id==user_id,
            Authorization.status.not_in(['CONSUMED','EXPIRED']))):
        if ((plan_id and auth.command.get('plan_id')==plan_id) or auth.authorization_id in data.get('preview_ids',[]) or
                auth.operation_id==(data.get('active_command') or {}).get('operation_id')):
            if await db.get(Order,auth.operation_id) is None:auth.status='EXPIRED'
    await db.commit()
