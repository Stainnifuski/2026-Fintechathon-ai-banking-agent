"""Owner-scoped presentation ownership; releasing an Agent is not a bank cancel."""
import time
import json
from sqlalchemy import select
from ...models.nova_conversation import NovaConversationORM
from ...models.wealth import WealthConversationORM
from ...models.nova_bill_action import NovaBillActionORM
from ...models.nova_task import NovaSessionORM
from ...models.nova_execution import NovaExecutionORM
from ...models.nova_conversation import NovaConversationMessageORM
from sqlalchemy import func
from uuid import uuid4
from . import task_flow
from ..bank import nova_conversations as chats

GROUPS = {'transfer': ('tasks', 'chat_task_id', '转账', 'transfer'),
          'collection': ('collection_cards', 'collection_id', '收款', 'transfer'),
          'card': ('card_operations', 'entry_id', '卡片操作', 'cards'),
          'subscription': ('subscription_tasks', 'entry_id', '取消订阅', 'subscriptions'),
          'calendar': ('calendar_tasks', 'entry_id', '日程', 'calendar'),
          'payment_request': ('payment_request_tasks', 'entry_id', '收款申请', 'transfer'),
          'shop': ('shop_tasks', 'entry_id', '店铺订单', 'thirdParty')}
TERMINAL = {'SUCCEEDED', 'CANCELLED', 'FAILED', 'COMPLETED', 'SENT', 'FINISHED',
            'SETTLED', 'completed', 'cancelled', 'expired', 'REJECTED', 'EXPIRED'}
DRAFT_FIELDS = {'recipient','amount','phone','card','clearPhone','clearCard','payer','note',
               'executionMode','scheduledAt',
               'selected','account','split','cardId','accountId','singleLimit','dailyLimit','creditLimit','channels',
               'shop','productId','quantity','title','day','start','end','endDay','location','description','allDay'}


def validate_draft(draft):
    if set(draft)-DRAFT_FIELDS or len(json.dumps(draft,ensure_ascii=False))>12000:
        raise chats.ConversationError('表单内容超出限制；验证码和授权不能保存为草稿。',422)


async def save_draft(ctx,key,draft):
    validate_draft(draft)
    item=next((item for item in await inventory(ctx) if item['key']==key),None)
    if item is None:
        raise chats.ConversationError('原任务已结束或已人工接管，未改动表单。',409)
    if item['finished'] or item['released']:
        return {'saved':False,'reason':'task_finished'}
    row,data=await task_flow.load(ctx)
    data.setdefault('input_drafts',{})[key]=draft
    await task_flow.save(ctx,row,data)
    return {'saved':True}


async def continuation_entry(ctx, kind):
    if not ctx or ctx.task_switch_approved or ctx.execution_step or ctx.active_entry_type != kind:
        return None
    session=await ctx.db.get(NovaSessionORM,ctx.session_id,populate_existing=True)
    if not session or session.user_id!=ctx.user_id:
        return None
    group,field,_,_=GROUPS[kind]
    entry=next((entry for entry in session.state.get(group,[]) if entry.get(field)==ctx.active_entry_id
                 and not entry.get('agent_released') and not entry.get('closed')),None)
    if not entry:return None
    _,data=await task_flow.load(ctx)
    draft=data.get('input_drafts',{}).get(kind+':'+ctx.active_entry_id,{})
    prefill=dict(entry.get('prefill',{}))
    if kind=='calendar':
        mapping={'day':'start_date','start':'start_time','endDay':'end_date','end':'end_time',
                 'title':'title','location':'location','description':'description'}
        prefill.update({mapping[key]:value for key,value in draft.items() if key in mapping and value})
    if kind=='shop':
        if draft.get('productId'):prefill['product_id']=draft['productId']
        value=str(draft.get('quantity',''))
        if value.isdecimal() and 1<=int(value)<=20:prefill['quantity']=int(value)
        if draft.get('shop') in {'flower','cake'}:entry={**entry,'shop':draft['shop']}
    return {**entry,'prefill':prefill}


def replace_or_append(entries, entry, ctx, kind):
    if getattr(ctx,'active_entry_type',None)==kind and ctx.active_entry_id and not ctx.task_switch_approved and not ctx.execution_step:
        old=next((item for item in entries if item.get('entry_id')==ctx.active_entry_id
                  and not item.get('closed') and not item.get('agent_released')),None)
        if old:
            entry={**old,**entry,'entry_id':old['entry_id']}
            return [entry if item is old else item for item in entries]
    return [*entries,entry]


