"""NOVA intent bridge. No consent, verification or bank execution capability."""
from ..audit.spans import audited
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import re
from typing import Literal
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field, model_validator
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
from .parameter_grounding import require_value, require_cash_role
from .grounding import money
from .semantic_bindings import card_frame,canonical_text,product_name_key,object_mentioned,resolve_object,require_object_binding


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


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class WealthIntent(EmptyArguments):
    kind: Literal['assets','positions','returns','detail','compare','assessment',
                  'recommend','planning','purchase','redeem','resume','abandon','clarify'] = Field(
        description='本轮业务意图。本人明确要求开始理财规划或确认进入后选planning；宽泛投资愿望先解释并邀请进入，不直接建卡；未提供预算或产品仍可选planning，不因此选clarify。')
    task_id: str | None = Field(default=None, max_length=64,
        description='修改或继续已有卡片时使用可信未关闭任务 ID；明确新开卡片时留空。')
    product_queries: list[str] = Field(default_factory=list, max_length=4,
        description='已选候选、已有卡片的具体产品引用或详情/比较/赎回对象。新对话要买或投资某名称但尚未选择候选时用name_query搜索，不在这里把名称当作已选产品。')
    name_query: str | None = Field(default=None, min_length=1, max_length=120,
        description='目标产品名称搜索：按语义摘出用户原文中有识别作用的名称核心关键词/简称或代码，区分专有名称、办理动作和泛称类别，不把泛称类别并入名称，不补全、不猜产品。多个名称关键词用空格分隔，每个词都逐字来自name_query_quote。新对话指名要投资/买产品时用kind=recommend，展示候选供本人选择。与product_queries、candidate_indices、category互斥。')
    name_query_quote: str | None = Field(default=None, min_length=1, max_length=200,
        description='支撑name_query的本轮连续原文，包含每个名称关键词；不引用历史或模型猜测。')
    candidate_indices: list[int] = Field(default_factory=list, max_length=4,
        description='用户所指卡片的候选序号，从1开始：第一个填[1]，第二个填[2]。不是从0开始的数组下标。')
    category: Literal['quick_redeem','bank_wealth','bond_fund','mixed_fund','equity_fund'] | None = Field(
        default=None,description='目标推荐的产品类别；用户未指定时留空并打开推荐表单，不能猜类别。')
    cash_field: Literal['reserve','invest','total'] | None = Field(default=None,
        description='规划预算含义：reserve 保留现金、invest 本次新增投资、total 总投资；不明确时留空。')
    cash_yuan: str | None = Field(default=None, max_length=24, pattern=r'^\d+(\.\d{1,2})?$',
        description='用户本轮明确给出的规划预算，人民币元；同时提供 cash_quote。')
    amounts_yuan: list[str | None] = Field(default_factory=list, max_length=4,
        description='按 product_queries 或已选产品顺序填写本轮明确的单产品申购金额，人民币元；逐项提供 amount_quotes。')
    redemption_input: Literal['units','market_value','holding_ratio'] = 'units'
    redemption_value: str | None = Field(default=None, max_length=24, pattern=r'^\d+(\.\d{1,6})?$',
        description='本轮明确给出的赎回份额、金额或比例，含义由 redemption_input 指定；同时提供 redemption_quote。')
    redemption_mode: Literal['standard','quick'] = 'standard'
    period: Literal['day','month','year','cumulative'] = Field(default='day',
        description='整体理财收益周期：day 昨日、month 近一月、year 近一年、cumulative 累计；未指定时为 day。')
    anchor_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    question: str = Field(default='', max_length=400)
    cash_quote: str | None = Field(default=None, max_length=200)
    amount_quotes: list[str] = Field(default_factory=list, max_length=4)
    redemption_quote: str | None = Field(default=None, max_length=200)
    advance: Literal['none','continue','prepare','confirm','next_batch'] = Field(default='none',
        description='用户明确要求推进现有表单步骤或换批时填写，并给出 advance_quote；纯询问、否定或新建卡片时用 none。确认交易仅提示本人在卡片操作。')
    advance_quote: str | None = Field(default=None,max_length=200,
        description='请求推进或换批时，逐字引用本轮用户要求继续或换批的连续原文；纯询问或否定不得填写。')
    stage: str | None = Field(default=None,max_length=64,pattern=r'^[a-z_]+$',
        description='推进已有卡片时选择可信当前步骤，如 match、candidates、cash_management、selected、budget、allocation、details、redemption_input；无焦点时留空。')
    selection_operation: Literal['keep','add','replace','remove'] = 'keep'
    edit_kind: Literal['correction','backtrack'] = 'correction'
    target_stage: Literal['match','cash_management','stable','growth','candidates','selected','budget','amounts','purchase_review','allocation','details','redemption_input','trade'] | None = None

    @model_validator(mode='after')
    def search_is_not_selection(self):
        if self.name_query and (self.kind not in {'recommend','purchase'} or self.product_queries or self.candidate_indices or self.category):
            raise ValueError('名称搜索只能用于目标推荐/申购候选，不能同时声明产品已选或类别筛选')
        return self


