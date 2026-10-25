"""Durable serial orchestration. This table is never a bank order or authorization.

Only the active goal can create a draft. Bank-owned terminal facts, never an LLM
completion claim or browser event, unlock the next goal. No reordering API.
"""
import hashlib
import json
import time
from dataclasses import replace
from uuid import uuid4

from sqlalchemy import update

from ...models.nova_execution import NovaExecutionORM
from ...models.wealth import WealthConversationORM, WealthPlanORM
from ...models.card import CardOperationORM
from ...models.nova_conversation import NovaConversationORM
from ..bank import nova_conversations as conversations, nova_reference_context as references

TERMINAL = {'completed', 'stopped'}
FAILED = {'FAILED', 'CANCELLED', 'PARTIALLY_COMPLETED', 'UNKNOWN', 'REJECTED', 'EXPIRED'}
LABELS = {'pending':'待办理','running':'正在核对','waiting':'等待本人操作',
          'completed':'已完成','blocked':'已暂停','stopped':'已停止'}


def unpack(row):
    data=json.loads(conversations._decrypt(row.payload_ciphertext))
    data['_version']=row.version
    return data


def encode(data):
    return conversations._encrypt(json.dumps(data, ensure_ascii=False))


async def load(context):
    conversation = await context.db.get(NovaConversationORM, context.session_id)
    if not conversation or conversation.user_id != context.user_id:
        raise conversations.ConversationError()
    row = await context.db.get(NovaExecutionORM, context.session_id, populate_existing=True)
    if row and row.user_id != context.user_id:
        raise conversations.ConversationError()
    return row


def public(row, data=None):
    if not row:
        return None
    data = data or unpack(row)
    return {'queue_id':data['queue_id'], 'version':row.version, 'status':data['status'],
            'request_id':data['request_id'], 'message':data.get('message',''),
            'steps':[{'goal_id':s['goal_id'], 'objective':conversations.redact(s['objective']),
                      'status':s['status'], 'depends_on':s['depends_on'],
                      'reply':conversations.redact(s.get('reply','')),
                      'review_operation_id':next((r['id'] for r in s['refs'] if r['type']=='card'),None),
                      'request_id':s['request_id']} for s in data['steps']]}


def ordered(plan, message):
    """Stable topological order. Executable text MUST be a real original span."""
    from .nova import GoalPlanArguments
    plan = GoalPlanArguments.model_validate(plan).model_dump()
    goals = plan['goals']
    used=[]
    for goal in goals:
        text = goal.get('source_text')
        if not text or text not in message:
            raise ValueError('无法把所有操作可靠对应到本轮原文，请分句说明每项操作；尚未启动队列。')
        start=-1
        while True:
            start=message.find(text,start+1)
            if start<0:raise ValueError('多个目标引用了重叠原文，请分别明确每项操作；尚未启动队列。')
            end=start+len(text)
            if all(end<=a or start>=b for a,b in used):break
        used.append((start,end))
        goal['_source_start']=start
        goal['objective']=text[:200]
    goals.sort(key=lambda g:g['_source_start'])
    output = []
    while goals:
        ready = next(g for g in goals if set(g['depends_on']) <= {x['goal_id'] for x in output})
        goals.remove(ready)
        output.append(ready)
    return output


