"""Versioned multi-turn orchestration; models have only read/preview capabilities."""
import os
import re
from datetime import datetime,timezone,timedelta
from decimal import Decimal,ROUND_DOWN
from uuid import uuid4
from sqlalchemy import select,update
from ...models.wealth import WealthConversationORM
from ...models.wealth_research import WealthChatRequestORM
from ...domain.wealth_synthetic import fingerprint
from ...integrations.wealth_authorization import WealthPolicyError,lock_profile,require_suitability
from ...schemas.wealth import InvestmentCommand,PlanInput
from ..orchestration import wealth_plan
from ...domain.wealth_allocation import candidates
from .wealth_model import Intent,understand,explain
from ...domain.wealth_requirements import apply_frame, planning_slots


def resolve(ref: str,products: list[dict],candidate_ids: list[str]) -> str:
    if ref in {p['product_id'] for p in products}: return ref
    ordinal=re.fullmatch(r'(?:第)?([一二三四五六七八九十0-9]+)(?:个|只|款)?',ref)
    if ordinal:
        s=ordinal[1]; n=int(s) if s.isdigit() else {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}.get(s,0)
        if 0<n<=len(candidate_ids) and candidate_ids[n-1] in {p['product_id'] for p in products}: return candidate_ids[n-1]
        raise ValueError('候选列表已变化或序号无效，请明确产品')
    matches=[p['product_id'] for p in products if ref and (ref==p['name'] or ref in p['name'])]
    if len(matches)!=1: raise ValueError('产品名称存在歧义或无匹配，请明确产品ID或份额类别')
    return matches[0]


def card_list(data: dict, kind: str, items: list[str], response: dict) -> None:
    """Keep each rendered ordering immutable; newer cards supersede only the same kind."""
    snapshots = list(data.get('card_lists', []))
    snapshot = {'list_id': 'CL'+uuid4().hex, 'kind': kind, 'items': list(items),
                'context_id': data.get('context_id', 'legacy-v1')}
    data['card_lists'] = snapshots + [snapshot]
    response['card_list'] = snapshot


def ordinal_candidates(data: dict, text: str) -> list[list[str]]:
    latest = {}
    for snapshot in data.get('card_lists', []):
        if snapshot['context_id'] == data.get('context_id', 'legacy-v1'):
            latest[snapshot['kind']] = snapshot['items']
    if '方案' in text:
        latest = {key: value for key, value in latest.items() if key == 'plan'}
    elif '搜索' in text or '目录' in text:
        latest = {key: value for key, value in latest.items() if key == 'directory'}
    elif '候选' in text:
        latest = {key: value for key, value in latest.items() if key == 'products'}
    return list(latest.values())


def offline(text: str) -> Intent:
    from .wealth import parse_money
    refs=re.findall(r'(?<![A-Z0-9])(?:[SB]\d{3}|M[WF]\d{3}|R\d{6})(?![A-Z0-9])',text.upper())
    refs+=re.findall(r'第[一二三四五六七八九十0-9]+个',text)
    money=r'([0-9]+(?:\.\d{1,2})?[百千万]?|[一二两三四五六七八九十][百千万]?)'
    def match(prefix):
        m=re.search(prefix+money,text); return parse_money(m[1]) if m else None
    amount=match(r'(?:买|申购|金额|改成|改为)\s*'); budget=match(r'(?:有|预算|投入)\s*'); reserve=match(r'(?:留|保留)\s*')
    budget_range=re.search(r'(?:预算|投入)\s*'+money+r'\s*(?:到|至|~|—|-)\s*'+money,text)
    lower=parse_money(budget_range[1]) if budget_range else None
    upper=parse_money(budget_range[2]) if budget_range else None
    if budget_range:budget=None
    suggest=bool(re.search(r'(?:建议|推荐).*(?:金额|投入多少)|(?:金额|投入多少).*(?:建议|推荐)',text))
    days=re.search(r'(\d+)天',text); months=re.search(r'(\d+)个月',text)
    need='cash' if re.search('备用|应急|短期',text) else 'growth' if '增长' in text else 'balanced' if re.search('稳健|均衡|平衡',text) else None
    horizon=int(days[1]) if days else int(months[1])*30 if months else None
    intent='clarify'
    if re.search('搜索|查找|查询基金',text): intent='search'
    elif re.search('到账|订单|进度|成交|成功了吗',text): intent='status'
    elif re.search('赎回|快赎|取出|卖出',text): intent='redeem'
    elif re.search('对比|比较',text): intent='compare'
    elif re.search('改|排除|不要|保留',text): intent='modify'
    elif re.search('先别买|暂停|先停',text): intent='pause'
    elif re.search('继续刚才|恢复任务',text): intent='resume'
    elif re.search('返回方案|重新选',text): intent='rollback'
    elif re.search('人工|接管',text): intent='handoff'
    elif '取消任务' in text: intent='cancel_task'
    elif re.search('买|申购',text): intent='purchase'
    elif budget is not None or budget_range or suggest or horizon or need or '配置' in text or text.strip()=='采用建议金额': intent='allocate'
    elif '推荐' in text: intent='recommend'
    elif re.search('持仓|资产',text): intent='assets'
    elif '产品' in text and not re.search('什么|为什么|意思',text): intent='products'
    elif re.search('什么|为什么|风险|费用|赎回费|区别|年化|PMI|CPI|PPI|LPR|M1|M2|分红',text,re.I): intent='knowledge'
    elif text.strip() in {'继续','好的','确认','执行','同意'}: intent='clarify' if text.strip()=='继续' else 'refuse'
    return Intent(intent=intent,control_after='pause' if intent=='modify' and re.search('先别买|暂停|先停',text) else None,
        redemption_mode='quick' if '快赎' in text else None,budget_min_fen=lower,budget_max_fen=upper,suggest_budget=suggest,
        sell_then_buy=bool(re.search(r'(?:卖|赎回|快赎).*(?:再买|再申购|再配置)',text)),
        search_query=re.sub(r'^(?:搜索|查找|查询基金)\s*','',text) if intent=='search' else None,product_refs=refs,amount_fen=amount,budget_fen=budget,reserve_fen=reserve,horizon_days=horizon,need=need,
        fraction='half' if '一半' in text else 'all' if '全部' in text else None,exclude_refs=refs if re.search('排除|不要',text) else [])