class IntentBatch(EmptyArguments):
    intents: list[WealthIntent] = Field(min_length=1, max_length=4)


class WealthPlanningArguments(WealthIntent):
    kind: Literal['planning','recommend','purchase','detail','compare'] = 'planning'


class WealthPositionsArguments(WealthIntent):
    kind: Literal['positions','redeem'] = Field(
        description='查询或筛选持仓用 positions；用户明确要赎回或调整持仓时用 redeem。')


class WealthReturnsArguments(WealthIntent):
    kind: Literal['returns'] = 'returns'


class RiskAnswerEvidence(EmptyArguments):
    question_quote: str = Field(min_length=1,max_length=200)
    option_quote: str = Field(min_length=1,max_length=200)


class WealthRiskArguments(EmptyArguments):
    action: Literal['open','reassess','answer','submit','confirm'] = 'open'
    task_id: str | None = Field(default=None,max_length=64)
    answers: dict[str,int] = Field(default_factory=dict,max_length=9,
        description='本人本轮明确回答的问卷题目 ID 到选项编号（0至3）的映射；未回答的题目不要猜。')
    answer_evidence: dict[str,RiskAnswerEvidence] = Field(default_factory=dict,max_length=9)
    adult_quote: str | None = Field(default=None,max_length=200,
        description='提交时逐字引用本人明确声明成年且具有完全民事行为能力的原文。')
    confirm_quote: str | None = Field(default=None,max_length=200,
        description='确认最新评估结果时逐字引用本人明确确认评估结果的原文。')


class AssessmentGateIntent(EmptyArguments):
    kind: Literal['assets','positions','returns','detail','compare','assessment',
                  'recommend','planning','purchase','redeem'] = Field(
        description='用户希望在完成本人风险评估后办理的理财类型；笼统的理财规划需求归为planning。')


def verified_name_query(intent, message):
    """Verify model-supplied evidence; never infer or expand a product name."""
    query = intent.name_query
    quote = intent.name_query_quote
    terms = query.split() if query else []
    return query if terms and quote and all(term in quote for term in terms) and quote in message else None


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


@audited("wealth")
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


@audited("wealth")
async def propose_backtrack(db,user,row,stage,command=None):
    from uuid import uuid4
    await assert_reversible(db,user,row.data)
    kind=wealth_steps.effective_kind(row.data)
    if stage=='candidates' and kind in {'recommend','purchase'} and not row.data['command'].get('category') and not row.data['command'].get('name_query'):
        stage='match'
    if stage=='trade':stage='allocation' if kind=='planning' else 'redemption_input' if kind=='redeem' else 'purchase_review'
    if stage=='details' and kind=='planning':stage='allocation'
    try:invalid=wealth_steps.affected(kind,stage)
    except ValueError:reject('当前任务没有这个步骤')
    row.data={**row.data,'pending_backtrack':{'token':uuid4().hex,'stage':stage,
        'label':wealth_steps.LABELS[stage],'invalidated':invalid,'command':command}}
    row.version+=1


