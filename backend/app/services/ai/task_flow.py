"""Natural-language continuation and explicitly confirmed task replacement.

The router proposes a change; only a fresh, owner-scoped confirmation may end
an editable draft. Neither a proposal nor a confirmation authorizes a trade.
"""
import json
import logging
import time
import hashlib
from dataclasses import replace
from uuid import uuid4
from sqlalchemy import select, update
from ...models.nova_task_flow import NovaTaskFlowORM
from ...models.nova_conversation import NovaConversationORM
from ...models.wealth import WealthConversationORM
from ...models.nova_task import NovaSessionORM
from ...models.card import CardOperationORM
from ..bank import nova_conversations as chats
from ...domain.time import utc_now
from ...integrations.card_authorization import aware

logger = logging.getLogger(__name__)

TITLES = {'planning':'理财规划','recommend':'目标产品推荐','purchase':'申购','positions':'查看持仓／赎回',
          'redeem':'赎回','assessment':'本人风险评估','clarify':'补充理财需求'}


async def load(ctx):
    owner = await ctx.db.get(NovaConversationORM,ctx.session_id)
    if not owner or owner.user_id != ctx.user_id:raise chats.ConversationError()
    row = await ctx.db.get(NovaTaskFlowORM,ctx.session_id,populate_existing=True)
    if row and row.user_id != ctx.user_id:raise chats.ConversationError()
    return row, json.loads(chats._decrypt(row.payload_ciphertext)) if row else {}


async def save(ctx,row,data):
    value=chats._encrypt(json.dumps(data,ensure_ascii=False))
    if row:
        version=row.version
        result=await ctx.db.execute(update(NovaTaskFlowORM).where(
            NovaTaskFlowORM.conversation_id==ctx.session_id,NovaTaskFlowORM.user_id==ctx.user_id,
            NovaTaskFlowORM.version==version).values(version=version+1,payload_ciphertext=value))
        if result.rowcount!=1:raise chats.ConversationError('当前操作已变化，请重新核对。',409)
    else:ctx.db.add(NovaTaskFlowORM(conversation_id=ctx.session_id,user_id=ctx.user_id,payload_ciphertext=value))
    await ctx.db.commit()