async def claim(db,user_id,client_id,digest):
    from sqlalchemy.dialects.sqlite import insert as si
    from sqlalchemy.dialects.postgresql import insert as pi
    key=user_id+':'+client_id
    insert=si if db.get_bind().dialect.name=='sqlite' else pi
    inserted=await db.execute(insert(WealthChatRequestORM).values(request_key=key,user_id=user_id,digest=digest,status='PROCESSING',response={},started_at=datetime.now(timezone.utc)).on_conflict_do_nothing(index_elements=['request_key']))
    await db.commit()
    row=await db.get(WealthChatRequestORM,key)
    if row.digest!=digest: raise WealthPolicyError('消息编号对应不同内容','IDEMPOTENCY_CONFLICT',409)
    if inserted.rowcount!=1: raise WealthPolicyError('消息已经被处理，请以原消息编号查询','MESSAGE_PENDING',409)
    return row


async def action(db,user_id,bank,text,intent,data,products,facts,contexts,mode):
    # 明确只读资产短句由服务端绑定到银行事实，不被历史订单意图劫持。
    if re.fullmatch(r'(?:查一下|查询|查看|查)(?:我的|本人|当前)?(?:持仓|资产|余额|账户)(?:资产|情况)?[。？?]?',text.strip()):
        intent=intent.model_copy(update={'intent':'assets'})
    elif intent.intent=='clarify' and re.search(r'是什么|是多少|为什么|为何',text):
        # 未识别的信息问题只走带来源的只读知识检索，资料不足不编造。
        intent=intent.model_copy(update={'intent':'knowledge'})
    if data.get('awaiting_plan_details') and re.match(r'^继续(?:刚才|原来|原任务|配置|$)',text.strip()) and intent.intent in {'clarify','resume','allocate'}:
        intent=intent.model_copy(update={'intent':'allocate'})
    if text.strip()=='采用建议金额' and data.get('budget_proposal'):
        intent=intent.model_copy(update={'intent':'allocate'})
    response={'mode':mode,'kind':'clarification','message':'请说明要查询资产、了解产品还是规划现有资金；我会读取银行资金事实，交易仍需在可信卡片确认。'}
    if intent.intent in {'pause','resume','rollback','handoff','cancel_task'}:
        prior=data.get('active_task')
        if not prior:
            response['message']='请先选择要控制的交易任务；取消任务与撤销银行订单不同。'
            return response
        from ..orchestration import wealth_task
        task=await wealth_task.control(db,user_id,prior['task_id'],
            'cancel' if intent.intent=='cancel_task' else intent.intent,
            {'expected_version':prior['version'],'request_id':'chat-control-'+uuid4().hex,'reason':text[:1000]})
        data['active_task']={'task_id':task['task_id'],'version':task['version']}
        response.update(kind='task',task=task,message='已保存任务控制状态；已受理或完成的银行订单保留原号核对，不回退账务。')
        return response
    stated_ordinals = re.findall(r'第[一二三四五六七八九十0-9]+(?:个|只|款)', text)
    ordinal = bool(stated_ordinals) or any(re.fullmatch(r'(?:第)?[一二三四五六七八九十0-9]+(?:个|只|款)?', ref) for ref in intent.product_refs)
    lists = ordinal_candidates(data, text) if ordinal else []
    if ordinal and (len(lists) > 1 or (not lists and data.get('research_candidates') and data.get('candidates'))):
        response['message']='当前有多个卡片列表，序号有歧义。请明确“方案／候选／目录中的第几个”或产品ID再继续。'
        return response
    codes = re.findall(r'(?<![0-9])([0-9]{6})(?![0-9])', text)
    if intent.intent == 'compare' and max(len(set(codes)), len(intent.product_refs)) > 4:
        response['message'] = '一次最多比较4项，请选择产品或分组比较。'
        return response
    if intent.intent == 'compare':
        outside = list(dict.fromkeys(codes + [r[1:] for r in intent.product_refs if re.fullmatch(r'R\d{6}', r)]))
        if not outside and data.get('research_candidates') and (not lists or all(pid.startswith('R') for pid in lists[0])):
            for ref in intent.product_refs:
                pid = resolve(ref, [{'product_id':'R'+c, 'name':c} for c in data['research_candidates']], ['R'+c for c in data['research_candidates']])
                outside.append(pid[1:])
        if len(outside) >= 2:
            analysis = await bank.call('POST', '/funds/compare', {'subject_user_id':user_id, 'codes':outside})
            response.update(kind='research', research_comparison=analysis,
                message='真实基金缓存对比；共同观察点 '+str(analysis['analysis']['common_samples'])+'。'+
                '\n'.join(p['name']+'：区间涨跌 '+str(p['metrics']['return'])+'，最大回撤 '+str(p['metrics']['max_drawdown']) for p in analysis['analysis']['products'])+
                '\n'+analysis['notice'])
            return response
    if intent.intent == 'search' or (codes and intent.intent in {'products', 'knowledge'}):
        query = intent.search_query or (codes[0] if codes else text)
        found = await bank.call('POST', '/funds/search', {'subject_user_id': user_id, 'query': query, 'page_size': 8})
        rows = found['items']; data['research_candidates'] = [r['code'] for r in rows]
        card_list(data, 'directory', ['R'+row['code'] for row in rows], response)
        data['research_candidate_names'] = [{'id':'R'+r['code'],'name':r['name']} for r in rows]
        detail = await bank.call('POST', '/funds/detail', {'subject_user_id': user_id, 'code': rows[0]['code']}) if len(rows) == 1 else None
        response.update(kind='research', directory=found, fund_detail=detail,
            message='真实基金目录匹配 '+str(found['total'])+' 条。'+ '\n'.join(r['code']+' '+r['name']+' · '+r['fund_type']+' · 真实基金数据' for r in rows)+
            ('\n详情缓存：'+', '.join(k+'='+v['status'] for k,v in detail['evidence'].items()) if detail else '')+
            '\n仅研究；目录/净值不等于交易规则完整，不生成未审核订单。详情在后台获取，失败保留旧证据，不返回模拟替代值。')
        return response
    candidate_ids=lists[0] if len(lists) == 1 else data.get('candidates',[])
    active=data.get('active_command')
    # A single active order plus an unqualified amount correction modifies that order.
    # Models occasionally label it as budget; bind it to the explicit active action,
    # never reinterpret an explicitly stated plan budget or reserve.
    if intent.semantic is None and active and intent.intent=='modify' and intent.amount_fen is None and intent.budget_fen is not None and re.search('改成|改为',text) and not re.search('预算|配置|保留|留现金',text):
        intent=intent.model_copy(update={'amount_fen':intent.budget_fen,'budget_fen':None})
    product_refs=stated_ordinals if stated_ordinals and not re.search(r'(?<![A-Z0-9])(?:[SB]\d{3}|M[WF]\d{3}|R\d{6})(?![A-Z0-9])',text,re.I) else intent.product_refs
    if intent.intent=='redeem' and not active and any(r in {'它','这个','这笔','刚才那笔'} for r in product_refs):
        holdings=data.get('holding_cards',[])
        if len(holdings)!=1:
            response.update(kind='assets',assets=facts,message='持仓指代不唯一，请明确产品和账户。');return response
        product_refs=[holdings[0]['product_id']];data['account_id']=holdings[0]['account_id']
    refs=[active['product_id'] if r in {'它','这个','这笔','刚才那个','刚才那笔'} and active else resolve(r,products,candidate_ids) for r in product_refs]
    if intent.intent=='redeem' and not refs and not active:
        holdings=data.get('holding_cards',[])
        if len(holdings)!=1:
            response.update(kind='assets',assets=facts,message='请明确当前持仓卡中的产品和账户；多笔持仓不能猜测“这笔”。')
            return response
        refs=[holdings[0]['product_id']];data['account_id']=holdings[0]['account_id']
    slots=dict(data.get('pending') or {})
    # A new planning request is not a revision of a completed/abandoned budget.
    if intent.intent == 'allocate' and not data.get('active_plan') and not data.get('awaiting_plan_details'):
        slots = {}
    if text.strip()=='采用建议金额' and data.get('budget_proposal'):
        proposal=data['budget_proposal']
        if proposal['context_id']!=data.get('context_id','legacy-v1'):
            response['message']='资金上下文已改变，请重新查询并建议投入金额。';return response
        slots['budget_fen']=proposal['amount_fen'];slots['budget_source']='user_accepted_suggestion'
        data.pop('budget_proposal',None)
    if intent.suggest_budget:slots['suggest_budget']=True
    if intent.budget_min_fen is not None or intent.budget_max_fen is not None:
        if intent.budget_min_fen is None or intent.budget_max_fen is None or intent.budget_min_fen>intent.budget_max_fen:
            response['message']='请提供有效的投入金额下限和上限。';return response
        slots.pop('budget_fen',None)
        slots['budget_range']=[intent.budget_min_fen,intent.budget_max_fen]
    for name in ('budget_fen','reserve_fen','horizon_days','need'):
        if getattr(intent,name) is not None and (intent.semantic is None or name=='need'): slots[name]=getattr(intent,name)
    excluded=list(slots.get('excluded_products',[]))
    for ref in intent.exclude_refs:
        pid=resolve(ref,products,candidate_ids)
        if pid not in excluded: excluded.append(pid)
    slots['excluded_products']=excluded; data['pending']=slots
    accounts=[a for a in facts['cash'] if a['account_type']!='credit']
    if intent.account_ref:
        aliases={f'账户{i}':a['account_id'] for i,a in enumerate(accounts,1)}
        selected_account=aliases.get(intent.account_ref) or next((a['account_id'] for a in accounts if a['account_id']==intent.account_ref),None)
        if selected_account: data['account_id']=selected_account
        else: response['message']='请从当前资金上下文的付款账户选择，不支持其他用户账户。'; return response
    account=next((a for a in accounts if a['account_id']==data.get('account_id')),accounts[0] if len(accounts)==1 else None)
    if intent.semantic:
        previous_categories=data.get('requirements',{}).get('fields',{}).get('excluded_categories',{}).get('value',[])
        previous_category_ids={p['product_id'] for p in products if p['category'] in previous_categories}
        state=apply_frame(data.get('requirements'),intent.semantic,message=text,
            message_id=data.get('_message_id',uuid4().hex),session_id=data['_session_id'],
            bank_facts=facts,account=account)
        data['requirements']=state
        slots=planning_slots(state,slots)
        excluded=[pid for pid in slots.get('excluded_products',[]) if pid not in previous_category_ids]
        categories=state['fields'].get('excluded_categories',{}).get('value',[])
        excluded=list(dict.fromkeys(excluded+[p['product_id'] for p in products if p['category'] in categories]))
        slots['excluded_products']=excluded;data['pending']=slots
        response['requirement_card']=state
        if state['ambiguities'] and intent.intent not in {'products','search','compare','knowledge','assets','status'}:
            data['last_question']={'field':'ambiguity','text':state['ambiguities'][0]}
            response['message']=state['ambiguities'][0]
            return response
        semantic_actions=[i.action for i in intent.semantic.intents]
        changed=set(state['changed_fields'])
        if ('plan' in semantic_actions or (data.get('awaiting_plan_details') and changed)) and intent.intent not in {'purchase','redeem','assets','status','pause','resume','handoff','cancel_task'}:
            intent=intent.model_copy(update={'intent':'allocate'})
        # Planning changes never revise an unrelated pending single-product order.
        if intent.intent=='allocate':
            data['active_command']=None
    if len(accounts)>1 and account is None and intent.intent in {'allocate','purchase','redeem','modify'}:
        response['message']='有多个可交易账户，请明确'+ '、'.join(f'账户{i}' for i in range(1,len(accounts)+1)); return response
    dataset=next((p.get('dataset_key') for p in products if p.get('dataset_key') and p['dataset_key']!='legacy-v1'),None)
    if intent.intent=='refuse': response['message']='口头确认不会授权或成交，请核对可信预览、本人确认并点击模拟高风险验证按钮。'
    elif intent.intent=='assets':
        data['holding_cards']=[{key: lot[key] for key in ('lot_id','product_id','account_id','redeemable_units')}
            for lot in facts['lots'] if Decimal(lot['redeemable_units'])>0]
        response.update(kind='assets',assets=facts,message='以下为银行事实；占款、锁定份额、在途和赎回应收分开，不构成即时可用资金。')
    elif intent.intent=='status':
        operation=intent.operation_id or (active or {}).get('operation_id')
        if not operation and len(facts['orders']) == 1: operation=facts['orders'][0]['operation_id']
        if operation:
            result=await bank.query(user_id,operation)
            response.update(kind='order',order=result,message='原操作号 '+operation+' 的银行状态：'+result['status']+'；受理不等于已结算或到账。')
        else: response.update(kind='assets', assets=facts, message='请从本人订单卡选择要查询的原操作号，多笔订单不能猜测“刚才那笔”。')
    elif intent.intent in {'products','recommend'}:
        if intent.intent=='recommend':
            if not slots.get('horizon_days'):
                response.update(kind='products',products=products[:8],message='先展示产品资料；使用期限尚未明确，未生成个性化配置或交易。')
                return response
            suitable=await require_suitability(db,user_id,1,allow_cash_only=True); await db.rollback()
            rows=candidates(products,suitable['capacity_level'],slots['horizon_days'],excluded)[:5] if suitable['purchase_allowed'] else []
        else: rows=products[:12]
        data['candidates']=[p['product_id'] for p in rows]; data['candidate_dataset']=dataset
        card_list(data, 'products', data['candidates'], response)
        response.update(kind='products',products=rows,message='以下候选顺序已保存，支持“第几个”；产品风险、费用和净值来自银行。均为模拟交易，不保证回报。')
    elif intent.intent=='compare':
        if not 2 <= len(refs) <= 4: response['message']='请选择2至4个明确产品，不能用模糊家族名称。'
        else:
            research=await bank.call('POST','/research',{'subject_user_id':user_id,'dataset_key':dataset,'product_ids':refs}) if dataset else None
            response.update(kind='comparison',products=[p for pid in refs for p in products if p['product_id']==pid],analysis=research.get('analysis') if research else None,message='共同观察窗口对比，A/C归同一家族；未核实分红不标为总回报，缺失指标不补零。')
    elif intent.intent=='knowledge':
        result=await bank.call('POST','/knowledge',{'subject_user_id':user_id,'dataset_key':dataset,'query':text})
        citations=result['citations']
        if mode=='model': answer,citations,evidence=await explain(text,citations); response['explanation_model']=evidence
        else: answer='\n'.join(r['excerpt'] for r in citations) if citations else '资料不足，无法提供有来源的结论。'
        response.update(kind='knowledge',message=answer,citations=citations)
    elif intent.intent=='modify' and refs and intent.amount_fen is not None and data.get('active_plan'):
        from ...schemas.wealth import AdoptPlan
        from ..orchestration import wealth_task
        previous=data['active_plan']; row=await wealth_plan.owned_plan(db,user_id,previous['plan_id'])
        targets=[item for item in row.data['items'] if item['command']['product_id']==refs[0]]
        if len(targets)!=1 or targets[0]['command']['direction']!='purchase':
            response['message']='请明确唯一未提交的申购项；赎回需确认份额而非猜测到账金额。';return response
        selections=[{'operation_id':item['command']['operation_id'],
            **({'amount_fen':intent.amount_fen} if item is targets[0] else {})} for item in row.data['items']]
        plan=await wealth_plan.adopt(db,user_id,row.plan_id,AdoptPlan(version=row.version,items=selections))
        task_row=await wealth_task.linked_plan_task(db,user_id,plan['plan_id']); task=await wealth_task.view(db,task_row)
        if intent.control_after=='pause':
            task=await wealth_task.control(db,user_id,task['task_id'],'pause',{
                'request_id':'plan-pause-'+uuid4().hex,'expected_version':task['version'],'reason':'本人修改后先别买'})
        data['active_plan']={'plan_id':plan['plan_id'],'version':plan['version']}
        card_list(data, 'plan', [item['command']['product_id'] for item in plan['items']], response)
        data['active_task']={'task_id':task['task_id'],'version':task['version']}
        response.update(kind='plan',plan=plan,task=task,message='已更新未提交方案并使旧确认失效；修改后暂停状态已保留，不自动成交。')
    elif intent.intent in {'purchase','redeem','modify'} and (refs or active) and (intent.amount_fen is not None or intent.intent in {'purchase','redeem'}):
        pid=refs[0] if refs else active['product_id']; product=next(p for p in products if p['product_id']==pid)
        direction='redeem' if intent.intent=='redeem' else active['direction'] if intent.intent=='modify' and active else 'purchase'
        if not account: response['message']='没有当前资金上下文的可交易账户。'; return response
        amount=intent.amount_fen or (active or {}).get('amount_fen')
        quantity=intent.units
        if direction=='redeem':
            lots=[lot for lot in facts['lots'] if lot['product_id']==pid and lot['account_id']==account['account_id']]
            if intent.fraction and len(lots) > 1 and not re.search('合并|全部持仓|所有批次|总份额', text):
                response.update(kind='assets',assets=facts,message='此产品有多笔持仓。“一半／全部”指哪个范围？请明确是否按该账户的合并总份额赎回；银行按FIFO分配批次。')
                return response
            total=sum((Decimal(lot['redeemable_units']) for lot in lots),Decimal(0))
            if intent.fraction: quantity=format((total/(2 if intent.fraction=='half' else 1)).quantize(Decimal('0.0001'),ROUND_DOWN),'.4f')
            if total<=0: response['message']='暂无可赎回份额，可能仍在计价、被占用或锁定；先核对订单。'; return response
            if not quantity or Decimal(quantity)<=0:
                response.update(kind='assets',assets=facts,message='请明确要赎回的份额或“一半/全部”；“一部分”不能自动换算为份额。');return response
        elif not amount: response['message']='请明确申购金额，不自动使用全部余额。'; return response
        command=InvestmentCommand(operation_id='WO'+uuid4().hex,direction=direction,product_id=pid,product_version=product['version'],disclosure_version=product['disclosure']['version'],account_id=account['account_id'],amount_fen=amount if direction=='purchase' else 0,units=quantity or '0.0000')
        # Validate hard bank rules before offering a actionable card; no authorization writes.
        if direction=='redeem' and intent.redemption_mode=='quick':
            command=command.model_copy(update={'command_version':2,'redemption_mode':'quick'})
        checked=await bank.precheck(user_id,command)
        command=InvestmentCommand.model_validate(checked['command'])
        from ..orchestration import wealth_task
        prior=data.get('active_task')
        if prior and intent.intent=='modify':
            task=await wealth_task.revise_command(db,user_id,prior['task_id'],prior['version'],command)
        else:
            task=await wealth_task.create_command(db,user_id,data['_session_id'],data.get('context_id','legacy-v1'),command,sell_then_buy=intent.sell_then_buy,requirements=data.get('requirements'))
        if intent.control_after=='pause':
            task=await wealth_task.control(db,user_id,task['task_id'],'pause',{
                'request_id':'compound-'+command.operation_id,'expected_version':task['version'],'reason':'本人修改后先别买'})
        data['active_task']={'task_id':task['task_id'],'version':task['version']}
        response['task']=task
        if active:
            from ...models.wealth import WealthAuthorizationORM
            await db.execute(update(WealthAuthorizationORM).where(WealthAuthorizationORM.user_id==user_id,WealthAuthorizationORM.operation_id==active['operation_id'],WealthAuthorizationORM.status.in_(['AWAITING_CONFIRMATION','AWAITING_MFA','VALID'])).values(status='EXPIRED'))
            await db.commit()
        data['active_command']=command.model_dump(); data['active_plan']=None
        data['pending']={}; data['awaiting_plan_details']=False
        response.update(kind='command',command=command.model_dump(),message='已生成新版本待确认模拟订单；金额修改使用新操作号，旧授权不会用于新参数。请在可信卡片本人确认与模拟验证。')
    elif intent.intent=='allocate' or (intent.intent=='modify' and intent.semantic and intent.semantic.patches) or (intent.intent=='modify' and
            (intent.exclude_refs or any(getattr(intent,k) is not None for k in ('budget_fen','reserve_fen','horizon_days','need')))) or (intent.intent=='clarify' and data.get('awaiting_plan_details') and
            (text.strip()=='继续' or any(getattr(intent,k) is not None for k in ('budget_fen','reserve_fen','horizon_days','need')))):
        data['awaiting_plan_details']=True
        # 银行可用现金是约束事实，不是本人投入意愿。旧会话自动预算需重新确认。
        if slots.get('budget_source') == 'bank_available_cash' and intent.budget_fen is None:
            slots.pop('budget_fen', None)
            slots.pop('budget_source', None)
        if intent.budget_fen is not None:
            slots['budget_source']='user_specified'
            slots.pop('budget_range',None);slots.pop('suggest_budget',None);data.pop('budget_proposal',None)
        data['pending']=slots
        budget_note=(f"已查询银行事实：账户{account['account_id']}可用现金¥{account['available_fen']/100:.2f}，这是可投入上限。" if account else '')
        response['budget_source']=slots.get('budget_source','user_specified')
        fields=data.get('requirements',{}).get('fields',{})
        # Specific expenses are optional; goals/horizon drive suitability and liquidity.
        missing=[key for key in ('budget_fen',) if key not in slots]
        if intent.semantic and 'investment_goal' not in fields and not slots.get('need'):
            missing.append('investment_goal')
        if 'horizon_days' not in slots and 'horizon' not in fields:missing.append('horizon_days')
        if fields.get('investment_goal',{}).get('value') in {'reduce_volatility','preserve_value'} and slots.get('horizon_days',9999)<=30 and 'liquidity_requirement' not in fields:
            missing.append('liquidity_requirement')
        if missing==['budget_fen'] and account and (slots.get('suggest_budget') or slots.get('budget_range')):
            ceiling=max(0,account['available_fen']-(slots.get('reserve_fen') or 0))
            bounds=slots.get('budget_range')
            amount=bounds[0] if bounds else ceiling//2//100*100
            if amount<=0 or amount>ceiling:
                response['message']=budget_note+'保留现金后无法满足该投入范围，请调整金额意愿或保留现金。';return response
            data['budget_proposal']={'amount_fen':amount,'context_id':data.get('context_id','legacy-v1')}
            response.update(budget_proposal=data['budget_proposal'],message=budget_note+f'建议规划投入¥{amount/100:.2f}。'+
                ('采用您给定范围的下限，避免擅自扩大投入。' if bounds else '透明规则：保留现金后剩余可用金额的一半，向下取整到元。')+
                '如接受请回复“采用建议金额”；这仅选择规划预算，不代表交易确认或授权。')
            return response
        if missing:
            key=missing[0]
            proposed=intent.semantic.clarification_field if intent.semantic else None
            proposed={'investment_budget':'budget_fen','horizon':'horizon_days'}.get(proposed,proposed)
            if proposed in missing:key=proposed
            question={'budget_fen':'您愿意投入多少？可以给金额、比例、范围，或让我先建议。',
                'investment_goal':'这笔钱您更希望尽量减少波动、争取增长，还是兼顾两者？',
                'horizon_days':'您大约可以让这笔钱持续投资多久？可以回答随时需要、半年后使用或不确定。',
                'liquidity_requirement':'如果临时需要用钱，您希望立即到账，还是可以等待几个工作日？'}[key]
            data['last_question']={'field':'investment_budget' if key=='budget_fen' else 'horizon','text':question}
            if key in {'investment_goal','liquidity_requirement'}:data['last_question']['field']=key
            response['message']=budget_note+question
            if data.get('requirements'):
                from ..orchestration.wealth_requirement_task import record
                task=await record(db,user_id,data['_session_id'],data.get('context_id','legacy-v1'),
                    account['account_id'] if account else None,data['requirements'],facts,data.get('planning_task_id'))
                data['planning_task_id']=task['task_id'];data['active_task']={'task_id':task['task_id'],'version':task['version']}
                response['task']=task
        elif ('horizon_days' not in slots or any(fields.get(key,{}).get('state') in {'unknown','skipped','unmentioned'}
                for key in ('investment_goal','horizon','liquidity_requirement'))):
            # Unknown/declined conditions produce explicitly conditional research, not executable orders.
            from ...domain.wealth_recommendation import conditional_research
            suitable=await require_suitability(db,user_id,1,allow_cash_only=True);await db.rollback()
            recommendation=conditional_research({'products':products,'constraints':fields,'capacity':suitable['capacity_level'],
                'horizon_days':slots.get('horizon_days'),'excluded':excluded,'as_of':facts['business_at'],
                'available_cash_fen':account['available_fen'] if account else None,
                'data_versions':{p['product_id']:p['version'] for p in products}})
            rows=[next(p for p in products if p['product_id']==item['product_id']) for item in recommendation['items']]
            data['candidates']=[p['product_id'] for p in rows];card_list(data,'products',data['candidates'],response)
            data.pop('last_question',None)
            response.update(kind='products',products=rows,conditional=True,
                recommendation= recommendation,
                message='部分目标、期限或到账要求尚未确定：以下仅为条件候选资料，不生成可执行配置。请结合产品持有期及到账规则判断；可以随时补充条件，不会循环追问。')
            from ..orchestration.wealth_requirement_task import record
            task=await record(db,user_id,data['_session_id'],data.get('context_id','legacy-v1'),
                account['account_id'] if account else None,data['requirements'],facts,data.get('planning_task_id'),
                waiting_reason=None,result=recommendation)
            response['task']=task;data['planning_task_id']=None
        elif account:
            request=PlanInput(account_id=account['account_id'],budget_fen=slots['budget_fen'],reserve_fen=slots.get('reserve_fen'),horizon_days=slots['horizon_days'],excluded_products=excluded,need=slots.get('need'),constraint_states=slots.get('constraint_states',{})).model_dump()
            request['budget_source']=slots.get('budget_source','user_specified')
            request['requirement_task_id']=data.get('planning_task_id')
            request['requirement_version']=data.get('requirements',{}).get('version')
            previous=data.get('active_plan')
            if previous: request['version']=previous['version']
            if data.get('active_task') and not previous:
                from ..orchestration import wealth_task
                current=await wealth_task.owned(db,user_id,data['active_task']['task_id'])
                current_view=await wealth_task.view(db,current)
                if current.data.get('goal')=='sell_then_buy' and not any(n['kind']=='REREAD_CASH' and n['status']=='COMPLETED' for n in current_view['nodes']):
                    raise WealthPolicyError('须先按原号确认赎回实际到账并重读可用现金，不能用预计资金买入','TASK_DEPENDENCY_PENDING',409)
                if current.data.get('goal')=='sell_then_buy': request['parent_task_id']=current.task_id
            plan=await wealth_plan.create(db,user_id,bank,request,previous['plan_id'] if previous else None)
            from ..orchestration import wealth_task
            task=await wealth_task.register_plan(db,user_id,data['_session_id'],data.get('context_id','legacy-v1'),plan)
            data['active_task']={'task_id':task['task_id'],'version':task['version']}
            response['task']=task
            data['active_plan']={'plan_id':plan['plan_id'],'version':plan['version']}; data['active_command']=None
            data['awaiting_plan_details']=False
            data.pop('last_question',None)
            data['candidates']=[i['command']['product_id'] for i in plan['items']]
            card_list(data, 'plan', data['candidates'], response)
            explanation = ('已保存新版本配置方案，默认最多3笔；保留现金和不足最低金额的尾款不强制投入。统一预览后本人确认和模拟验证。'
                if plan['items'] else '当前评估、期限及产品规则下未生成交易项；本版本未生成订单，资金保留为现金。可以继续查看产品资料或调整需求。')
            response.update(kind='plan',plan=plan,message=budget_note+explanation)
    return response


