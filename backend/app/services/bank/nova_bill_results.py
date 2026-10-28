"""Build read-only views from verified tool results, never from assistant prose.

Snapshots are immutable and encrypted. Opening a workspace queries current data;
restoring a conversation always returns the original query-time snapshot.
"""
import hashlib
import json
import logging
from decimal import Decimal
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from pydantic import ValidationError
from ...models.nova_bill_result import NovaBillResultORM as Snapshot
from ...schemas.nova_bill_results import BillResultCard
from . import nova_conversations as conversations

logger = logging.getLogger(__name__)
TOOLS = {
    'get_bill_analysis': ('analysis','账单概览','bills.analysis'),
    'list_transactions': ('transactions','交易明细','bills.transactions'),
    'get_transaction': ('transaction','交易详情','bills.transaction'),
    'get_budget_status': ('budgets','预算使用情况','bills.budgets'),
    'list_bill_anomalies': ('anomalies','异常核对','bills.anomalies'),
    'get_subscription_insights': ('subscriptions','订阅扣费核对','bills.subscription_insights'),
    'project_cashflow': ('cashflow','现金流预测','bills.cashflow'),
    'list_recurring_records': ('recurring','周期收支','bills.recurring'),
    'list_forecast_reviews': ('forecasts','预测复盘','bills.forecasts'),
    'get_bill_catalog': ('catalog','账单整理目录','bills.catalog'),
    'preview_bill_merchant_change': ('merchant_preview','商户整理预览','bills.merchant_preview'),
}
CATEGORIES = {'dining':'餐饮','shopping':'购物','transport':'交通','entertainment':'娱乐','subscription':'订阅','education':'学习','other':'其他'}


def text(value):
    return conversations.redact(str(value or ''))[:1000]


def when(value):
    at=datetime.fromisoformat(value.replace('Z','+00:00'))
    if at.tzinfo is None:raise ValueError('TIMEZONE_REQUIRED')
    return at.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')


def metric(label, amount=None, value=None):
    if amount is not None and type(amount) is not int:
        raise ValueError('NON_INTEGER_CENTS')
    return {'label':label,'amount_fen':amount,'text':text(value) if value is not None else None}


def metrics(data, fields):
    return [metric(label, data[key]) for key,label in fields if key in data]


def yuan(value):
    if type(value) is not int:
        raise ValueError('NON_INTEGER_CENTS')
    return f'¥{Decimal(value)/100:,.2f}'


def row(title, description='', badge='', values=None, target=None, progress=None):
    return {'title':text(title),'description':text(description),'badge':text(badge),
            'metrics':values or [],'target':target,'progress':float(progress) if progress is not None else None}


def chart(title, kind, series):
    return {'title':title,'type':kind,'series':series}


def target(section, subsection=None, **values):
    return {'section':section,'subsection':subsection,**values}


