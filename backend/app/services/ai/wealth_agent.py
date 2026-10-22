"""NOVA intent bridge. No consent, verification or bank execution capability."""
from copy import deepcopy
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update

from ...models.wealth import WealthConversationORM, WealthPlanORM
from ...models.wealth_research import WealthChatRequestORM
from ...models.nova_conversation import NovaConversationORM
from ...integrations.wealth_bank_api import HttpWealthBankApi
from ...integrations.wealth_authorization import WealthPolicyError
from ...integrations.wealth_authorization import lock_profile
from ...domain.time import utc_now
from .skill_registry import load_skill
from .llm import llm
from ..bank.nova_conversations import redact
from . import wealth_steps


CATEGORY_NAMES = {'债券基金':'bond_fund','混合基金':'mixed_fund','股票基金':'equity_fund',
                  '权益基金':'equity_fund','银行理财':'bank_wealth','快赎理财':'quick_redeem'}


def category_intent(intent):
    # Exact category labels are not names of individual products. Preserve real
    # names/codes and do not infer arbitrary themes or choose any concrete fund.
    if intent.kind == 'recommend' and len(intent.product_queries) == 1:
        category = CATEGORY_NAMES.get(intent.product_queries[0])
        if category and intent.category in (None, category):
            return intent.model_copy(update={'category':category,'product_queries':[]})
    return intent


def goal_key(command):
    kind=command.get('kind')
    if kind not in {'recommend','planning','purchase','redeem'}:return None
    products=[] if kind=='planning' or kind=='recommend' and command.get('category') else command.get('products',[])
    return (kind,command.get('category'),tuple(sorted((p['product_id'],str(p['product_version'])) for p in products)))


async def unfinished_match(db,user,context_id,command):
    key=goal_key(command)
    if key is None:return None
    rows=await db.scalars(select(WealthConversationORM).where(WealthConversationORM.user_id==user,
        WealthConversationORM.session_id.like('NW%')).order_by(WealthConversationORM.session_id))
    for row in rows:
        data=row.data
        if data.get('closed') or data.get('context_id')!=context_id or goal_key(data.get('command',{}))!=key:continue
        conversation=await db.get(NovaConversationORM,data.get('nova_session_id')) if data.get('nova_session_id') else None
        if not conversation or conversation.user_id!=user:continue
        plan_id=data.get('execution_plan_id')
        if plan_id:
            plan=await db.get(WealthPlanORM,plan_id)
            if plan and plan.user_id==user and plan.status=='COMPLETED':
                if plan.data.get('stage')=='SELL' and (plan.data.get('portfolio_plan') or {}).get('planning'):
                    child=plan.data.get('incremental_continuation',{}).get('prepared',{}).get('execution_plan_id')
                    child_plan=await db.get(WealthPlanORM,child) if child else None
                    if child_plan and child_plan.user_id==user and child_plan.status=='COMPLETED':continue
                else:continue
        from ..bank.nova_conversations import _summary
        return row, _summary(conversation)['title']
    return None


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class WealthIntent(EmptyArguments):
    kind: Literal['assets','positions','returns','detail','compare','assessment',
                  'recommend','planning','purchase','redeem','resume','abandon','clarify']
    task_id: str | None = Field(default=None, max_length=64)
    product_queries: list[str] = Field(default_factory=list, max_length=4)
    candidate_indices: list[int] = Field(default_factory=list, max_length=4,
        description='用户所指卡片的候选序号，从1开始：第一个填[1]，第二个填[2]。不是从0开始的数组下标。')
    category: Literal['quick_redeem','bank_wealth','bond_fund','mixed_fund','equity_fund'] | None = None
    cash_field: Literal['reserve','invest','total'] | None = None
    cash_yuan: str | None = Field(default=None, max_length=24, pattern=r'^\d+(\.\d{1,2})?$')
    amounts_yuan: list[str | None] = Field(default_factory=list, max_length=4)
    redemption_input: Literal['units','market_value','holding_ratio'] = 'units'
    redemption_value: str | None = Field(default=None, max_length=24, pattern=r'^\d+(\.\d{1,6})?$')
    redemption_mode: Literal['standard','quick'] = 'standard'
    period: Literal['day','month','year','cumulative'] = 'day'
    anchor_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    question: str = Field(default='', max_length=400)
    advance: Literal['none','continue','prepare','confirm','next_batch'] = 'none'
    stage: str | None = Field(default=None,max_length=64,pattern=r'^[a-z_]+$')
    selection_operation: Literal['keep','add','replace','remove'] = 'keep'
    edit_kind: Literal['correction','backtrack'] = 'correction'
    target_stage: Literal['match','cash_management','stable','growth','candidates','selected','budget','amounts','purchase_review','allocation','details','redemption_input','trade'] | None = None