@audited("wealth")
async def manage(arguments, context):
    if not context.session_id or not context.request_id or not context.original_message:
        return {'error':'WEALTH_CONTEXT_REQUIRED', 'message':'缺少理财会话上下文'}
    db, user, session = context.db, context.user_id, context.session_id
    current = await cards(db, user, session)
    editable = [card for card in current if not card.get('closed')]
    editable_ids = {card['task_id'] for card in editable}
    if context.wealth_task_id and context.wealth_task_id not in editable_ids:
        context = replace(context, wealth_task_id=None, wealth_stage=None)
    argument_key=arguments.model_dump_json(exclude_none=True) if isinstance(arguments,WealthIntent) else ''
    request_key='NWREQ'+hashlib.sha256(f'{user}:{session}:{context.request_id}:{argument_key}'.encode()).hexdigest()[:40]
    request_digest=hashlib.sha256(context.original_message.encode()).hexdigest()
    replay=await db.get(WealthChatRequestORM,request_key)
    if replay:
        if replay.digest!=request_digest:
            reject('请求编号不能用于不同的理财需求','IDEMPOTENCY_CONFLICT')
        return {'reply':replay.response.get('reply') or '已恢复本次理财任务，请核对卡片。',
                'trusted_reply':replay.response.get('trusted_reply'), 'wealth_cards':current}
    from ...integrations.wealth_authorization import latest_assessment, aware
    assessment=await latest_assessment(db,user)
    if not assessment or not assessment.confirmed_at or aware(assessment.expires_at)<=utc_now():
        gated=SimpleNamespace(data=AssessmentGateIntent(kind=arguments.kind)) if isinstance(arguments,WealthIntent) else await llm.generate_structured(messages=[
            {'role':'system','content':
                '你是理财风险评估前的需求识别助手。当前本人尚无已确认且有效的风险评估。'
                '只识别用户在完成评估后想办理的理财类型，输出AssessmentGateIntent结构。'
                '笼统地表示想理财、做配置或规划时选择planning；明确要求风险评估时选择assessment。'
                '不要抽取金额、产品或交易参数，不要生成规划、推荐、申赎或声称已办理。'
                '用户须先在表单由本人回答并确认评估，然后才能在原卡继续相应操作。'},
            {'role':'user','content':json.dumps({'message':redact(context.original_message),
                'focused_card':{'task_id':context.wealth_task_id,'stage':context.wealth_stage},
                'pending_tasks':[{'task_id':c['task_id'],'kind':c.get('command',{}).get('kind')}
                                 for c in current if c.get('assessment_pending') and not c.get('closed')]},ensure_ascii=False)}
        ],schema=AssessmentGateIntent,max_tokens=200)
        facts=await HttpWealthBankApi().assets(user)
        if not facts.get('context_id'):
            reject('理财账户范围暂不可用，请重试','STALE_CONTEXT')
        await db.rollback()
        await lock_profile(db,user)
        await owner(db,user,session)
        repeated=await db.get(WealthChatRequestORM,request_key,populate_existing=True)
        if repeated:
            if repeated.digest!=request_digest:reject('请求编号不能用于不同的理财需求','IDEMPOTENCY_CONFLICT')
            await db.rollback()
            return {'reply':repeated.response['reply'],
                    'trusted_reply':repeated.response.get('trusted_reply'),
                    'wealth_cards':await cards(db,user,session)}
        kind=gated.data.kind
        name_query=verified_name_query(arguments,context.original_message) if isinstance(arguments,WealthIntent) else None
        existing=next((c for c in current if c.get('assessment_pending') and not c.get('closed')
                       and not c.get('execution_plan_id') and c.get('command',{}).get('kind')==kind
                       and (not name_query or c.get('command',{}).get('name_query')==name_query)),None)
        if not existing:
            task_id='NW'+hashlib.sha256(f'{user}:{session}:{context.request_id}:assessment'.encode()).hexdigest()[:40]
            command=WealthIntent(kind=kind).model_dump(mode='json')
            if name_query:
                command.update(name_query=name_query,name_query_quote=arguments.name_query_quote)
            command['products']=[]
            db.add(WealthConversationORM(session_id=task_id,user_id=user,version=1,data={
                'nova_session_id':session,'request_id':context.request_id,'context_id':facts['context_id'],
                'command':command,'assessment_pending':True,'closed':False}))
        reply='请先完成并本人确认风险评估。已打开评估表单；确认有效后可在原卡继续理财操作。'
        db.add(WealthChatRequestORM(request_key=request_key,user_id=user,digest=request_digest,status='COMPLETED',
            response={'session_id':session,'reply':reply,'trusted_reply':reply},started_at=utc_now()))
        await db.commit()
        return {'reply':reply,'trusted_reply':reply,'wealth_cards':await cards(db,user,session)}
    result = SimpleNamespace(data=IntentBatch(intents=[arguments.model_copy(deep=True)])) if isinstance(arguments,WealthIntent) else await llm.generate_structured(messages=[
        {'role':'system','content':load_skill('wealth') + '\n金额和份额只能来自本轮原文。'
         'cash_yuan提供cash_quote；amounts_yuan逐项提供amount_quotes；redemption_value提供redemption_quote。'
         'quote必须是本轮连续原文并包含数值和单位，多产品金额quote还须包含该产品名、代码或明确序号。'
         '先根据语义识别字段，不限固定措辞；cash_quote应包含支撑字段含义的原文。'
         '省略字段时只使用当前焦点可信卡片，不从历史回答推测。只提取本轮修改值，保留值留空由服务端继承。'
         '中文或数字序号都转成从1起始的candidate_indices；不同候选池不可混用。'
         '不确定则留空，不能从产品代码、历史回答或余额补造用户金额。'},
        {'role':'user','content':json.dumps({
            'message':redact(context.original_message),
            'focused_card':{'task_id':context.wealth_task_id,'stage':context.wealth_stage},
            'history':(context.conversation_history or [])[-12:],
            'tasks':[{k:c.get(k) for k in ('task_id','version','command','view_context','closed','execution_plan_id')} for c in editable[-20:]],
        },ensure_ascii=False)}], schema=IntentBatch, max_tokens=2000)
    result.data.intents=[category_intent(i) for i in result.data.intents]
    name_notices=[]
    for intent in result.data.intents:
        if intent.name_query and not verified_name_query(intent,context.original_message):
            intent.name_query=None;intent.name_query_quote=None
            name_notices.append('产品名称片段未能从本轮原文核实，请在目标产品推荐中填写名称或代码。')
        if intent.task_id and intent.task_id not in editable_ids:
            intent.task_id=None
    # Read-only drill-downs never replace their parent selection/holding task.
    for intent in result.data.intents:
        if intent.kind in {'detail','compare','returns'}:intent.advance='none'
        if intent.kind in {'detail','compare'} and not intent.task_id and context.wealth_task_id:
            intent.task_id=context.wealth_task_id
        if intent.kind=='returns':
            intent.product_queries=[];intent.candidate_indices=[];intent.anchor_date=None
    if (context.execution_step or context.serial_focus_only) and len(result.data.intents)!=1:
        return {'error':'SERIAL_STEP_AMBIGUOUS','message':'当前步骤包含多个操作，请明确单个目标。','wealth_cards':current}
    if context.serial_focus_only:
        focused=next((c for c in current if c['task_id']==context.wealth_task_id),None)
        for intent in result.data.intents:
            if intent.kind in {'assets','positions','returns','detail','compare','assessment'}:continue
            if not focused or intent.task_id not in (None,focused['task_id']):
                return {'error':'SERIAL_STEP_PENDING','message':'请先完成当前队列卡片或停止后续办理。','wealth_cards':current}
            if focused['command']['kind']!='clarify' and intent.kind not in {focused['command']['kind'],'resume','abandon'} and not (
                (focused['command']['kind']=='recommend' and intent.kind=='purchase') or
                (focused['command']['kind']=='positions' and intent.kind=='redeem')):
                return {'error':'SERIAL_STEP_PENDING','message':'当前只可补充本步骤，请先完成当前卡片。','wealth_cards':current}
            intent.task_id=focused['task_id']
    try:
        for intent in result.data.intents:
            focused=next((c for c in current if c['task_id']==context.wealth_task_id),None)
            if focused and focused['command']['kind'] in {'positions','redeem'} and context.wealth_stage=='redemption_input' and intent.kind in {'planning','recommend','purchase'}:
                return {'error':'HOLDINGS_TASK_CONTEXT','message':'当前正在办理已选持仓赎回，请填写赎回数量；如果希望切换，请明确说明要结束当前赎回并办理的目标操作。','wealth_cards':current}
            if intent.kind=='redeem' and focused and focused['command']['kind'] in {'positions','redeem'}:
                if intent.task_id not in (None,focused['task_id']):raise ValueError('REFERENCE_NOT_GROUNDED')
                intent.task_id=focused['task_id']
            if (not intent.task_id and context.wealth_task_id and
                    (intent.cash_yuan is not None or intent.amounts_yuan or intent.redemption_value is not None or intent.candidate_indices or intent.name_query)):
                focused=next((c for c in current if c['task_id']==context.wealth_task_id),None)
                if focused and intent.kind==focused.get('command',{}).get('kind'):
                    intent.task_id=context.wealth_task_id
            require_value(intent.cash_yuan, context.original_message, quote=intent.cash_quote)
            focused = next((card for card in current if card['task_id'] == (intent.task_id or context.wealth_task_id)), {})
            frame=card_frame(focused,focused_stage=context.wealth_stage) if focused else None
            intent.cash_field=require_cash_role(intent.cash_field, intent.cash_yuan, context.original_message,
                              frame.focused_field if frame else None,quote=intent.cash_quote)
            visible=(focused.get('view_context') or {}).get('visible_products',[])
            if context.wealth_stage in {'cash_management','stable','growth'}:
                visible=[p for p in visible if p.get('bucket')==context.wealth_stage]
            if any(not object_mentioned(context.original_message,ordinal=i) for i in intent.candidate_indices):
                raise ValueError('REFERENCE_NOT_GROUNDED')
            for index, value in enumerate(intent.amounts_yuan):
                quote = intent.amount_quotes[index] if index < len(intent.amount_quotes) else None
                require_value(value, context.original_message, quote=quote)
                if value is not None and len(intent.amounts_yuan) > 1:
                    reference=intent.product_queries[index] if index<len(intent.product_queries) else None
                    rank=intent.candidate_indices[index] if index<len(intent.candidate_indices) else None
                    aliases=[reference] if reference else []
                    if reference:
                        for p in [*visible,*((focused.get('view_context') or {}).get('selected_products',[]))]:
                            if canonical_text(reference) in {canonical_text(p['product_id']),canonical_text(p.get('name',''))}:
                                aliases.extend([p['product_id'],p.get('name','')])
                    if quote is None:raise ValueError('AMOUNT_OBJECT_NOT_GROUNDED')
                    require_object_binding(quote,aliases=aliases,ordinal=rank,
                        other_aliases=[q for j,q in enumerate(intent.product_queries) if j!=index])
            require_value(intent.redemption_value, context.original_message,
                          unit='money' if intent.redemption_input == 'market_value' else
                               'percent' if intent.redemption_input == 'holding_ratio' else 'units',
                          quote=intent.redemption_quote)
            # 自由问题不能作为事实正文进入可信任务卡。
            intent.question = '请补充具体产品、金额或要处理的任务，或在卡片中选择。'
    except ValueError:
        return {'error': 'PARAMETER_NOT_GROUNDED', 'message': '金额或份额缺少本轮原文依据，请明确产品、数值和单位。',
                'wealth_cards': current}
    bank = HttpWealthBankApi()
    facts = await bank.assets(user)
    return_results={}
    for index,intent in enumerate(result.data.intents):
        if intent.kind=='returns':
            return_results[index]=await bank.call('POST','/returns',{'subject_user_id':user,'scope':'all',
                'period':intent.period,'overview':True})
    catalogue = await bank.call('POST','/catalog',{'subject_user_id':user,'facts_only':True,'policy_references':True}) if any(
        i.product_queries or i.candidate_indices or (i.kind in {'redeem','detail','compare'} and i.task_id) for i in result.data.intents) else None
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
    replies=list(name_notices)
    planning_opened=False
    for index, intent in enumerate(result.data.intents):
        if intent.kind=='assets':
            cash=sum(a['available_fen'] for a in facts['cash'] if a['account_type']!='credit')
            summary=facts.get('asset_summary') or {}
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
        pending_advance=None
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
            if not intent.advance_quote or intent.advance_quote not in context.original_message:
                replies.append('继续或换批的原文依据未能确认；请在当前卡片核对，或再次明确说明要继续的步骤。')
                continue
            pending_advance={'id':context.request_id,'action':intent.advance,
                             'stage':context.wealth_stage or intent.stage}
            if not (intent.product_queries or intent.candidate_indices or intent.cash_yuan is not None
                    or any(value is not None for value in intent.amounts_yuan)
                    or intent.redemption_value is not None or intent.category is not None or intent.name_query is not None):
                row.data={**row.data,'instruction':pending_advance}
                row.version+=1
                replies.append('已交给当前卡片核对。交易同意须本人手动勾选，验证码仍在验证框输入。')
                continue
            intent=intent.model_copy(update={'task_id':selected_task})
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
            if any(product_name_key(q) not in product_name_key(context.original_message) and not any(
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
                    queries.extend(resolve_object(f'第{i}个',visible,id_field='product_id')['product_id'] for i in intent.candidate_indices)
            for query in queries:
                matches = [p for p in catalogue if canonical_text(query)==canonical_text(p['product_id']) or product_name_key(query)==product_name_key(p['name'])]
                if len(matches) != 1:
                    command.update(kind='clarify',question=f'“{query}”未唯一匹配，请提供准确名称或代码，或在候选卡中选择。')
                    break
                p = matches[0]
                refs.append({'product_id':p['product_id'],'product_version':p['version'],'name':p['name']})
        prior=next((c for c in current if c['task_id']==intent.task_id),None)
        if prior and intent.kind=='redeem' and prior['command']['kind'] in {'positions','redeem'} and not refs and not intent.product_queries and not intent.candidate_indices:
            selected=(prior.get('view_context') or {}).get('selected_products',[]) if (prior.get('view_context') or {}).get('selection_kind')=='positions' else []
            if not selected:
                replies.append('请先在当前持仓卡勾选需要赎回的产品，再继续赎回；不会新建其他任务。')
                continue
            held={(lot['product_id'],str(lot['version_id'])) for lot in facts.get('lots',[])}
            for p in selected:
                matches=[c for c in catalogue if c['product_id']==p['product_id'] and c['version']==p['product_version']]
                if len(matches)!=1 or (p['product_id'],f"{p['product_id']}:{p['product_version']}") not in held:
                    reject('已选持仓已变化或不属于当前账户，请重新勾选当前持仓。','STALE_HOLDING_SELECTION')
                refs.append({'product_id':p['product_id'],'product_version':p['product_version'],'name':matches[0]['name']})
        if prior and intent.kind in ('recommend','purchase','planning'):
            saved=prior.get('view_context') or {}
            previous=[] if intent.name_query else saved['selected_products'] if 'selected_products' in saved else prior.get('command',{}).get('products',[])
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
            if command['category'] is None and not intent.name_query:command['category']=prior.get('command',{}).get('category')
            if not command.get('name_query') and not intent.category:
                command['name_query']=prior.get('command',{}).get('name_query')
        if command['kind']!='clarify' and intent.kind == 'purchase' and not refs and not command.get('category') and not command.get('name_query'):
            command.update(kind='clarify',question='希望查找哪类产品，或哪只产品的名称／代码？')
        command['products'] = refs
        if intent.name_query:
            replies.append(f'已按“{intent.name_query}”打开目标产品名称匹配，请在卡片中查看候选并选择具体产品；尚未选定或购买。')
        parent_id=None
        if intent.kind in {'detail','compare'} and prior:
            parent_id=prior['task_id']
            if not refs:
                selected=(prior.get('view_context') or {}).get('selected_products',[])
                if (intent.kind=='detail' and len(selected)==1) or (intent.kind=='compare' and 2<=len(selected)<=4):
                    refs=[]
                    for p in selected:
                        matches=[c for c in catalogue or [] if c['product_id']==p['product_id'] and c['version']==p['product_version']]
                        if len(matches)!=1:reject('已选产品版本已变化，请在原候选中重新核对。','STALE_PRODUCT_SELECTION')
                        refs.append({'product_id':p['product_id'],'product_version':p['product_version'],'name':matches[0]['name']})
                    command['products']=refs
            intent.task_id=None;prior=None
        if intent.kind=='compare' and len(refs)<2:
            command.update(kind='clarify',question='请在推荐或规划的候选中选择2至4只产品，或明确要比较的产品名称／代码。')
        if intent.kind=='detail' and not refs:
            command.update(kind='clarify',question='请指定产品名称／代码，或从候选及持仓中打开详情。')
        if prior and (prior.get('pending_backtrack')):
            replies.append('请先在卡片确认或取消待处理的回退。');continue
        edit_stage=intent.target_stage or context.wealth_stage
        if prior:edit_stage=wealth_steps.parameter_stage(prior,command,edit_stage)
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
        task_id = intent.task_id or 'NW'+hashlib.sha256(f'{user}:{session}:{context.request_id}:{argument_key}:{index}'.encode()).hexdigest()[:40]
        data = dict(nova_session_id=session,request_id=context.request_id,context_id=facts['context_id'],command=command,closed=False)
        if pending_advance:data['instruction']=pending_advance
        if parent_id:data['parent_task_id']=parent_id
        if intent.kind in {'detail','compare'} and refs:
            data['read_result']={'products':refs}
        if index in return_results:
            performance=return_results[index].get('performance') or {}
            data['read_result']=performance
            amount=performance.get('amount_fen');rate=performance.get('rate_percent')
            replies.append(f"整体理财收益（{performance.get('start_date','')}—{performance.get('end_date','')}，含已清仓产品）："
                + (money(amount) if amount is not None else '待更新')+'；收益率：'
                +(f'{float(rate):+.2f}%' if rate is not None else '—' if amount is not None else '待更新')+'。'
                +(performance.get('notice') or ''))
        if intent.kind=='redeem' or prior and (prior['command']['kind']=='positions' or prior.get('origin_kind')=='positions'):
            data['origin_kind']='positions'
        if prior and prior.get('view_context'):
            data['view_context']=deepcopy(prior['view_context'])
            if intent.kind in ('planning','recommend','purchase','redeem'):
                data['view_context']['selected_products']=refs
                if intent.kind!='redeem':data['view_context']['amounts_yuan']={p['product_id']:command['amounts_yuan'][i] or '' for i,p in enumerate(refs)}
                if intent.cash_yuan is not None:
                    data['view_context']['cash_yuan']=command['cash_yuan']
                    data['view_context']['cash_field']='investment' if command['cash_field']=='invest' else command['cash_field']
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
        if command['kind']=='planning':
            planning_opened=True
            replies.append('已打开理财规划卡片，请在卡片中继续选择和核对。')
    trusted_reply=None
    db.add(WealthChatRequestORM(request_key=request_key,user_id=user,digest=request_digest,status='COMPLETED',
        response={'session_id':session,'reply':'\n\n'.join(replies),'trusted_reply':trusted_reply},started_at=utc_now()))
    await db.commit()
    return {'reply':'\n\n'.join(replies) or '已更新理财任务，请核对下方卡片。',
            'trusted_reply':trusted_reply,
            'wealth_cards':await cards(db,user,session)}


async def manage_risk(arguments: WealthRiskArguments, context):
    if not context.session_id or not context.request_id:
        return {'error':'WEALTH_CONTEXT_REQUIRED','message':'缺少理财会话上下文'}
    db,user,session=context.db,context.user_id,context.session_id
    await owner(db,user,session)
    from ...integrations.wealth_authorization import latest_assessment,aware,submit_assessment,confirm_assessment
    from ...schemas.wealth import AssessmentSubmission
    from ...domain.wealth_risk import QUESTIONS,QUESTIONNAIRE_VERSION
    from .semantic_bindings import quote_in_message
    question_map={key:(index,title,options) for index,(key,title,options) in enumerate(QUESTIONS,1)}
    for key,value in arguments.answers.items():
        evidence=arguments.answer_evidence.get(key)
        if key not in question_map or type(value) is not int or not 0<=value<=3 or evidence is None:
            reject('风险问卷答案缺少题目或选项依据','ASSESSMENT_ANSWER_UNGROUNDED',422)
        index,title,options=question_map[key]
        if (not quote_in_message(evidence.question_quote,context.original_message or '')
                or not quote_in_message(evidence.option_quote,context.original_message or '')
                or not object_mentioned(evidence.question_quote,aliases=(key,title),ordinal=index)
                or not object_mentioned(evidence.option_quote,aliases=(options[value],),ordinal=value+1)):
            reject('风险问卷答案缺少题目或选项依据','ASSESSMENT_ANSWER_UNGROUNDED',422)
    if arguments.action=='submit' and not quote_in_message(arguments.adult_quote or '',context.original_message or ''):
        reject('请本人明确声明成年且具有完全民事行为能力','ASSESSMENT_DECLARATION_REQUIRED',422)
    if arguments.action=='confirm' and not quote_in_message(arguments.confirm_quote or '',context.original_message or ''):
        reject('请本人明确确认风险评估结果','ASSESSMENT_CONFIRMATION_REQUIRED',422)
    assessment=await latest_assessment(db,user)
    valid=bool(assessment and assessment.confirmed_at and aware(assessment.expires_at)>utc_now())
    capacity_level=assessment.result['capacity_level'] if valid else None
    argument_key=arguments.model_dump_json(exclude_none=True)
    request_key='NWREQ'+hashlib.sha256(f'{user}:{session}:{context.request_id}:risk:{argument_key}'.encode()).hexdigest()[:40]
    repeated=await db.get(WealthChatRequestORM,request_key)
    if repeated:
        return {'reply':repeated.response['reply'],'trusted_reply':repeated.response['reply'],
                'wealth_cards':await cards(db,user,session)}
    facts=await HttpWealthBankApi().assets(user)
    if not facts.get('context_id'):reject('理财账户范围暂不可用，请重试','STALE_CONTEXT')
    await db.rollback()
    profile=await lock_profile(db,user)
    await owner(db,user,session)
    repeated=await db.get(WealthChatRequestORM,request_key,populate_existing=True)
    if repeated:
        return {'reply':repeated.response['reply'],'trusted_reply':repeated.response['reply'],
                'wealth_cards':await cards(db,user,session)}
    current=await cards(db,user,session)
    def risk_target(card):
        return (not card.get('closed') and
                (card.get('command',{}).get('kind')=='assessment' or
                 arguments.action in {'answer','submit','confirm'} and card.get('assessment_pending')))
    selected_id=arguments.task_id
    if not selected_id and context.wealth_task_id:
        focused=next((c for c in current if c['task_id']==context.wealth_task_id
                      and risk_target(c)),None)
        selected_id=focused['task_id'] if focused else None
    target=next((c for c in current if c['task_id']==selected_id and risk_target(c)),None)
    if arguments.task_id and target is None:
        reject('所指风险评估卡不存在或已关闭','ASSESSMENT_CARD_NOT_FOUND',404)
    if target is None and arguments.action in {'answer','submit','confirm'}:
        target=next((c for c in reversed(current) if risk_target(c)),None)
    answers=dict((target or {}).get('view_context',{}).get('risk_answers') or {})
    answers.update(arguments.answers)
    if arguments.action=='submit':
        if set(answers)!=set(question_map):reject('请先完整回答九道风险评估题','ASSESSMENT_INCOMPLETE',422)
        result=await submit_assessment(db,user,AssessmentSubmission(questionnaire_version=QUESTIONNAIRE_VERSION,
            profile_version=profile.version,adult=True,full_capacity=True,answers=answers))
        reply=f"风险评估已提交，结果为 R{result['capacity_level']}；请由本人确认评估结果后继续理财。"
    elif arguments.action=='confirm':
        latest=await latest_assessment(db,user)
        if latest is None:reject('尚无可确认的风险评估','ASSESSMENT_NOT_FOUND',404)
        result=await confirm_assessment(db,user,latest.assessment_id)
        reply=f"已确认风险评估等级 R{result['capacity_level']}，现在可以继续理财操作。"
    else:
        if target:
            row=await require_card(db,user,target['task_id'])
            data=deepcopy(row.data)
            data['request_id']=context.request_id
            data['reassess_open']=(arguments.action=='reassess' if arguments.action in {'open','reassess'}
                                   else data.get('reassess_open',False))
            if arguments.action=='reassess':
                data['assessment_pending']=True
                data['reassess_started_at']=utc_now().isoformat()
            if arguments.action=='answer':data.setdefault('view_context',{})['risk_answers']=answers
            row.data=data;row.version+=1
        else:
            task_id='NW'+hashlib.sha256(f'{user}:{session}:{context.request_id}:risk:{argument_key}'.encode()).hexdigest()[:40]
            command=WealthIntent(kind='assessment').model_dump(mode='json')
            command['products']=[]
            db.add(WealthConversationORM(session_id=task_id,user_id=user,version=1,data={
                'nova_session_id':session,'request_id':context.request_id,'context_id':facts['context_id'] if facts else '',
                'command':command,'view_context':{'risk_answers':answers},
                'assessment_pending':not valid or arguments.action=='reassess',
                'reassess_open':arguments.action=='reassess',
                **({'reassess_started_at':utc_now().isoformat()} if arguments.action=='reassess' else {}),
                'closed':False}))
        reply=(f"当前已确认的风险评估等级为 R{capacity_level}。请在卡片中选择是否重新评估。"
               if valid and arguments.action=='open' else
               '已记录本人回答；请继续完成问卷。' if arguments.action=='answer' else
               '已打开风险评估表单，请由本人回答并确认。')
    if arguments.action in {'submit','confirm'}:
        if target:
            row=await require_card(db,user,target['task_id'])
            data=deepcopy(row.data)
            data['request_id']=context.request_id
            data.setdefault('view_context',{})['risk_answers']=answers
            data['assessment_pending']=not result['confirmed']
            if result['confirmed']:data['reassess_open']=False
            row.data=data;row.version+=1
        else:
            task_id='NW'+hashlib.sha256(f'{user}:{session}:{context.request_id}:risk:{argument_key}'.encode()).hexdigest()[:40]
            command=WealthIntent(kind='assessment').model_dump(mode='json')
            command['products']=[]
            db.add(WealthConversationORM(session_id=task_id,user_id=user,version=1,data={
                'nova_session_id':session,'request_id':context.request_id,'context_id':facts['context_id'],
                'command':command,'view_context':{'risk_answers':answers},
                'assessment_pending':not result['confirmed'],'closed':False}))
    db.add(WealthChatRequestORM(request_key=request_key,user_id=user,digest=hashlib.sha256(
        (context.original_message or '').encode()).hexdigest(),status='COMPLETED',
        response={'session_id':session,'reply':reply,'trusted_reply':reply},started_at=utc_now()))
    await db.commit()
    return {'reply':reply,'trusted_reply':reply,'wealth_cards':await cards(db,user,session)}


def wealth_tools():
    definitions={
        'assess_wealth_risk':(WealthRiskArguments,manage_risk,
            '打开或操作本人风险评估卡。action=open 查询当前等级并显示是否重新评估的按钮；reassess 打开问卷；answer 将本轮明确回答的题目填入卡片；submit 在九题完整且本人明确声明成年完全民事行为能力后提交；confirm 须本人明确确认最新结果。'
            'answers 是题目 ID 到从0开始的选项编号，answer_evidence 每题分别逐字引用本轮题目和选项原文。不得猜答案或代用户声明；未回答的题目留空。'),
        'plan_wealth':(WealthPlanningArguments,manage,
            '打开或更新规划与推荐表单。kind=planning 为整体配置规划，仅本人明确要求或确认启动规划时选 planning；kind=recommend 为目标产品推荐，kind=purchase 为申购草稿，detail/compare 为产品资料查看。'
            '新对话指名要买/投资某产品而尚未选择候选时，使用kind=recommend、name_query及name_query_quote，保留原文名称片段供名称包含匹配，不补全、不将匹配结果视为已选产品；无匹配仍保留搜索表单。'
            '把用户明确给出的产品、类别、预算和操作填入字段；缺失字段留空仍打开卡片。更新已有卡时传可信 task_id。'
            '用户通过聊天选择产品、调整预算、换批或继续时，用 candidate_indices、selection_operation、cash_field/cash_yuan、advance、target_stage 表达；引用须来自当前可见候选和用户原文。'),
        'manage_wealth_positions':(WealthPositionsArguments,manage,
            '打开或更新本人持仓查询与调整表单。kind=positions 查询持仓；kind=redeem 在持仓卡勾选并准备赎回。'
            '已说要赎回时选 redeem；具体产品、份额、金额或比例填入 product_queries、redemption_input、redemption_value，并提供原文引用。'
            '缺失输入留空，供用户在卡片或后续聊天补充；修改现有卡传可信 task_id。'),
        'show_wealth_returns':(WealthReturnsArguments,manage,
            '打开本人整体理财收益表单并查询收益。period 可选 day、month、year、cumulative，缺省为 day；可在后续聊天调整周期。'),
    }
    return {name:{'schema':{'type':'function','function':{'name':name,'description':description,
             'parameters':argument_type.model_json_schema()}},
             'arguments':argument_type,'execute':execute,'requires_identity':True}
            for name,(argument_type,execute,description) in definitions.items()}


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