async def released(ctx):
    _, data = await task_flow.load(ctx)
    return data.get('released', {})


async def inventory(ctx):
    from .transfer_manager import get_session, ManagerError
    _, data = await task_flow.load(ctx)
    result = []
    try:
        view = await get_session(ctx.db, ctx.user_id, ctx.session_id)
    except ManagerError as error:
        if error.code != 'SESSION_NOT_FOUND':
            raise
        view = {}
    for kind, (group, field, title, destination) in GROUPS.items():
        for item in view.get(group, []):
            operation = item.get('operation') or item.get('task') or {}
            label = title
            if kind == 'card':
                from ..card_operations import ACTION_LABELS
                label = ACTION_LABELS.get(item.get('action'),title)
            elif kind == 'transfer' and item.get('recipient'):
                label += ' · '+item['recipient']
            status = item.get('status') or operation.get('status') or operation.get('outcome')
            finished = status in TERMINAL or item.get('closed') or item.get('event') is not None
            if kind == 'transfer' and operation.get('phase') == 'WAITING_EXECUTION' and operation.get('scheduled_at'):
                finished = True
            if kind == 'subscription':
                finished = finished or (item.get('subscription') or {}).get('status') == 'CANCELLED'
            if kind == 'shop':
                finished = finished or (item.get('order') or {}).get('status') in {'COMPLETED', 'PAYMENT_CLOSED', 'REFUNDED'}
            if kind == 'payment_request':
                requests = item.get('requests') or []
                finished = not requests or all(request.get('status') != 'PENDING' for request in requests)
            key = f'{kind}:{item[field]}'
            actions=operation.get('allowed_actions',[])
            summary=('已完成' if finished else '待验证' if 'verify' in actions else
                     '待核实' if 'reconcile' in actions else '待本人确认' if 'confirm' in actions else
                     '待补充' if item.get('missing_fields') or kind in {'calendar','collection'} else '待核对')
            if finished:
                summary={'CANCELLED':'已取消','FAILED':'失败','REJECTED':'已拒绝','EXPIRED':'已过期',
                         'SENT':'已发送','cancelled':'已取消','expired':'已过期'}.get(status,summary)
                if kind == 'transfer' and operation.get('phase') == 'WAITING_EXECUTION' and operation.get('scheduled_at'):
                    summary='已预约'
                if item.get('deleted'):summary='已删除'
                if kind=='shop':summary={'PAYMENT_CLOSED':'付款已关闭','REFUNDED':'已退款'}.get((item.get('order') or {}).get('status'),summary)
            result.append({'key': key, 'type': kind, 'id': item[field], 'label': label,
                           'destination': destination, 'finished': bool(finished),
                           'released': data.get('released', {}).get(key),
                           'status': operation.get('status'), 'card_id':item.get('card_id'),
                           'summary':summary,'service':item.get('shop'),
                           'request_id': item.get('origin_request_id')})
    rows = (await ctx.db.scalars(select(WealthConversationORM).where(
        WealthConversationORM.user_id == ctx.user_id, WealthConversationORM.session_id.like('NW%')))).all()
    for row in rows:
        value = row.data
        if value.get('nova_session_id') != ctx.session_id or value.get('parent_task_id'):
            continue
        kind = value.get('command', {}).get('kind')
        if kind not in task_flow.TITLES:
            continue
        finished = bool(value.get('closed'))
        if value.get('execution_plan_id') or kind=='assessment':
            from .execution_queue import wealth_state
            finished = finished or await wealth_state(ctx, row.session_id, 'settled') == 'completed'
        key = 'wealth:' + row.session_id
        result.append({'key': key, 'type': 'wealth', 'id': row.session_id,
                       'label': task_flow.TITLES[kind], 'destination': 'wealth',
                       'finished': finished, 'summary':'已取消' if value.get('closed') else '已完成' if finished else '待核对', 'released': data.get('released', {}).get(key),
                       'request_id': value.get('request_id')})
    bills = (await ctx.db.scalars(select(NovaBillActionORM).where(
        NovaBillActionORM.user_id == ctx.user_id, NovaBillActionORM.session_id == ctx.session_id))).all()
    for row in bills:
        key = 'bill:' + row.action_id
        result.append({'key': key, 'type': 'bill', 'id': row.action_id,
                       'label': '账单修改', 'destination': 'insights', 'finished': row.status != 'pending',
                       'released': data.get('released', {}).get(key), 'request_id': row.request_id})
    for item in result:
        item['position'] = data.get('positions', {}).get(item['key'])
    known = {item['key'] for item in result}
    for key, receipt in data.get('released', {}).items():
        if key not in known:
            result.append({**receipt, 'finished': True, 'released': receipt,
                           'position': data.get('positions', {}).get(key)})
    return result