class IntentBatch(EmptyArguments):
    intents: list[WealthIntent] = Field(min_length=1, max_length=4)


def reject(message, code='WEALTH_AGENT_CONFLICT', status=409):
    raise WealthPolicyError(message, code, status)


async def owner(db, user, session):
    row = await db.get(NovaConversationORM, session)
    if row is None or row.user_id != user:
        reject('理财对话不存在', 'NOT_FOUND', 404)


async def cards(db, user, session):
    await owner(db, user, session)
    rows = (await db.scalars(select(WealthConversationORM).where(
        WealthConversationORM.user_id == user,
        WealthConversationORM.session_id.like('NW%')))).all()
    return [view(row) for row in rows if row.data.get('nova_session_id') == session]


async def require_card(db, user, task_id):
    row = await db.get(WealthConversationORM, task_id, populate_existing=True)
    if not row or row.user_id != user or not row.data.get('nova_session_id'):
        reject('理财任务不存在', 'NOT_FOUND', 404)
    await owner(db, user, row.data['nova_session_id'])
    if row.data.get('closed'):
        reject('草稿已放弃，请重新描述需求')
    if row.data.get('paused'):
        reject('理财任务已暂停，请先恢复并重新核对', 'TASK_WRITE_BLOCKED')
    return row


async def link_plan(db, user, task_id, plan_id):
    if not task_id or not plan_id:
        return
    row = await require_card(db, user, task_id)
    plan = await db.get(WealthPlanORM, plan_id)
    if not plan or plan.user_id != user:
        reject('交易方案不属于本人', 'NOT_FOUND', 404)
    if plan.data.get('context_id') != row.data.get('context_id'):
        reject('理财卡片与交易资金范围不一致', 'STALE_CONTEXT')
    if row.data.get('execution_plan_id') == plan_id:
        return
    row.data = {**row.data, 'execution_plan_id':plan_id}
    row.version += 1
    await db.commit()


def view(row):
    return dict(task_id=row.session_id, version=row.version, **deepcopy(row.data))