def build(item, evidence, request_id):
    kind,title,source = TOOLS[item['name']]
    result = item['result']
    if result.get('source') != source or result.get('amount_unit') != 'fen' or result.get('currency') != 'CNY' or result.get('data_kind') != 'simulated_bank':
        raise ValueError('UNVERIFIED_RESULT_ENVELOPE')
    d = result['data']
    query = d.get('query') or {}
    if kind=='transaction' and not query:
        query={'period':'month','date':when(d['timestamp'])[:7],'account_id':d['account_id']}
    query_target = {k:v for k,v in query.items() if k in {'period','date','account_id','category','tag_id'}}
    card = dict(version=1,result_id='',request_id=request_id,kind=kind,title=title,summary='',
        as_of=result['as_of'],scope=['模拟银行数据 · CNY','指定账户' if query.get('account_id') else '本人授权账户范围'],
        notices=[text(n) for n in d.get('notices',[])],metrics=[],charts=[],rows=[],total=d.get('total'),
        has_more=bool(d.get('has_more')),target=target('overview',query=query_target),
        evidence_id=evidence.evidence_id,content_hash=evidence.content_hash,compact_reply=False)
    if query.get('date'):
        card['scope'].append(('年度' if query.get('period')=='year' else '月度')+'账期 '+query['date'])
    if query.get('category'):
        card['scope'].append('分类：'+CATEGORIES.get(query['category'],'其他'))
    if query.get('tag_id'):
        card['scope'].append('已应用标签筛选')
    if kind == 'analysis':
        totals = d['totals']
        card['scope'].append('对比区间：'+when(d['previous_start'])+' — '+when(d['previous_end_exclusive'])+'（不含终点）')
        card['metrics'] = metrics(totals,[('net_consumption_fen','净消费'),('inflow_fen','报告流入'),('outflow_fen','报告流出'),('net_inflow_fen','报告净流入'),('consumption_fen','消费原额'),('refund_fen','退款')])
        card['summary'] = f"本期净消费 {yuan(totals['net_consumption_fen'])}，消费原额较可比上期变化 {yuan(d['change_fen'])}。"
        card['notices'] += ['报告流入流出包含转账；理财申赎不参与账单。消费原额、退款与净消费分开统计。']
        if d['partial']:
            card['notices'].append('本期尚未结束；以上为截至查询时点的数据，上期按同期比较。')
        points=[{'label':text(c['label']),'value_fen':c['amount_fen'],'target':target('transactions',query={**query_target,'category':c['category']})} for c in d['categories']]
        card['charts'].append(chart('消费分类 · 原额','bar',[{'name':'消费原额','points':points}]))
        card['charts'].append(chart('消费趋势','line',[{'name':'消费原额','points':[{'label':p['date'],'value_fen':p['consumption_fen']} for p in d['trend']]}]))
        drivers=d.get('change_explanation',{})
        card['rows']=[row(r['merchant'],r['explanation'],'消费变化',metrics(r,[('current_fen','本期'),('previous_fen','上期'),('change_fen','变化')]),target('transactions',query=query_target,tx_ids=[t['tx_id'] for t in r['current_evidence']][:200]) if r['current_evidence'] else None) for r in drivers.get('drivers',[])[:5]]
        if drivers.get('drivers_has_more') or len(drivers.get('drivers',[]))>5:
            card['notices'].append('当前展示主要变化商户，完整变化依据可在账单工作台查看。')
        pending=[r for r in d.get('anomalies',[]) if r.get('review_status','pending')=='pending']
        for r in pending[:3]:
            card['rows'].append(row(r['title'],r['reason'],'待核对提示',target=target('checks','anomalies',query=query_target)))
        if pending:card['notices'].append('当前返回的异常中有 '+str(len(pending))+' 项待核对；异常提示不认定欺诈。')
        card['target']=target('transactions',query=query_target)
    elif kind in {'transactions','transaction'}:
        records=d['items'] if kind=='transactions' else [d]
        card['target']=target('transactions',query=query_target,**({'tx_id':d['tx_id']} if kind=='transaction' else {}))
        for r in records:
            description=f"{when(r['timestamp'])} · {CATEGORIES.get(r['category'],'其他')}"
            description+=' · 标签：'+('、'.join(text(t['name']) for t in r.get('tags',[])) or '无')
            if r.get('original_tx_id'):description+=' · 已关联退款原单'
            if r.get('paired_tx_id'):description+=' · 已关联本人互转'
            card['rows'].append(row(r.get('merchant_name') or r['counterparty'],description,{'purchase':'消费','refund':'退款','salary':'工资收入','subscription':'订阅扣费','transfer_in':'转入','transfer_out':'转出'}.get(r['type'],'交易'),[metric('交易金额',r['amount_fen'])],target('transactions',query=query_target,tx_id=r['tx_id'])))
        card['notices'].append('仅展示本次查询范围内的已完成流水；金额按原交易展示，收支方向见交易类型。')
    elif kind == 'budgets':
        card['target']=target('budgets',budget_month=d['month'])
        card['scope']=['模拟银行数据 · CNY','预算月份 '+d['month'],'预算按各项账户及分类范围计算，不沿用标签筛选']
        for r in d['items']:
            badge='已停用' if not r['active'] else {'exceeded':'已超支','warning':'接近上限','projected_exceeded':'预计超支','ok':'未触发预警'}.get(r['status'],r['status'])
            description=('指定账户' if r['account_id'] else '全部账户')+f" · 使用 {r['usage_percent']}% · 预警线 {r['warning_percent']}%"
            if not r.get('projection_has_variable_baseline'):description+=' · 消费样本不足，月底估计仅含已知部分'
            card['rows'].append(row(CATEGORIES.get(r['category'],'总预算'),description,badge,metrics(r,[('amount_fen','预算'),('spent_fen','已用净消费'),('remaining_fen','剩余'),('projected_spending_fen','月底估计')]),card['target'],r['usage_percent']))
        card['notices'].append('预算预警属于估计，未触发预警不保证未来不会超支。')
    elif kind == 'anomalies':
        card['target']=target('checks','anomalies',query=query_target)
        card['rows']=[row(r['title'],r['reason']+'；备注：'+(r.get('review_note') or '无'),{'pending':'待核对','normal':'已核实正常','resolved':'已处理'}[r['review_status']],target=target('transactions',query=query_target,tx_ids=r['tx_ids'][:200]) if r['tx_ids'] else None) for r in d['items']]
        card['notices'].append('异常提示用于核对，不认定欺诈；没有提示不代表所有交易均无异常。')
    elif kind == 'subscriptions':
        card['target']=target('checks','subscriptions',query=query_target)
        for r in d['items']:
            card['rows'].append(row(r['merchant'],'；'.join(r['explanations']),'需要核对' if r['flags'] else '扣费核对',metrics(r,[('current_fen','本期实际'),('previous_fen','上期实际'),('configured_monthly_fen','当前月费计划')]),target('transactions',query=query_target,tx_ids=[t['tx_id'] for t in r['current_evidence']][:200]) if r['current_evidence'] else None))
        card['notices'].append('当前计划与历史实际扣费分别展示；本次查询不会取消订阅或执行扣费。')
    elif kind == 'cashflow':
        card['target']=target('forecast','cashflow',account_id=d['account_id'])
        card['scope']=['模拟银行数据 · CNY',f"从查询时起预测 {d['days']} 天",'指定储蓄账户' if d['account_id'] else '全部有效储蓄账户']
        card['metrics']=metrics(d,[('opening_available_fen','期初可用现金'),('baseline_end_fen','基准期末估计'),('scenario_end_fen','情景期末估计'),('scenario_saving_fen','情景差额')])
        scenario=d['scenario']
        for r in scenario['reductions']:
            card['scope'].append(CATEGORIES.get(r['category'],'其他')+f"减少 {r['percent']}%（假设）")
        if scenario['excluded_subscription_ids']:card['scope'].append(f"假设排除 {len(scenario['excluded_subscription_ids'])} 项订阅，尚未取消")
        for r in scenario.get('one_off_events',[]):card['scope'].append(f"临时假设：{text(r['label'])} · {r['at']} · {yuan(r['amount_fen'])}")
        card['charts']=[chart('未来可用现金 · 估计','line',[{'name':label,'points':[{'label':p['date'],'value_fen':p[key]} for p in d['timeline']]} for key,label in [('planned_balance_fen','仅已知计划'),('baseline_balance_fen','基准估计'),('scenario_balance_fen','假设情景')]])]
        card['rows']=[row(r['label'],when(r['at']),{'subscription_plan':'订阅计划','scheduled_transfer':'已安排转账','salary_estimate':'工资估计','recurring_estimate':'历史模式估计','recurring_plan':'本人周期计划','scenario':'临时假设'}[r['source']]+(' · 情景中排除' if r['excluded_in_scenario'] else ''),[metric('收支金额',r['amount_fen'])]) for r in d['events']]
        card['total']=d.get('events_total',len(d['events']));card['has_more']=bool(d.get('events_has_more'))
        card['notices'].append('预测不代表实际到账或扣款；情景差额不表示已节省。缺失项目不表示未来为零。')
    elif kind == 'recurring':
        card['target']=target('forecast','recurring')
        for r in d['items']:
            description={'WEEKLY':'每周','MONTHLY':'每月','QUARTERLY':'每季度','YEARLY':'每年'}[r['frequency']]+(' · 周期收入' if r['kind']=='income' else ' · 周期支出')
            description+=' · '+{'manual':'本人手动计划','detected':'历史模式估计','subscription':'银行订阅计划'}[r['source']]
            if r.get('next_expected_at'):description+=' · 下次预计 '+when(r['next_expected_at'])
            card['rows'].append(row(r['merchant'],description,'纳入预测' if r['enabled'] and r['status']=='ACTIVE' else '未纳入预测',[metric('周期金额',r['amount_fen'])],card['target']))
        card['notices'].append('周期记录和纳入预测设置不会执行收支；历史识别不是银行承诺。')
    elif kind == 'forecasts':
        card['target']=target('forecast','history')
        for r in d['items']:
            if r['scope_changed']:
                card['rows'].append(row('预测账户范围已变化','无法直接比较','范围变化'));continue
            card['rows'].append(row(when(r['start_at'])+' — '+when(r['end_at']),'按保存时的范围核对收支净变化'+('' if r['complete'] else '；尚未结束，不计算最终偏差'),'已结束' if r['complete'] else '观察中',metrics(r,[('predicted_net_flow_fen','预计净变化'),('actual_net_flow_fen','已发生净变化')]+([('error_fen','最终偏差')] if r['complete'] else [])),card['target']))
    elif kind == 'catalog':
        card['target']=target('organize','merchants' if d['section'] in {'merchants','merchant_history'} else 'rules')
        for r in d['items']:
            description=''
            if 'aliases' in r:description='别名：'+('、'.join(text(a) for a in r['aliases']) or '无')
            if 'transaction_count' in r:description+=f" · 关联 {r['transaction_count']} 笔流水"
            if 'category' in r:description+=' · 分类：'+CATEGORIES[r['category']]
            card['rows'].append(row(r.get('name') or (r.get('source_name') or '原商户')+' → '+(r.get('target_name') or '目标商户'),description,target=card['target']))
    else:
        card['target']=target('organize','merchants')
        card['rows']=[row('商户归并' if d['mode']=='merge' else '商户拆分','请在工作台核对完整范围','可继续预览' if d['allowed'] else '当前范围不允许变更',[metric('涉及流水数',value=d.get('transaction_count','待核实'))])]
        card['notices'].append('这是只读预览，尚未修改商户归属。实际修改需本人确认。')
    if not card['summary']:
        count=card['total'] if card['total'] is not None else len(card['rows'])
        card['summary']=f'本次查询返回 {len(card["rows"])} 项，范围内共 {count} 项。' if card['rows'] or kind!='cashflow' else '按已知计划与历史估计分别展示未来现金流。'
    if not card['rows'] and not card['charts']:
        card['notices'].append('本次范围内暂无匹配记录，不代表其他范围也没有。')
    return card