async def position(ctx, key, request_id, existing=False):
    items = await inventory(ctx)
    if not any(item['key'] == key and not item['released'] for item in items):
        raise chats.ConversationError('原任务已退出NOVA，不能继续更新对话卡片。', 409)
    row, data = await task_flow.load(ctx)
    data.setdefault('positions', {})[key] = request_id
    await task_flow.save(ctx, row, data)
    if not existing:
        old = await ctx.db.scalar(select(NovaConversationMessageORM).where(
            NovaConversationMessageORM.conversation_id == ctx.session_id,
            NovaConversationMessageORM.request_id == request_id))
        if not old:
            sequence = await ctx.db.scalar(select(func.coalesce(func.max(NovaConversationMessageORM.sequence), -1)).where(
                NovaConversationMessageORM.conversation_id == ctx.session_id))
            ctx.db.add(NovaConversationMessageORM(message_id=uuid4().hex, conversation_id=ctx.session_id,
                request_id=request_id, role='assistant', sequence=sequence+1, position=0,
                content_ciphertext=chats._encrypt('')))
            await ctx.db.commit()
    return {'position': request_id}


async def event(ctx,request_id,message):
    previous=await ctx.db.scalar(select(NovaConversationMessageORM).where(
        NovaConversationMessageORM.conversation_id==ctx.session_id,
        NovaConversationMessageORM.request_id==request_id))
    if previous:return
    sequence=await ctx.db.scalar(select(func.coalesce(func.max(NovaConversationMessageORM.sequence),-1)).where(
        NovaConversationMessageORM.conversation_id==ctx.session_id))
    ctx.db.add(NovaConversationMessageORM(message_id=uuid4().hex,conversation_id=ctx.session_id,
        request_id=request_id,role='assistant',sequence=sequence+1,position=0,content_ciphertext=chats._encrypt(message)))