async def save_state(db, user, session, task_id, version, state):
    await lock_profile(db,user)
    await owner(db, user, session)
    row = await db.get(WealthConversationORM, task_id, populate_existing=True)
    if not row or row.user_id != user or row.data.get('nova_session_id') != session:
        reject('理财任务不存在', 'NOT_FOUND', 404)
    if state.execution_plan_id and state.execution_plan_id == row.data.get('execution_plan_id') and not state.closed and state.paused is None:
        return await cards(db, user, session)
    if row.version != version:
        reject('理财任务已更新，请重新读取', 'STALE_WEALTH_CARD')
    data = deepcopy(row.data)
    if data.get('closed'):
        reject('草稿已放弃，请重新描述需求')
    if state.backtrack_stage:
        await propose_backtrack(db,user,row,state.backtrack_stage)
        await db.commit()
        return await cards(db,user,session)
    if state.backtrack_token:
        pending=data.get('pending_backtrack')
        if not pending or pending['token']!=state.backtrack_token:
            reject('回退请求已变化，请重新核对','STALE_WEALTH_CARD')
        if state.accept_backtrack:
            await assert_reversible(db,user,data)
            await discard_prepared(db,user,data.get('execution_plan_id'))
            row.data=wealth_steps.reset_data(data,pending['stage'],pending.get('command'))
        else:
            data.pop('pending_backtrack',None)
            row.data=data
        row.version+=1
        await db.commit()
        return await cards(db,user,session)
    if state.reset_draft:
        await propose_backtrack(db,user,row,'match' if data['command']['kind']!='redeem' else 'redemption_input')
        await db.commit()
        return await cards(db,user,session)
    # Only references and non-authorizing display inputs. Never accept proof/consent.
    if state.execution_plan_id:
        plan = await db.get(WealthPlanORM, state.execution_plan_id)
        if not plan or plan.user_id != user:
            reject('交易方案不属于本人', 'NOT_FOUND', 404)
        data['execution_plan_id'] = plan.plan_id
    if state.closed:
        if data.get('execution_plan_id'):
            reject('已有交易方案，请保留原号查询；可暂停后续步骤')
        data['closed'] = True
    if state.paused is not None:
        from uuid import uuid4
        from ...models.wealth import WealthTaskORM
        from ..orchestration import wealth_task
        plan_id=data.get('execution_plan_id')
        if plan_id:
            tasks=(await db.scalars(select(WealthTaskORM).where(WealthTaskORM.user_id==user,
                WealthTaskORM.status.not_in(wealth_task.TERMINAL)))).all()
            for task in tasks:
                if task.data.get('plan_id')==plan_id or task.session_id==plan_id:
                    await wealth_task.control(db,user,task.task_id,'pause' if state.paused else 'resume',
                        {'request_id':uuid4().hex,'expected_version':task.version,'reason':'本人在NOVA控制理财后续步骤'})
        data['paused']=state.paused
    result = await db.execute(update(WealthConversationORM).where(
        WealthConversationORM.session_id == task_id,
        WealthConversationORM.version == version).values(data=data, version=version+1))
    if result.rowcount != 1:
        reject('理财任务已更新，请重新读取', 'STALE_WEALTH_CARD')
    await db.commit()
    return await cards(db, user, session)


async def assert_reversible(db,user,data):
    plan_ids=[*data.get('committed_stage_ids',[]),data.get('execution_plan_id')]
    # Older cards may not carry the new barrier list. The original SELL keeps
    # the idempotent continuation response, which also proves the BUY ancestry.
    if data.get('execution_plan_id'):
        parents=await db.scalars(select(WealthPlanORM).where(WealthPlanORM.user_id==user,
            WealthPlanORM.data['incremental_continuation']['prepared']['execution_plan_id'].as_string()==data['execution_plan_id']))
        plan_ids.extend(p.plan_id for p in parents)
    for plan_id in plan_ids:
        if not plan_id:continue
        plan=await db.get(WealthPlanORM,plan_id,populate_existing=True)
        if not plan or plan.user_id!=user:reject('交易方案不存在','NOT_FOUND',404)
        if plan.status!='AWAITING_CONFIRMATION' or any(i['status']!='AWAITING_CONFIRMATION' for i in plan.data.get('items',[])):
            reject('已有成交、到账或待核实结果，不能回退或改写此前步骤。请保留原号核对；需要新的调整请新建任务。','RECONCILIATION_REQUIRED')


async def propose_backtrack(db,user,row,stage,command=None):
    from uuid import uuid4
    await assert_reversible(db,user,row.data)
    kind=row.data['command']['kind']
    if stage=='candidates' and kind in {'recommend','purchase'} and not row.data['command'].get('category'):
        stage='match'
    if stage=='trade':stage='allocation' if kind=='planning' else 'redemption_input' if kind=='redeem' else 'purchase_review'
    if stage=='details' and kind=='planning':stage='allocation'
    try:invalid=wealth_steps.affected(row.data['command']['kind'],stage)
    except ValueError:reject('当前任务没有这个步骤')
    row.data={**row.data,'pending_backtrack':{'token':uuid4().hex,'stage':stage,
        'label':wealth_steps.LABELS[stage],'invalidated':invalid,'command':command}}
    row.version+=1