def build_cards(reply, request_id):
    from ..ai.grounding import collect, evidence_for
    trace=reply.get('tool_calls',[])
    try:
        selected={e.evidence_id for e in collect(trace)}
    except (KeyError,TypeError,ValueError):
        return []
    cards=[]
    for index,item in enumerate(trace):
        if item.get('name') not in TOOLS:continue
        evidence=evidence_for(index,item)
        if not evidence or evidence.evidence_id not in selected or evidence.outcome!='success':continue
        try:
            card=build(item,evidence,request_id)
            card['position']=index
            card['result_id']='BR-'+hashlib.sha256((request_id+evidence.content_hash).encode()).hexdigest()[:32]
            cards.append(BillResultCard.model_validate(card).model_dump(mode='json'))
        except (KeyError,TypeError,ValueError,ValidationError):
            logger.warning('Bill result presentation unavailable tool=%s',item.get('name'))
    compact=bool(cards) and len(cards)==len(selected) and all(item.get('name') in TOOLS for item in trace) and reply.get('grounding',{}).get('status')=='grounded' and not reply.get('execution_queue') and not reply.get('goal_plan')
    for card in cards:card['compact_reply']=compact
    return cards


async def owned(db,user_id,session_id):
    from ...models.nova_conversation import NovaConversationORM
    owner=await db.get(NovaConversationORM,session_id)
    if owner is None or owner.user_id!=user_id:raise conversations.ConversationError()