async def active(ctx):
    """Resolve references against owned server records, never a model task id."""
    row,data=await load(ctx)
    saved=data.get('active') or {}
    wealth=ctx.wealth_task_id or (saved.get('id') if not ctx.focus_task_id and saved.get('type')=='wealth' else None)
    transfer=ctx.focus_task_id or (saved.get('id') if not ctx.wealth_task_id and saved.get('type')=='transfer' else None)
    cards=(await ctx.db.scalars(select(WealthConversationORM).where(WealthConversationORM.user_id==ctx.user_id,
        WealthConversationORM.session_id.like('NW%')))).all()
    cards=[c for c in cards if c.data.get('nova_session_id')==ctx.session_id and not c.data.get('closed')
           and c.data.get('command',{}).get('kind') in TITLES]
    transfers=await ctx.db.get(NovaSessionORM,ctx.session_id,populate_existing=True)
    tasks=transfers.state.get('tasks',[]) if transfers and transfers.user_id==ctx.user_id else []
    if tasks:
        from .transfer_manager import get_session
        fresh=await get_session(ctx.db,ctx.user_id,ctx.session_id)
        statuses={t['chat_task_id']:t['status'] for t in fresh['tasks']}
        scheduled_confirmed={t['chat_task_id'] for t in fresh['tasks']
            if t.get('task') and t['task'].get('phase')=='WAITING_EXECUTION'
            and t['task'].get('scheduled_at')}
        tasks=[{**t,'status':statuses.get(t['chat_task_id'],t['status'])} for t in tasks
               if t['chat_task_id'] not in scheduled_confirmed]
    tasks=[t for t in tasks if t.get('status') not in {'SUCCEEDED','CANCELLED','FAILED'}]
    if not wealth and not transfer and not saved:
        if len(cards)+len(tasks)==1:
            wealth=cards[0].session_id if cards else None
            transfer=tasks[0]['chat_task_id'] if tasks else None
        elif len(cards)+len(tasks)>1:
            return {'type':'ambiguous','id':'','label':'多个未结束任务'}
    if wealth:
        c=next((c for c in cards if c.session_id==wealth),None)
        if c and c.data.get('execution_plan_id'):
            from .execution_queue import wealth_state
            if await wealth_state(ctx,wealth,'settled')=='completed':return None
        if c:return {'type':'wealth','id':wealth,'version':c.version,'label':TITLES[c.data['command']['kind']],
                     'digest':hashlib.sha256(json.dumps(c.data,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                     'kind':c.data['command']['kind']}
    if transfer:
        t=next((t for t in tasks if t['chat_task_id']==transfer),None)
        if t:return {'type':'transfer','id':transfer,'version':transfers.version,
                     'label':'转账：'+str(t.get('slots',{}).get('recipient') or t.get('recipient') or '待补充收款人')}
    if saved.get('type')=='card':
        operation=await ctx.db.get(CardOperationORM,saved['id'],populate_existing=True)
        if operation and operation.user_id==ctx.user_id and operation.status in {'AWAITING_CONFIRMATION','AWAITING_MFA','AUTHORIZED'} and aware(operation.expires_at)>utc_now():
            from ..card_operations import ACTION_LABELS
            return {'type':'card','id':operation.operation_id,'status':operation.status,'label':ACTION_LABELS[operation.action]}
    return None


async def focus(ctx):
    if not ctx or not ctx.session_id or ctx.execution_step or ctx.task_switch_approved:return ctx
    current=await active(ctx)
    if not current:return ctx
    if current['type']=='ambiguous':return ctx
    if current['type']=='wealth':
        c=await ctx.db.get(WealthConversationORM,current['id'])
        from .wealth_steps import sequence
        done=c.data.get('view_context',{}).get('completed_stages',[])
        stage=(ctx.wealth_stage if ctx.wealth_stage not in done else None) or next((s for s in sequence(current['kind']) if s not in done),'trade')
        return replace(ctx,wealth_task_id=current['id'],wealth_stage=stage,focus_task_id=None)
    if current['type']=='card':
        from ...schemas.nova import CardContext
        operation=await ctx.db.get(CardOperationORM,current['id'])
        return replace(ctx,wealth_task_id=None,wealth_stage=None,focus_task_id=None,
            page=ctx.page.model_copy(update={'cards':CardContext(selected_card_id=operation.card_id)}))
    return replace(ctx,focus_task_id=current['id'],wealth_task_id=None,wealth_stage=None)


def reply(text,pending=None):
    return {'reply':text,'mode':'model','tool_calls':[], '_execution_grounded':True,'task_switch':pending}


async def public(ctx):
    _,data=await load(ctx)
    p=data.get('pending')
    return {k:p[k] for k in ('token','current_label','target_label')} if p and time.time()-p['created_at']<=600 else None


async def propose(ctx,target_label):
    current=await active(ctx)
    if not current:return None
    if current['type']=='ambiguous':
        return reply('当前对话有多个未结束任务，请先说明要继续或结束哪个任务，再说明目标操作。')
    row,data=await load(ctx)
    data['pending']={'token':uuid4().hex,'current':current,'current_label':current['label'],
        'target_label':chats.redact(target_label)[:100],'message':chats.redact_proof(ctx.original_message),
        'created_at':time.time()}
    await save(ctx,row,data)
    return reply('是否结束当前“'+current['label']+'”，改为“'+data['pending']['target_label']+'”？确认前保留原任务；切换不撤回任何已成交结果。',await public(ctx))


async def decide(ctx):
    """Return (context, terminal response); claim the one-use proposal before I/O.

    A failed/uncertain close never starts the target. Fresh proposal required.
    """
    if not ctx or not ctx.session_id or ctx.execution_step:return ctx,None
    row,data=await load(ctx)
    if data.get('switch_in_progress'):
        return ctx,reply('任务切换正在核对或上次连接已中断。请先核对原卡片结果，暂不重复启动目标操作。')
    pending=data.get('pending')
    if pending and time.time()-pending['created_at']>600:
        data.pop('pending');await save(ctx,row,data);pending=None
    if not ctx.task_switch_token:
        if pending:return ctx,reply('请先确认是否结束“'+pending['current_label']+'”并转向“'+pending['target_label']+'”，或选择继续当前任务。',await public(ctx))
        return ctx,None
    if not pending or pending['token']!=ctx.task_switch_token or time.time()-pending['created_at']>600:
        return ctx,reply('切换确认已过期或已处理，请重新描述需求；未启动新任务。')
    if not ctx.accept_task_switch:
        data.pop('pending');await save(ctx,row,data)
        return ctx,reply('已保留当前“'+pending['current_label']+'”，可以直接输入补充。')
    current=await active(ctx)
    if not current or current!=pending['current']:
        data.pop('pending');await save(ctx,row,data)
        return ctx,reply('当前任务已变化，本次切换未执行。请重新描述需求后核对。')
    # Claim exactly once. Business services may commit; failure must not replay.
    data.pop('pending');data['switch_in_progress']=pending['token'];await save(ctx,row,data)
    try:
        from . import execution_queue as queue
        q=await queue.load(ctx)
        q_version=q.version if q and queue.unpack(q)['status'] not in queue.TERMINAL else None
        if q_version and queue.unpack(q).get('lease_until',0)>time.time():
            raise ValueError('队列正在核对，请稍候')
        if current['type']=='wealth':
            from .wealth_agent import assert_reversible,discard_prepared,lock_profile
            await lock_profile(ctx.db,ctx.user_id)
            card=await ctx.db.get(WealthConversationORM,current['id'],populate_existing=True)
            if card.version!=current['version']:raise ValueError('任务已更新，请重新核对')
            digest=hashlib.sha256(json.dumps(card.data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            if digest!=current['digest']:raise ValueError('卡片输入已更新，请重新核对')
            await assert_reversible(ctx.db,ctx.user_id,card.data)
            await discard_prepared(ctx.db,ctx.user_id,card.data.get('execution_plan_id'))
            card.data={**card.data,'closed':True};card.version+=1
            await ctx.db.commit()
        elif current['type']=='transfer':
            from .transfer_manager import cancel_chat_task
            await cancel_chat_task(ctx.db,ctx.bank_api,user_id=ctx.user_id,session_id=ctx.session_id,
                chat_task_id=current['id'],session_version=current['version'],request_id='switch-'+pending['token'])
        else:
            from ...integrations.card_authorization import cancel
            operation=await ctx.db.get(CardOperationORM,current['id'],with_for_update=True,populate_existing=True)
            if operation.user_id!=ctx.user_id or operation.status!='AWAITING_CONFIRMATION':
                raise ValueError('已确认的卡片操作须在原卡片处理')
            cancel(ctx.db,operation);await ctx.db.commit()
        if q_version:
            await queue.stop(ctx,q_version)
    except Exception as error:
        # 只记录类型与业务码，不记录用户原文或金额。
        logger.warning('NOVA task switch close failed type=%s code=%s current=%s',
                       type(error).__name__, getattr(error,'code',None), current['type'])
        await ctx.db.rollback()
        row,data=await load(ctx);data.pop('switch_in_progress',None);await save(ctx,row,data)
        return ctx,reply('当前任务无法安全结束，未启动目标操作。请在原卡片核对已确认、已到账或待核实的结果；不要重复提交。')
    row,data=await load(ctx);data.pop('active',None);data.pop('switch_in_progress',None)
    await save(ctx,row,data)
    transfers=await ctx.db.get(NovaSessionORM,ctx.session_id,populate_existing=True)
    return replace(ctx,original_message=pending['message'],wealth_task_id=None,wealth_stage=None,
                   session_version=transfers.version if transfers else 0,
                   focus_task_id=None,serial_focus_only=False,task_switch_token=None,accept_task_switch=None,task_switch_approved=True),None


async def guard_tool(ctx,name):
    if not ctx or not ctx.session_id or ctx.execution_step or ctx.task_switch_approved:return None
    domain={'manage_wealth_request':'wealth','manage_transfer_task':'transfer',
            'manage_collection_request':'collection','preview_card_operation':'card'}.get(name)
    if not domain:return None
    current=await active(ctx)
    if current and current['type']=='ambiguous':
        # 转账卡由调用参数指定新建、修改或取消，多个未结束任务不阻止这张卡的操作。
        if name == 'manage_transfer_task':
            return None
        return {'error':'TASK_CONTEXT_AMBIGUOUS','message':'当前有多个未结束任务，请说明要补充哪个任务，不能猜测或创建新操作。'}
    if current and current['type']=='card' and domain=='card' and current['status']!='AWAITING_CONFIRMATION':
        return {'error':'TASK_WRITE_BLOCKED','message':'当前卡片操作已经本人确认，请先在原卡片核对结果；不会通过对话重建或替换待验证操作。'}
    if current and current['type']!=domain:
        return await propose(ctx,ctx.original_message)
    return None


async def observe(ctx,result):
    if not ctx or not ctx.session_id or ctx.execution_step or result.get('task_switch'):return
    found=[]
    for c in result.get('wealth_cards',[]):
        if c.get('request_id')==ctx.request_id and not c.get('closed') and not c.get('parent_task_id') and c.get('command',{}).get('kind') in TITLES:
            found.append({'type':'wealth','id':c['task_id']})
    for t in result.get('tasks',[]):
        continuing=t.get('chat_task_id')==ctx.focus_task_id and any(
            t['name']=='manage_transfer_task' and not t.get('result',{}).get('error') for t in result.get('tool_calls',[]))
        if (t.get('origin_request_id')==ctx.request_id or continuing) and t.get('status') not in {'CANCELLED','SUCCEEDED','FAILED'}:
            found.append({'type':'transfer','id':t['chat_task_id']})
    for tool in result.get('tool_calls',[]):
        if tool['name']=='preview_card_operation' and not tool.get('result',{}).get('error'):
            key=tool['result'].get('data',{}).get('operation_id')
            if key:found.append({'type':'card','id':key})
    if len(found)==1:
        row,data=await load(ctx)
        previous=data.get('active',{})
        if previous.get('type')==found[0]['type']=='card' and previous['id']!=found[0]['id']:
            from ...integrations.card_authorization import cancel
            old=await ctx.db.get(CardOperationORM,previous['id'],with_for_update=True,populate_existing=True)
            if old and old.user_id==ctx.user_id:
                if old.status=='AWAITING_CONFIRMATION' and aware(old.expires_at)>utc_now():cancel(ctx.db,old)
                elif old.status in {'AWAITING_MFA','AUTHORIZED'}:
                    new=await ctx.db.get(CardOperationORM,found[0]['id'],populate_existing=True)
                    cancel(ctx.db,new)
                    result.update(reply='原卡片操作已确认，本次新预览已取消。请保留原操作号核对结果。')
                    for tool in result.get('tool_calls',[]):
                        if tool['name']=='preview_card_operation':
                            tool['result'].update(error='TASK_WRITE_BLOCKED',message=result['reply'])
                    await ctx.db.commit()
                    return
        data['active']=found[0];await save(ctx,row,data)