async def manage(arguments, context):
    if not context.session_id or not context.request_id or not context.original_message:
        return {'error':'WEALTH_CONTEXT_REQUIRED', 'message':'缺少理财会话上下文'}
    db, user, session = context.db, context.user_id, context.session_id
    current = await cards(db, user, session)
    request_key='NWREQ'+hashlib.sha256(f'{user}:{session}:{context.request_id}'.encode()).hexdigest()[:40]
    request_digest=hashlib.sha256(context.original_message.encode()).hexdigest()
    replay=await db.get(WealthChatRequestORM,request_key)
    if replay:
        if replay.digest!=request_digest:
            reject('请求编号不能用于不同的理财需求','IDEMPOTENCY_CONFLICT')
        return {'reply':replay.response.get('reply') or '已恢复本次理财任务，请核对卡片。', 'wealth_cards':current}
    result = await llm.generate_structured(messages=[
        {'role':'system','content':load_skill('wealth')},
        {'role':'user','content':json.dumps({
            'message':redact(context.original_message),
            'focused_card':{'task_id':context.wealth_task_id,'stage':context.wealth_stage},
            'history':(context.conversation_history or [])[-12:],
            'tasks':[{k:c.get(k) for k in ('task_id','version','command','view_context','closed','execution_plan_id')} for c in current[-20:]],
        },ensure_ascii=False)}], schema=IntentBatch, max_tokens=2000)
    result.data.intents=[category_intent(i) for i in result.data.intents]
    bank = HttpWealthBankApi()
    facts = await bank.assets(user)
    catalogue = await bank.call('POST','/catalog',{'subject_user_id':user,'facts_only':True,'policy_references':True}) if any(
        i.product_queries or i.candidate_indices for i in result.data.intents) else None
    # Serialize only the short persistence phase, after all model/network work.
    await db.rollback()
    await lock_profile(db,user)
    await owner(db,user,session)
    replay=await db.get(WealthChatRequestORM,request_key,populate_existing=True)
    if replay:
        if replay.digest!=request_digest:
            reject('请求编号不能用于不同的理财需求','IDEMPOTENCY_CONFLICT')
        await db.rollback()
        return {'reply':'已恢复本次理财任务，请核对卡片。','wealth_cards':await cards(db,user,session)}
    replies=[]
    for index, intent in enumerate(result.data.intents):
        if intent.kind=='assets':
            cash=sum(a['available_fen'] for a in facts['cash'] if a['account_type']!='credit')
            summary=facts.get('asset_summary') or {}
            def money(v):return '尚未核实' if v is None else f'¥{v/100:,.2f}'
            replies.append(f"当前可用现金 **{money(cash)}**。\n\n持仓市值：{money(summary.get('positions_market_value_fen'))}；理财总资产：{money(summary.get('total_assets_fen'))}。信用额度不计入可投资现金。")
            continue
        if intent.kind=='assessment':
            from ...integrations.wealth_authorization import latest_assessment,aware
            assessment=await latest_assessment(db,user)
            if assessment and assessment.confirmed_at and aware(assessment.expires_at)>utc_now():
                replies.append(f"你的本人风险评估为 **R{assessment.result['capacity_level']}，已确认且在有效期内**，无需重新填写。有效期至 {aware(assessment.expires_at).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).date()}。")
                continue
        if not intent.task_id and context.wealth_task_id and (intent.advance!='none' or intent.candidate_indices):
            intent=intent.model_copy(update={'task_id':context.wealth_task_id})
        if intent.edit_kind=='backtrack':
            selected_task=intent.task_id or context.wealth_task_id
            if not selected_task:
                replies.append('请先选择要回退的卡片。');continue
            row=await require_card(db,user,selected_task)
            if row.data['nova_session_id']!=session:reject('补充对象不属于此对话','NOT_FOUND',404)
            stage=intent.target_stage or context.wealth_stage
            if not stage:
                replies.append('请说明要回到哪一步。');continue
            await propose_backtrack(db,user,row,stage)
            replies.append('请在卡片中核对回退范围并手动确认；确认前不会修改原输入。')
            continue
        if intent.advance!='none':
            if intent.advance=='confirm' or context.wealth_stage=='trade':
                replies.append('交易确认需要本人手动勾选并点击卡片确认按钮；RED操作还需要独立MFA。聊天不会代为确认。')
                continue
            selected_task=intent.task_id or context.wealth_task_id
            if not selected_task:
                replies.append('请先在要继续的卡片上选择“用对话补充”。')
                continue
            row=await require_card(db,user,selected_task)
            if row.data.get('pending_backtrack'):
                replies.append('请先在卡片确认或取消待处理的回退。');continue
            if row.data.get('nova_session_id')!=session:reject('理财补充对象不属于此对话','NOT_FOUND',404)
            # A model cannot invent authority from a question/negation. Only a
            # direct user instruction advances UI; consent remains a manual checkbox.
            if not re.fullmatch(r'\s*(?:请)?(?:继续|下一步|继续核对|核对所选产品|生成预览|准备预览|确认|确认当前明细|确认这份方案|确认申购方案|确认赎回方案|换一批)[。！!\s]*',context.original_message):
                replies.append('请核对当前卡片；需要继续时可明确输入“下一步”，或使用卡片按钮。')
                continue
            row.data={**row.data,'instruction':{'id':context.request_id,'action':intent.advance,'stage':context.wealth_stage or intent.stage}}
            row.version+=1
            replies.append('已交给当前卡片核对。交易同意须本人手动勾选，验证码仍在验证框输入。')
            continue
        command = intent.model_dump(mode='json')
        if intent.task_id:
            prior = next((c for c in current if c['task_id'] == intent.task_id), None)
            if not prior:
                reject('找不到所指的理财任务，请明确选择')
            if intent.kind == 'resume':
                continue
            row = await require_card(db, user, intent.task_id)
            if row.version != prior['version']:
                reject('理财任务在理解需求期间发生变化，请重新读取', 'STALE_WEALTH_CARD')
            if intent.kind == 'abandon':
                if prior.get('execution_plan_id'):reject('已有交易方案，请在卡片核对后处理草稿')
                row.data = {**row.data, 'closed':True}
                row.version += 1
                continue
        elif intent.kind in ('resume','abandon'):
            # Never guess which task to abandon.
            command.update(kind='clarify', question='请明确要恢复或放弃哪一个理财任务。')
        refs = []
        if intent.product_queries or intent.candidate_indices:
            queries = list(intent.product_queries)
            prior = next((c for c in current if c['task_id'] == intent.task_id), None)
            selected_refs=(prior or {}).get('view_context',{}).get('selected_products',[])
            previously_named=(prior or {}).get('command',{}).get('products',[])
            grounded_ids={p['product_id'] for p in [*selected_refs,*previously_named]}
            # The submodel may understand a goal, but cannot silently pick a
            # concrete fund for a category-only request.
            if any(q not in context.original_message and not any(
                p['product_id'] in grounded_ids and q in (p['product_id'],p['name']) for p in catalogue)
                for q in queries):
                queries=[]
                command.update(kind='clarify',question='请提供产品名称／代码，或在候选卡中自行勾选产品。')
            # Ordinals resolve only against a saved, explicitly referenced card.
            if intent.candidate_indices:
                prior = next((c for c in current if c['task_id'] == intent.task_id), None)
                visible = (prior or {}).get('view_context',{}).get('visible_products',[])
                if context.wealth_stage in {'cash_management','stable','growth'}:
                    visible=[p for p in visible if p.get('bucket')==context.wealth_stage]
                if not visible or any(i < 1 or i > len(visible) for i in intent.candidate_indices):
                    command.update(kind='clarify',question='候选序号范围不明确，请在对应候选卡勾选产品。')
                else:
                    queries.extend(visible[i-1]['product_id'] for i in intent.candidate_indices)
            for query in queries:
                matches = [p for p in catalogue if query in (p['product_id'],p['name'])]
                if len(matches) != 1:
                    command.update(kind='clarify',question=f'“{query}”未唯一匹配，请提供准确名称或代码，或在候选卡中选择。')
                    break
                p = matches[0]
                refs.append({'product_id':p['product_id'],'product_version':p['version'],'name':p['name']})
        prior=next((c for c in current if c['task_id']==intent.task_id),None)
        if prior and intent.kind in ('recommend','purchase','planning'):
            saved=prior.get('view_context') or {}
            previous=saved['selected_products'] if 'selected_products' in saved else prior.get('command',{}).get('products',[])
            # A singular amount on the explicitly focused amounts card has one
            # unambiguous recipient. Never spread it across several products.
            if (not refs and not intent.product_queries and not intent.candidate_indices
                    and context.wealth_stage=='amounts' and len(previous)==1
                    and len(intent.amounts_yuan)==1 and intent.amounts_yuan[0] is not None):
                refs=list(previous)
            explicit_amounts={p['product_id']:intent.amounts_yuan[i] for i,p in enumerate(refs) if i<len(intent.amounts_yuan) and intent.amounts_yuan[i] is not None}
            if intent.selection_operation=='remove':refs=[p for p in previous if p['product_id'] not in {r['product_id'] for r in refs}]
            elif intent.selection_operation in ('keep','add'):
                refs=list({p['product_id']:p for p in [*previous,*refs]}.values())
            amounts=saved.get('amounts_yuan') or {}
            command['amounts_yuan']=[explicit_amounts.get(p['product_id'],amounts.get(p['product_id'])) for p in refs]
            if command['cash_yuan'] is None and saved.get('cash_yuan'):
                command['cash_yuan']=saved['cash_yuan']
                command['cash_field']='invest' if saved.get('cash_field')=='investment' else saved.get('cash_field')
            if command['category'] is None:command['category']=prior.get('command',{}).get('category')
        if intent.kind in ('recommend','purchase') and not refs and not command.get('category'):
            command.update(kind='clarify',question='希望查找哪类产品，或哪只产品的名称／代码？')
        command['products'] = refs
        if not intent.task_id:
            duplicate=await unfinished_match(db,user,facts['context_id'],command)
            if duplicate:
                existing,title=duplicate
                # Escape Markdown labels; the target is a server-owned ID, and
                # only this user's conversations were eligible above.
                title=re.sub(r'[\\\[\]()]',' ',title)
                replies.append(f'已有相同理财任务尚未结束，位于对话“{title}”。请继续原任务；如需重新办理，请先在原卡片放弃未提交草稿。\n\n[打开对话：{title}](#nova-conversation={existing.data["nova_session_id"]})')
                continue
        if prior and (prior.get('pending_backtrack')):
            replies.append('请先在卡片确认或取消待处理的回退。');continue
        edit_stage=intent.target_stage or context.wealth_stage
        if prior and (prior.get('execution_plan_id') or edit_stage in (prior.get('view_context') or {}).get('completed_stages',[])):
            row=await require_card(db,user,prior['task_id'])
            await propose_backtrack(db,user,row,edit_stage or ('match' if intent.kind!='redeem' else 'redemption_input'),command)
            replies.append('此修正影响已完成步骤，请在卡片确认回退；相关后续输入和旧确认将失效。')
            continue
        # Complete explicit, unambiguous parameters may advance read/precheck
        # steps. This flag never authorizes confirm, verify or execute.
        command['auto_prepare']=bool(
            (refs and intent.amounts_yuan and len(command['amounts_yuan'])==len(refs)
                and all(v is not None and re.fullmatch(r'\d+(?:\.\d{1,2})?',v) and float(v)>0 for v in command['amounts_yuan']))
            or (intent.kind=='planning' and refs and intent.cash_yuan is not None)
            or (intent.kind=='redeem' and refs and intent.redemption_value is not None))
        task_id = intent.task_id or 'NW'+hashlib.sha256(f'{user}:{session}:{context.request_id}:{index}'.encode()).hexdigest()[:40]
        data = dict(nova_session_id=session,request_id=context.request_id,context_id=facts['context_id'],command=command,closed=False)
        if prior and prior.get('view_context'):
            data['view_context']=deepcopy(prior['view_context'])
            if intent.kind in ('planning','recommend','purchase'):
                data['view_context']['selected_products']=refs
                data['view_context']['amounts_yuan']={p['product_id']:command['amounts_yuan'][i] or '' for i,p in enumerate(refs)}
        if refs and intent.kind in ('recommend','purchase'):
            view_context=data.setdefault('view_context',{})
            view_context['completed_stages']=list(dict.fromkeys([*view_context.get('completed_stages',[]),'match','candidates','selected']))
        if prior and prior.get('committed_stage_ids'):data['committed_stage_ids']=prior['committed_stage_ids']
        row = await db.get(WealthConversationORM, task_id,populate_existing=True)
        if row:
            row.data = data
            row.version += 1
        else:
            db.add(WealthConversationORM(session_id=task_id,user_id=user,version=1,data=data))
    db.add(WealthChatRequestORM(request_key=request_key,user_id=user,digest=request_digest,status='COMPLETED',
        response={'session_id':session,'reply':'\n\n'.join(replies)},started_at=utc_now()))
    await db.commit()
    return {'reply':'\n\n'.join(replies) or '已更新理财任务，请核对下方卡片。',
            'wealth_cards':await cards(db,user,session)}