async def save(db,user_id,session_id,request_id,reply):
    await owned(db,user_id,session_id)
    if not request_id:return []
    cards=build_cards(reply,request_id)
    captured_at=datetime.now(timezone.utc)
    for card in cards:
        # Scope identity participates in the key; identical requests in other
        # conversations/users can never overwrite or expose another snapshot.
        key='BR-'+hashlib.sha256((user_id+':'+session_id+':'+card['result_id']).encode()).hexdigest()[:32]
        stored=await db.get(Snapshot,key)
        if stored:
            card.update(json.loads(conversations._decrypt(stored.payload_ciphertext)))
            continue
        card['result_id']=key
        db.add(Snapshot(result_id=key,user_id=user_id,session_id=session_id,request_id=request_id,created_at=captured_at,
            payload_ciphertext=conversations._encrypt(json.dumps(card,ensure_ascii=False))))
    await db.flush()
    return cards


async def cards(db,user_id,session_id):
    await owned(db,user_id,session_id)
    rows=(await db.scalars(select(Snapshot).where(Snapshot.user_id==user_id,Snapshot.session_id==session_id)
        .order_by(Snapshot.created_at,Snapshot.result_id))).all()
    decoded=[(row.created_at,BillResultCard.model_validate_json(conversations._decrypt(row.payload_ciphertext)).model_dump(mode='json')) for row in rows]
    return [card for _,card in sorted(decoded,key=lambda pair:(pair[0],pair[1]['position'],pair[1]['result_id']))]