async def release(ctx, key, mode, request_id, draft=None):
    draft = draft or {}
    validate_draft(draft)
    # Serialize simultaneous handoffs/switches against the owned conversation.
    owner = await ctx.db.scalar(select(NovaConversationORM).where(
        NovaConversationORM.conversation_id == ctx.session_id,
        NovaConversationORM.user_id == ctx.user_id).with_for_update())
    if owner is None:
        raise chats.ConversationError()
    row, data = await task_flow.load(ctx)
    previous = data.get('released', {}).get(key)
    if previous:
        if previous['request_id'] != request_id or previous['mode'] != mode:
            raise chats.ConversationError('任务已结束或已人工接管，请核对原记录。', 409)
        return previous
    items = await inventory(ctx)
    item = next((item for item in items if item['key'] == key and not item['finished']), None)
    if item is None:
        raise chats.ConversationError('没有可接管或结束的当前任务，请重新核对。', 409)
    from . import execution_queue as queue
    await ctx.db.scalar(select(NovaExecutionORM).where(
        NovaExecutionORM.conversation_id == ctx.session_id).with_for_update())
    q = await queue.load(ctx)
    pending = 0
    if q:
        state = queue.unpack(q)
        if state.get('lease_until', 0) > time.time():
            raise chats.ConversationError('当前操作正在核对，请稍后再接管。', 409)
        if state['status'] not in queue.TERMINAL:
            for step in state['steps']:
                if step['status'] == 'pending':
                    step['status'] = 'stopped'
                    pending += 1
                elif step['status'] in {'waiting', 'running', 'blocked'}:
                    step['status'] = 'stopped'
            state['status'] = 'stopped'
            state['message'] = f'当前操作已退出NOVA，后续尚未开始的{pending}项任务已取消。'
            # Do not commit separately: queue and ownership move together.
            q.payload_ciphertext = queue.encode(state)
            q.version += 1
    if mode == 'cancel':
        # Safe draft cancellation must use original business services. No
        # confirmed/unknown bank operation may be silently cancelled here.
        if item['type'] == 'wealth':
            from .wealth_agent import assert_reversible, discard_prepared
            card = await ctx.db.get(WealthConversationORM, item['id'], populate_existing=True)
            await assert_reversible(ctx.db, ctx.user_id, card.data)
            await discard_prepared(ctx.db,ctx.user_id,card.data.get('execution_plan_id'))
            card.data = {**card.data, 'closed': True}
            card.version += 1
        elif item['type'] == 'transfer':
            session = await ctx.db.get(NovaSessionORM, ctx.session_id)
            task = next(t for t in session.state['tasks'] if t['chat_task_id'] == item['id'])
            if task.get('transfer_task_id'):
                from .transfer_manager import cancel_chat_task
                from ...integrations.bank_api import get_bank_api
                await cancel_chat_task(ctx.db,ctx.bank_api or get_bank_api(),user_id=ctx.user_id,
                    session_id=ctx.session_id,chat_task_id=item['id'],session_version=session.version,request_id=request_id)
            else:
                session.state = {**session.state, 'tasks': [
                    {**t, 'status': 'CANCELLED'} if t['chat_task_id'] == item['id'] else t for t in session.state['tasks']]}
                session.version += 1
        elif item['type'] == 'card':
            from ...models.card import CardOperationORM
            from ...integrations.card_authorization import cancel
            session = await ctx.db.get(NovaSessionORM, ctx.session_id)
            entry = next(e for e in session.state.get('card_operations', []) if e.get('entry_id',e.get('operation_id')) == item['id'])
            if entry.get('operation_id'):
                operation = await ctx.db.get(CardOperationORM, entry['operation_id'], with_for_update=True)
                if operation.status != 'AWAITING_CONFIRMATION':
                    raise chats.ConversationError('已确认或待核实操作不能通过关闭任务取消，请先核对银行结果。',409)
                cancel(ctx.db, operation)
        elif item['type'] == 'bill':
            bill = await ctx.db.get(NovaBillActionORM,item['id'],populate_existing=True)
            bill.status = 'cancelled'
        elif item['type'] == 'collection':
            session = await ctx.db.get(NovaSessionORM,ctx.session_id)
            session.state = {**session.state, 'collection_cards':[
                {**entry,'status':'CANCELLED'} if entry['collection_id']==item['id'] else entry
                for entry in session.state.get('collection_cards',[])]}
            session.version += 1
    await ctx.db.flush()
    if mode=='manual' and item['type']=='wealth':
        wealth=await ctx.db.get(WealthConversationORM,item['id'],populate_existing=True)
        wealth.data={**wealth.data,'agent_released':True}
    message = (f'当前操作已转交人工办理，后续尚未开始的{pending}项任务已取消。如需办理，请重新提出。'
               if mode == 'manual' else f'当前Agent任务已结束，后续尚未开始的{pending}项任务已取消；已有银行结果保留。')
    # 人工接管后由本人在页面原表单继续：卡片上只有脱敏号码，这里交回本人已核实的完整收款信息。
    handoff = {}
    if mode == 'manual' and item['type'] == 'transfer':
        current = await ctx.db.get(NovaSessionORM, ctx.session_id, populate_existing=True)
        task = next((t for t in ((current.state or {}).get('tasks', []) if current else [])
                     if t['chat_task_id'] == item['id']), {})
        slots = task.get('slots', {})
        handoff = {field: slots[field] for field in ('phone', 'card', 'to_account') if slots.get(field)}
    receipt = {**item, 'mode': mode, 'request_id': request_id, 'message': message, 'cancelled_count': pending,
               'draft': draft, 'handoff': handoff}
    session = await ctx.db.get(NovaSessionORM, ctx.session_id, populate_existing=True)
    if session and item['type'] in GROUPS:
        group, field, _, _ = GROUPS[item['type']]
        session.state = {**session.state, group: [
            {**entry, 'agent_released': True} if entry[field] == item['id'] else entry
            for entry in session.state.get(group, [])]}
        session.version += 1
        if item['type']=='transfer' and session.state.get('focus_task_id')==item['id']:
            session.state={**session.state,'focus_task_id':None}
    data.setdefault('released', {})[key] = receipt
    data.pop('active', None)
    data.pop('pending', None)
    await event(ctx,request_id,message)
    await task_flow.save(ctx, row, data)
    return receipt