def wealth_tools():
    return {'manage_wealth_request':{
        'schema':{'type':'function','function':{'name':'manage_wealth_request',
            'description':'处理理财查询、目标推荐、规划、申购和赎回以及修改/恢复草稿。由专用子模型读取原文与当前任务。只生成可信卡片，不代填问卷、不确认、不验证、不执行交易。传空对象。',
            'parameters':EmptyArguments.model_json_schema()}},
        'arguments':EmptyArguments,'execute':manage,'requires_identity':True}}


async def discard_prepared(db,user,plan_id):
    """Invalidate only entirely unsubmitted plans. Never cancel bank effects."""
    if not plan_id:return
    from uuid import uuid4
    from ...models.wealth import WealthAuthorizationORM,WealthTaskORM
    from ..orchestration import wealth_task
    await lock_profile(db,user)
    plan=await db.get(WealthPlanORM,plan_id,populate_existing=True)
    if not plan or plan.user_id!=user:
        reject('交易方案不存在','NOT_FOUND',404)
    if plan.status!='AWAITING_CONFIRMATION' or any(i['status']!='AWAITING_CONFIRMATION' for i in plan.data.get('items',[])):
        reject('已提交或未知结果须按原号核对，不能修改后重下','RECONCILIATION_REQUIRED')
    operations=[i['command']['operation_id'] for i in plan.data.get('items',[])]
    authorizations=(await db.scalars(select(WealthAuthorizationORM).where(
        WealthAuthorizationORM.user_id==user,WealthAuthorizationORM.operation_id.in_(operations)))).all()
    if any(a.status=='CONSUMED' for a in authorizations):
        reject('已有银行受理结果，请按原号核对','RECONCILIATION_REQUIRED')
    for a in authorizations:
        if a.status in {'AWAITING_CONFIRMATION','AWAITING_MFA','VALID'}:a.status='EXPIRED'
    plan.status='CANCELLED'
    await db.flush()
    tasks=(await db.scalars(select(WealthTaskORM).where(WealthTaskORM.user_id==user,
        WealthTaskORM.status.not_in(wealth_task.TERMINAL)))).all()
    for task in tasks:
        if task.data.get('plan_id')==plan_id or task.session_id==plan_id:
            await wealth_task.control(db,user,task.task_id,'cancel',{'request_id':uuid4().hex,
                'expected_version':task.version,'reason':'本人修改NOVA未提交方案，旧确认及验证失效'})


async def link_continuation(db,user,previous,next_id):
    if not next_id:return
    rows=(await db.scalars(select(WealthConversationORM).where(
        WealthConversationORM.user_id==user,WealthConversationORM.session_id.like('NW%')))).all()
    for row in rows:
        if row.data.get('nova_session_id') and row.data.get('execution_plan_id')==previous and not row.data.get('closed'):
            row.data={**row.data,'committed_stage_ids':list(dict.fromkeys([*row.data.get('committed_stage_ids',[]),previous]))}
            await db.flush()
            await link_plan(db,user,row.session_id,next_id)


async def continuation_allowed(db,user,plan_id):
    rows=(await db.scalars(select(WealthConversationORM).where(
        WealthConversationORM.user_id==user,WealthConversationORM.session_id.like('NW%')))).all()
    if any(r.data.get('execution_plan_id')==plan_id and (r.data.get('paused') or r.data.get('closed')) for r in rows):
        reject('NOVA理财后续步骤已暂停，已到账资金保留为现金', 'TASK_WRITE_BLOCKED')