async def store(context, row, data):
    # A business service may commit its own transaction. Lease ownership is
    # rechecked by CAS, so that commit cannot release another worker's claim.
    # Async business services intentionally rollback their read transaction.
    # Keep the expected CAS version as plain data: accessing an expired ORM row
    # after that rollback would attempt synchronous implicit IO (MissingGreenlet).
    expected=data['_version']
    data['_version']=expected+1
    result = await context.db.execute(update(NovaExecutionORM).where(
        NovaExecutionORM.conversation_id == context.session_id,
        NovaExecutionORM.user_id == context.user_id, NovaExecutionORM.version == expected
    ).values(payload_ciphertext=encode(data), version=expected+1).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        await context.db.rollback()
        raise conversations.ConversationError('办理队列已更新，请刷新读取原进度。',409)
    await context.db.commit()
    return await load(context)


async def start(context, plan, *, model=None):
    row = await load(context)
    if row:
        data = unpack(row)
        if data['request_id'] == context.request_id:
            if data['original']!=context.original_message:
                raise conversations.ConversationError('请求编号已用于其他办理指令。',409)
            return await advance(context, model=model)
        if data['status'] not in TERMINAL:
            return response(row, [], '当前对话已有未结束的办理队列，请先完成当前操作，或停止后续办理。')
    try:
        steps = ordered(plan, context.original_message)
    except ValueError as error:
        return {'reply':str(error), 'mode':'model', 'tool_calls':[], 'execution_blocked':True}
    queue_id = uuid4().hex
    for step in steps:
        step.update(status='pending', refs=[], request_id='QUEUE-'+hashlib.sha256(
            (queue_id+step['goal_id']).encode()).hexdigest()[:40])
    data = dict(queue_id=queue_id, request_id=context.request_id, status='pending',
                steps=steps, original=context.original_message, page=context.page.model_dump(mode='json'),
                _version=row.version if row else 1,
                message='按以下顺序逐项办理；交易确认与MFA仍由本人在卡片完成。')
    if row:
        row = await store(context, row, data)
    else:
        row = NovaExecutionORM(conversation_id=context.session_id, user_id=context.user_id,
                               version=1, payload_ciphertext=encode(data))
        context.db.add(row)
        await context.db.commit()
    return await advance(context, model=model)


def response(row, results, text=None):
    data=unpack(row)
    order='\n'.join(f"{i+1}. {conversations.redact(s['objective'])}（{LABELS[s['status']]}）" for i,s in enumerate(data['steps']))
    details='\n\n'.join(r['reply'] for r in results if r.get('reply'))
    return {'reply':f"办理顺序：\n{order}\n\n{text or data.get('message','')}"+(f'\n\n{details}' if details else ''),
            'mode':'model', 'tool_calls':[t for r in results for t in r.get('tool_calls',[])],
            'execution_queue':public(row), '_execution_grounded':True,
            'wealth_cards':next((r['wealth_cards'] for r in reversed(results) if 'wealth_cards' in r),[]),
            **next(({k:r[k] for k in ('session_version','tasks','focus_task_id') if k in r}
                    for r in reversed(results) if 'tasks' in r),{})}


def bind(step, result):
    """Register only objects produced by this exact step, never all chat cards."""
    refs=[]
    for card in result.get('wealth_cards',[]):
        if card.get('request_id') == step['request_id']:
            refs.append({'type':'wealth','id':card['task_id']})
    for task in result.get('tasks',[]):
        if task.get('origin_request_id') == step['request_id']:
            refs.append({'type':'transfer','id':task['chat_task_id']})
    for tool in result.get('tool_calls',[]):
        value=tool.get('result',{})
        if tool['name']=='preview_card_operation' and not value.get('error'):
            key=value.get('data',{}).get('operation_id')
            if key:refs.append({'type':'card','id':key})
    step['refs']=list({(r['type'],r['id']):r for r in refs}.values())
    failed=any(t.get('result',{}).get('error') for t in result.get('tool_calls',[]))
    if failed or result.get('execution_blocked') or result.get('grounding',{}).get('status')=='blocked':
        return 'blocked'
    if refs:
        return 'waiting'
    # Absence of a card is never proof that a mutation completed.
    if step['completion']!='result' or not result.get('tool_calls'):
        return 'blocked'
    if any(t['name'] in {'manage_transfer_task','preview_card_operation'} for t in result['tool_calls']):
        return 'blocked'
    return 'completed'


async def wealth_state(context, key, completion):
    row=await context.db.get(WealthConversationORM,key,populate_existing=True)
    if not row or row.user_id!=context.user_id or row.data.get('nova_session_id')!=context.session_id:
        return 'blocked'
    data=row.data
    if data.get('closed'):return 'blocked'
    if data.get('paused') or data.get('pending_backtrack'):return 'waiting'
    kind=data.get('command',{}).get('kind')
    if kind=='clarify':return 'waiting'
    if kind=='assessment':
        from ...integrations.wealth_authorization import latest_assessment,aware
        from ...domain.time import utc_now
        a=await latest_assessment(context.db,context.user_id)
        return 'completed' if a and a.confirmed_at and aware(a.expires_at)>utc_now() else 'waiting'
    view=data.get('view_context') or {}
    if completion=='selection' and kind=='recommend':
        return 'completed' if view.get('selected_products') else 'waiting'
    if completion=='result' and kind in {'assets','positions','returns','detail','compare'}:
        if kind=='returns' and 'read_result' not in data:return 'waiting'
        if kind in {'detail','compare'} and not data.get('command',{}).get('products'):return 'waiting'
        return 'completed'
    if completion=='result' and kind=='recommend':
        return 'completed' if 'match' in view.get('completed_stages',[]) or view.get('visible_products') else 'waiting'
    # Regardless of the model's completion label, planning/purchase/redemption
    # cannot complete with a draft, a selected product, or an empty response.
    plan_id=data.get('execution_plan_id')
    if not plan_id:return 'waiting'
    seen=set()
    while plan_id:
        if plan_id in seen:return 'blocked'
        seen.add(plan_id)
        plan=await context.db.get(WealthPlanORM,plan_id,populate_existing=True)
        if not plan or plan.user_id!=context.user_id:return 'blocked'
        # Refresh-review replaces an expired unsubmitted BUY in separate
        # persistence phases. Its cancelled draft is not a failed settlement;
        # wait for the card's replacement link, never unlock the next goal.
        if plan.status in {'CANCELLED','EXPIRED'} and plan.data.get('items') and all(
            i.get('status')=='AWAITING_CONFIRMATION' for i in plan.data['items']):return 'waiting'
        if plan.status in FAILED:return 'blocked'
        if plan.status!='COMPLETED':return 'waiting'
        if plan.data.get('stage')=='SELL' and (plan.data.get('portfolio_plan') or {}).get('planning'):
            plan_id=(plan.data.get('incremental_continuation') or {}).get('prepared',{}).get('execution_plan_id')
            if not plan_id:return 'waiting'
        else:return 'completed'
    return 'waiting'


async def observe(context, step):
    states=[]
    for ref in step['refs']:
        if ref['type']=='wealth':
            states.append(await wealth_state(context,ref['id'],step['completion']))
        elif ref['type']=='transfer':
            from .transfer_manager import get_session
            view=await get_session(context.db,context.user_id,context.session_id)
            task=next((t for t in view['tasks'] if t['chat_task_id']==ref['id']),None)
            states.append('blocked' if not task or task['status'] in FAILED else
                          'completed' if task['status']=='SUCCEEDED' else 'waiting')
        elif ref['type']=='card':
            row=await context.db.get(CardOperationORM,ref['id'],populate_existing=True)
            states.append('blocked' if not row or row.user_id!=context.user_id or row.status in FAILED else
                          'completed' if row.status=='COMPLETED' else 'waiting')
        else:states.append('blocked')
    return 'blocked' if 'blocked' in states or not states else 'completed' if all(s=='completed' for s in states) else 'waiting'


async def advance(context, *, model=None):
    """Claim one queue; run serial goals until first interaction, failure or budget.

    Stable step request IDs support recovery through existing business receipts.
    A live lease never overlaps another request. Expired execution is stopped
    for reconciliation rather than replaying potentially non-idempotent tools.
    """
    row=await load(context)
    if not row:return {'reply':'当前没有多操作队列。','mode':'model','tool_calls':[],'execution_queue':None}
    from .task_flow import load as load_flow
    _,flow=await load_flow(context)
    if flow.get('pending') or flow.get('switch_in_progress'):
        return response(row,[],'正在等待任务切换核对，暂不启动下一项操作。')
    data=unpack(row)
    if data['status'] in TERMINAL or data['status']=='blocked':return response(row,[])
    if data.get('lease_until',0)>time.time():return response(row,[],'正在核对当前步骤，请稍候。')
    if data['status']=='running':
        data.update(status='blocked',message='上次办理连接中断，已暂停后续步骤。请核对原任务，避免重复提交。')
        row=await store(context,row,data)
        return response(row,[])
    active=next((s for s in data['steps'] if s['status']!='completed'),None)
    if active and active['status']=='waiting' and await observe(context,active)=='waiting':
        return response(row,[])
    data.update(lease_until=time.time()+110, status='running')
    row=await store(context,row,data)
    results=[]
    try:
        deadline=time.monotonic()+65
        for step in data['steps']:
            if step['status']=='completed':continue
            if step['status']=='waiting':
                step['status']=await observe(context,step)
                if step['status']!='completed':break
            if step['status'] in {'blocked','stopped'}:break
            if step['status']=='pending':
                step['status']='running'
                # Checkpoint before ANY business service commits.
                row=await store(context,row,data)
                from . import nova
                from ...schemas.nova import PageContext
                refs=await references.load(context.db,context.user_id,context.session_id)
                previous=[t for s in data['steps'] for t in s.get('trace',[])]
                from ...models.nova_task import NovaSessionORM
                session=await context.db.get(NovaSessionORM,context.session_id,populate_existing=True)
                selection=next((r['id'] for s in data['steps'] if s['goal_id'] in step['depends_on']
                    and s['completion']=='selection' for r in s['refs'] if r['type']=='wealth'),None)
                if not selection:
                    for previous_step in reversed(data['steps']):
                        if previous_step['goal_id'] not in step['depends_on']:continue
                        for ref in previous_step['refs']:
                            if ref['type']!='wealth':continue
                            card=await context.db.get(WealthConversationORM,ref['id'],populate_existing=True)
                            if card and card.user_id==context.user_id and card.data.get('command',{}).get('kind')=='positions':
                                selection=ref['id'];break
                        if selection:break
                ctx=replace(context, execution_step=True, original_message=step['source_text'],
                    request_id=step['request_id'],page=PageContext.model_validate(data['page']),
                    session_version=session.version if session else 0,
                    focus_task_id=None,wealth_task_id=selection,wealth_stage=None,
                    reference_results=refs,current_tool_results=previous)
                result=await nova.chat(conversations.redact(step['source_text']),[],model=model,context=ctx)
                results.append(result)
                step['reply']=result.get('reply','')
                step['status']=bind(step,result)
                if step['status']=='blocked' and ctx.wealth_task_id and not any(t.get('result',{}).get('error') for t in result.get('tool_calls',[])):
                    reused=next((c for c in result.get('wealth_cards',[]) if c['task_id']==ctx.wealth_task_id),None)
                    if reused and reused.get('command',{}).get('kind') in {'positions','redeem'}:
                        step['refs']=[{'type':'wealth','id':ctx.wealth_task_id}]
                        step['status']='waiting'
                step['trace']=result.get('tool_calls',[])
                await references.save(context.db,context.user_id,context.session_id,step['trace'])
                if step['status']=='waiting':step['status']=await observe(context,step)
                if step['status']!='completed':break
                if time.monotonic()>deadline:break
        states=[s['status'] for s in data['steps']]
        data['status']='blocked' if 'blocked' in states else 'completed' if all(s=='completed' for s in states) else 'waiting'
        data['message']=('所有步骤已按序完成。' if data['status']=='completed' else
            '当前步骤未完成或状态异常，已暂停后续办理；已成交操作保留，资金以银行账本为准。' if data['status']=='blocked' else
            '请完成当前卡片；系统核对完成后再办理下一项。本人确认与MFA不能由聊天代替。')
    except Exception:
        await context.db.rollback()
        row=await load(context)
        data.update(status='blocked',message='当前步骤未完成，已暂停后续办理。请核对原任务；不会重放已受理操作。')
        for step in data['steps']:
            if step['status']=='running':step['status']='blocked'
        import logging
        logging.getLogger(__name__).exception('NOVA serial execution stopped')
    data.pop('lease_until',None)
    row=await store(context,row,data)
    return response(row,results)


async def stop(context, version):
    row=await load(context)
    if not row or row.version!=version:raise conversations.ConversationError('队列已更新，请刷新。',409)
    data=unpack(row)
    if data.get('lease_until',0)>time.time():raise conversations.ConversationError('正在核对当前操作，请稍候再停止。',409)
    data.update(status='stopped',message='已停止后续办理；当前草稿请在原卡片处理，已成交操作不会撤回。')
    for step in data['steps']:
        if step['status']=='pending':step['status']='stopped'
    return public(await store(context,row,data))


async def focus(context):
    if not context or not context.session_id or context.execution_step:return context
    if not await context.db.get(NovaExecutionORM,context.session_id):return context
    row=await load(context)
    if not row:return context
    data=unpack(row)
    if data['status'] in TERMINAL:return context
    step=next((s for s in data['steps'] if s['status']!='completed'),None)
    if not step:return context
    wealth=next((r['id'] for r in step['refs'] if r['type']=='wealth'),None)
    transfer=next((r['id'] for r in step['refs'] if r['type']=='transfer'),None)
    return replace(context,serial_focus_only=True,wealth_task_id=context.wealth_task_id or wealth,
                   focus_task_id=context.focus_task_id or transfer)


async def guard_tool(context, name):
    if not context or not context.session_id or context.execution_step:return None
    row=await context.db.get(NovaExecutionORM,context.session_id,populate_existing=True)
    if not row:return None
    data=unpack(row)
    if data['status'] in TERMINAL:return None
    if name not in {'manage_wealth_request','manage_transfer_task','manage_collection_request','preview_card_operation'}:return None
    step=next((s for s in data['steps'] if s['status']!='completed'),None)
    allowed=step and step['status']=='waiting' and any(
        r['type']=='wealth' and name=='manage_wealth_request' and r['id']==context.wealth_task_id or
        r['type']=='transfer' and name=='manage_transfer_task' and r['id']==context.focus_task_id
        for r in step['refs'])
    if not allowed:return {'error':'SERIAL_STEP_PENDING','message':'当前队列尚未完成此步骤，请先办理当前卡片或停止后续办理；不会并行创建新操作。'}
    return None