async def message(db,user_id,bank,text,session_id,version,client_message_id,*,parsed_intent: Intent | None=None):
    mode='typed' if parsed_intent is not None else os.getenv('WEALTH_NLU_MODE','offline')
    if mode not in {'offline','model','typed'}: raise ValueError('WEALTH_NLU_MODE_INVALID')
    # Never send credentials or bypass requests to a model. Known fund codes remain valid references.
    if re.search(r'密码|验证码|TOTP|口令|sk-[A-Za-z0-9]|绕过|忽略.*(?:规则|风控|授权)|代答|替我.*评估|提高.*等级',text,re.I):
        return {'mode':mode,'kind':'refusal','message':'请使用可信组件；对话不接收凭据，不代答评估、不绕过授权。'}
    key=user_id+':'+client_message_id
    digest=fingerprint([text,session_id,version,parsed_intent.model_dump() if parsed_intent else None])
    existing=await db.get(WealthChatRequestORM,key)
    if existing:
        if existing.digest!=digest: raise WealthPolicyError('消息编号对应不同内容','IDEMPOTENCY_CONFLICT',409)
        if existing.status=='DONE': return existing.response
        if datetime.now(timezone.utc)-existing.started_at.replace(tzinfo=timezone.utc)<timedelta(seconds=180): raise WealthPolicyError('消息仍在处理中，请以原消息编号重试','MESSAGE_PENDING',409)
        await db.delete(existing); await db.commit()
    session=await db.get(WealthConversationORM,session_id) if session_id else None
    if session_id and (session is None or session.user_id!=user_id): raise WealthPolicyError('会话不存在','NOT_FOUND',404)
    if session and session.data.get('legacy_read_only'):
        raise WealthPolicyError('历史来源归档只读，请使用当前会话','ARCHIVED_HISTORY',409)
    if session and session.version!=version: raise WealthPolicyError('会话已更新，请刷新','STALE_PLAN',409)
    sid=session_id or 'WC'+uuid4().hex
    data=dict(session.data) if session else {'history':[],'pending':{},'candidates':[]}
    data['_session_id']=sid
    data['_message_id']=client_message_id
    await db.rollback()
    row=await claim(db,user_id,client_message_id,digest)
    evidence={}
    try:
        products,facts=await bank.products(user_id),await bank.assets(user_id)
        contexts=await bank.call('POST','/contexts',{'subject_user_id':user_id})
        if data.get('context_id') and data['context_id']!=contexts['selected']:
            raise WealthPolicyError('会话属于另一资金范围，请载入当前轮次会话','STALE_CONTEXT',409)
        data['context_id']=contexts['selected']
        if data.get('active_task'):
            from ..orchestration import wealth_task
            task=await wealth_task.owned(db,user_id,data['active_task']['task_id'])
            if task.status in wealth_task.TERMINAL:
                data.update(active_task=None,active_command=None)
            else:
                data['active_task']={'task_id':task.task_id,'version':task.version}
            await db.rollback()
        if data.get('active_plan'):
            from ...models.wealth import WealthPlanORM
            previous=await db.get(WealthPlanORM,data['active_plan']['plan_id'])
            if previous is None or previous.user_id!=user_id or previous.status in {'COMPLETED','EXPIRED','CANCELLED'}:
                data.update(pending={},active_plan=None,awaiting_plan_details=False)
            elif previous.status in {'EXECUTING','WAITING_SETTLEMENT'}:
                data.update(pending={},awaiting_plan_details=False)
            await db.rollback()
        elif not data.get('awaiting_plan_details'):
            # Also repairs pre-fix persisted sessions without deleting their history/orders.
            pending=data.get('pending') or {}
            if all(k in pending for k in ('budget_fen','reserve_fen','horizon_days','need')):
                data['pending']={}
        evidence={}
        if mode=='model':
            plan_context=None
            if data.get('active_plan'):
                from ...models.wealth import WealthPlanORM
                current_plan=await db.get(WealthPlanORM,data['active_plan']['plan_id'])
                if current_plan and current_plan.user_id==user_id:
                    plan_context={'plan_id':current_plan.plan_id,'version':current_plan.version,
                        'items':[{'ordinal':i,'product_id':item['command']['product_id'],
                            'direction':item['command']['direction'],'amount_fen':item['command']['amount_fen'],
                            'units':item['command']['units']} for i,item in enumerate(current_plan.data['items'],1)]}
                await db.rollback()
            context={'awaiting_plan_details':data.get('awaiting_plan_details',False),'holding_cards':data.get('holding_cards',[]),'research_candidates':data.get('research_candidate_names',[]),'pending':data.get('pending',{}),'candidates':[{'id':p['product_id'],'name':p['name']} for pid in data.get('candidates',[]) for p in products if p['product_id']==pid],
                'products':[{'id':p['product_id'],'name':p['name']} for p in products], 'active':{'direction':data['active_command']['direction'],'product_id':data['active_command']['product_id'],'amount_fen':data['active_command']['amount_fen']} if data.get('active_command') else None,
                'accounts':[f'账户{i}' for i,a in enumerate([a for a in facts['cash'] if a['account_type']!='credit'],1)],
                'available_cash_fen':[{'account_ref':f'账户{i}','available_fen':a['available_fen']} for i,a in enumerate([a for a in facts['cash'] if a['account_type']!='credit'],1)],
                'active_plan':plan_context,'recent_turns':[{'user':h['user'],'assistant':h['reply'].get('message','')[:1000]} for h in data.get('history',[])[-6:]],
                'last_question':data.get('last_question'),'requirements':data.get('requirements'),
                'main_task':data.get('active_task'),'card_lists':data.get('card_lists',[])[-4:],
                'pending_orders':[{'operation_id':o['operation_id'],'status':o['status'],'product_id':o['product_id']} for o in facts['orders'][-8:]]}
            intent,evidence=await understand(text,context)
        elif parsed_intent is not None:
            intent=parsed_intent;evidence={'provider':'validated-typed-input','live':False}
        else: intent=offline(text)
        response=await action(db,user_id,bank,text,intent,data,products,facts,contexts,mode)
        response.update(model_evidence=evidence,intent=intent.model_dump(),client_message_id=client_message_id)
    except WealthPolicyError as error:
        await db.rollback(); response={'mode':mode,'kind':'assessment_required' if 'ASSESSMENT' in error.code else 'clarification','code':error.code,'message':error.message}
        if response['kind']=='assessment_required':
            response['pending']={key:value for key,value in data.get('pending',{}).items()
                if key in {'budget_fen','reserve_fen','horizon_days','need','excluded_products','budget_source','budget_range'}}
        if error.code == 'INSUFFICIENT_AVAILABLE_FUNDS':
            data['pending']={k:v for k,v in (data.get('pending') or {}).items() if k!='budget_fen'}
            data['awaiting_plan_details']=True
    except Exception as error:
        from ...integrations.bank_api import BankApiError
        await db.rollback()
        if isinstance(error,BankApiError): response={'mode':mode,'kind':'clarification','code':error.code,'message':error.message}
        elif isinstance(error,ValueError) and not str(error).startswith('MODEL_'): response={'mode':mode,'kind':'clarification','message':str(error)[:200]}
        else:
            from pydantic import ValidationError
            code='MODEL_UNAVAILABLE'
            if isinstance(error,ValidationError):code='MODEL_SCHEMA_INVALID'
            elif isinstance(error,ValueError) and re.fullmatch(r'MODEL_[A-Z0-9_]+',str(error)):code=str(error)
            response={'mode':mode,'kind':'model_unavailable' if mode=='model' else 'clarification','code':code if mode=='model' else 'CLARIFICATION_REQUIRED','message':'模型请求失败，本轮未生成新的交易参数；可重试或使用可信表单。' if mode=='model' else '无法生成预览，请明确参数。'}
    await lock_profile(db,user_id)
    session=await db.get(WealthConversationORM,sid,with_for_update=True,populate_existing=True)
    if session and session.version!=version:
        await db.rollback()
        raise WealthPolicyError('处理期间会话发生变化，请刷新','STALE_PLAN',409)
    if session is None: session=WealthConversationORM(session_id=sid,user_id=user_id,version=1,data={}); db.add(session)
    else: session.version+=1
    response.update(session_id=sid,version=session.version,model_evidence=evidence)
    if data.get('requirements'): response['requirement_card']=data['requirements']
    if 'intent' not in response and 'intent' in locals(): response['intent']=intent.model_dump()
    data['history']=[*data.get('history',[]),{'user':text,'reply':response}]; session.data=data
    row=await db.get(WealthChatRequestORM,key); row.status='DONE'; row.response=response
    await db.commit()
    return response
