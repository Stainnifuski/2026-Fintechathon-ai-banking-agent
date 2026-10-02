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


def offline(text: str) -> Intent:
    from .wealth import parse_money
    refs=re.findall(r'(?<![A-Z0-9])(?:[SB]\d{3}|M[WF]\d{3}|R\d{6})(?![A-Z0-9])',text.upper())
    refs+=re.findall(r'第[一二三四五六七八九十0-9]+个',text)
    money=r'([0-9]+(?:\.\d{1,2})?[百千万]?|[一二两三四五六七八九十][百千万]?)'
    def match(prefix):
        m=re.search(prefix+money,text); return parse_money(m[1]) if m else None
    amount=match(r'(?:买|申购|金额|改成|改为)\s*'); budget=match(r'(?:有|预算|投入)\s*'); reserve=match(r'(?:留|保留)\s*')
    days=re.search(r'(\d+)天',text); months=re.search(r'(\d+)个月',text)
    need='cash' if re.search('备用|应急|短期',text) else 'growth' if '增长' in text else 'balanced' if re.search('稳健|均衡|平衡',text) else None
    horizon=int(days[1]) if days else int(months[1])*30 if months else None
    intent='clarify'
    if re.search('搜索|查找|查询基金',text): intent='search'
    elif re.search('到账|订单|进度|成交|成功了吗',text): intent='status'
    elif re.search('赎回|取出|卖出',text): intent='redeem'
    elif re.search('对比|比较',text): intent='compare'
    elif re.search('买|申购',text): intent='purchase'
    elif re.search('改|排除|不要|保留',text): intent='modify'
    elif budget is not None or horizon or need or '配置' in text: intent='allocate'
    elif '推荐' in text: intent='recommend'
    elif re.search('持仓|资产',text): intent='assets'
    elif '产品' in text and not re.search('什么|为什么|意思',text): intent='products'
    elif re.search('什么|为什么|风险|费用|赎回费|区别|年化|PMI|CPI|PPI|LPR|M1|M2|分红',text,re.I): intent='knowledge'
    elif text.strip() in {'继续','好的','确认','执行','同意'}: intent='clarify' if text.strip()=='继续' else 'refuse'
    return Intent(intent=intent,search_query=re.sub(r'^(?:搜索|查找|查询基金)\s*','',text) if intent=='search' else None,product_refs=refs[:3],amount_fen=amount,budget_fen=budget,reserve_fen=reserve,horizon_days=horizon,need=need,
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
    response={'mode':mode,'kind':'clarification','message':'请明确需求；需要预算、保留现金、预计使用期限和用途，交易只在可信卡片确认。'}
    codes = re.findall(r'(?<![0-9])([0-9]{6})(?![0-9])', text)
    if intent.intent == 'compare':
        outside = list(dict.fromkeys(codes + [r[1:] for r in intent.product_refs if re.fullmatch(r'R\d{6}', r)]))
        if not outside and data.get('research_candidates'):
            for ref in intent.product_refs:
                pid = resolve(ref, [{'product_id':'R'+c, 'name':c} for c in data['research_candidates']], ['R'+c for c in data['research_candidates']])
                outside.append(pid[1:])
        if len(outside) >= 2:
            analysis = await bank.call('POST', '/funds/compare', {'subject_user_id':user_id, 'codes':outside[:3]})
            response.update(kind='research', research_comparison=analysis,
                message='真实基金缓存对比；共同观察点 '+str(analysis['analysis']['common_samples'])+'。'+
                '\n'.join(p['name']+'：区间涨跌 '+str(p['metrics']['return'])+'，最大回撤 '+str(p['metrics']['max_drawdown']) for p in analysis['analysis']['products'])+
                '\n'+analysis['notice'])
            return response
    if intent.intent == 'search' or (codes and intent.intent in {'products', 'knowledge'}):
        query = intent.search_query or (codes[0] if codes else text)
        found = await bank.call('POST', '/funds/search', {'subject_user_id': user_id, 'query': query, 'page_size': 8})
        rows = found['items']; data['research_candidates'] = [r['code'] for r in rows]
        data['research_candidate_names'] = [{'id':'R'+r['code'],'name':r['name']} for r in rows]
        detail = await bank.call('POST', '/funds/detail', {'subject_user_id': user_id, 'code': rows[0]['code']}) if len(rows) == 1 else None
        response.update(kind='research', directory=found, fund_detail=detail,
            message='真实基金目录匹配 '+str(found['total'])+' 条。'+ '\n'.join(r['code']+' '+r['name']+' · '+r['fund_type']+' · 真实基金数据' for r in rows)+
            ('\n详情缓存：'+', '.join(k+'='+v['status'] for k,v in detail['evidence'].items()) if detail else '')+
            '\n仅研究；目录/净值不等于交易规则完整，不生成未审核订单。详情在后台获取，失败保留旧证据，不返回模拟替代值。')
        return response
    candidate_ids=data.get('candidates',[])
    active=data.get('active_command')
    # A single active order plus an unqualified amount correction modifies that order.
    # Models occasionally label it as budget; bind it to the explicit active action,
    # never reinterpret an explicitly stated plan budget or reserve.
    if active and intent.intent=='modify' and intent.amount_fen is None and intent.budget_fen is not None and re.search('改成|改为',text) and not re.search('预算|配置|保留|留现金',text):
        intent=intent.model_copy(update={'amount_fen':intent.budget_fen,'budget_fen':None})
    refs=[active['product_id'] if r in {'它','这个','刚才那个','刚才那笔'} and active else resolve(r,products,candidate_ids) for r in intent.product_refs]
    slots=dict(data.get('pending') or {})
    for name in ('budget_fen','reserve_fen','horizon_days','need'):
        if getattr(intent,name) is not None: slots[name]=getattr(intent,name)
    excluded=list(slots.get('excluded_products',[]))
    for ref in intent.exclude_refs:
        pid=resolve(ref,products,candidate_ids)
        if pid not in excluded: excluded.append(pid)
    slots['excluded_products']=excluded; data['pending']=slots
    accounts=[a for a in facts['cash'] if a['account_type']!='credit']
    if intent.account_ref:
        aliases={f'账户{i}':a['account_id'] for i,a in enumerate(accounts,1)}
        selected_account=aliases.get(intent.account_ref)
        if selected_account: data['account_id']=selected_account
        else: response['message']='请从当前资金上下文的付款账户选择，不支持其他用户账户。'; return response
    account=next((a for a in accounts if a['account_id']==data.get('account_id')),accounts[0] if len(accounts)==1 else None)
    if len(accounts)>1 and account is None and intent.intent in {'allocate','purchase','redeem','modify'}:
        response['message']='有多个可交易账户，请明确'+ '、'.join(f'账户{i}' for i in range(1,len(accounts)+1)); return response
    dataset=next((p.get('dataset_key') for p in products if p.get('dataset_key') and p['dataset_key']!='legacy-v1'),None)
    if intent.intent=='refuse': response['message']='口头确认不会授权或成交，请核对可信预览、本人确认并点击模拟高风险验证按钮。'
    elif intent.intent=='assets': response.update(kind='assets',assets=facts,message='以下为银行事实；占款、锁定份额、在途和赎回应收分开，不构成即时可用资金。')
    elif intent.intent=='status':
        operation=intent.operation_id or (active or {}).get('operation_id')
        if not operation and facts['orders']: operation=facts['orders'][0]['operation_id']
        if operation:
            result=await bank.query(user_id,operation)
            response.update(kind='order',order=result,message='原操作号 '+operation+' 的银行状态：'+result['status']+'；受理不等于已结算或到账。')
        else: response['message']='当前没有可查询的原操作号，请指定订单。'
    elif intent.intent in {'products','recommend'}:
        if intent.intent=='recommend':
            if not slots.get('horizon_days') or not slots.get('need'): response['message']='个性化推荐前请明确预计使用期限及资金用途。'; return response
            suitable=await require_suitability(db,user_id,1,allow_cash_only=True); await db.rollback()
            rows=candidates(products,suitable['capacity_level'],slots['horizon_days'],excluded)[:5] if suitable['purchase_allowed'] else []
        else: rows=products[:12]
        data['candidates']=[p['product_id'] for p in rows]; data['candidate_dataset']=dataset
        response.update(kind='products',products=rows,message='以下候选顺序已保存，支持“第几个”；产品风险、费用和净值来自银行。均为模拟交易，不保证回报。')
    elif intent.intent=='compare':
        if len(refs)<2: response['message']='请选择2至3个明确产品，不能用模糊家族名称。'
        else:
            research=await bank.call('POST','/research',{'subject_user_id':user_id,'dataset_key':dataset,'product_ids':refs}) if dataset else None
            response.update(kind='comparison',products=[p for pid in refs for p in products if p['product_id']==pid],analysis=research.get('analysis') if research else None,message='共同观察窗口对比，A/C归同一家族；未核实分红不标为总回报，缺失指标不补零。')
    elif intent.intent=='knowledge':
        result=await bank.call('POST','/knowledge',{'subject_user_id':user_id,'dataset_key':dataset,'query':text})
        citations=result['citations']
        if mode=='model': answer,citations,evidence=await explain(text,citations); response['explanation_model']=evidence
        else: answer='\n'.join(r['excerpt'] for r in citations) if citations else '资料不足，无法提供有来源的结论。'
        response.update(kind='knowledge',message=answer,citations=citations)
    elif intent.intent in {'purchase','redeem','modify'} and (refs or active) and (intent.amount_fen is not None or intent.intent in {'purchase','redeem'}):
        pid=refs[0] if refs else active['product_id']; product=next(p for p in products if p['product_id']==pid)
        direction='redeem' if intent.intent=='redeem' else active['direction'] if intent.intent=='modify' and active else 'purchase'
        if not account: response['message']='没有当前资金上下文的可交易账户。'; return response
        amount=intent.amount_fen or (active or {}).get('amount_fen')
        quantity=intent.units
        if direction=='redeem':
            lots=[lot for lot in facts['lots'] if lot['product_id']==pid and lot['account_id']==account['account_id']]
            total=sum((Decimal(lot['redeemable_units']) for lot in lots),Decimal(0))
            if intent.fraction: quantity=format((total/(2 if intent.fraction=='half' else 1)).quantize(Decimal('0.0001'),ROUND_DOWN),'.4f')
            if not quantity or Decimal(quantity)<=0: response['message']='暂无可赎回份额，可能仍在计价、被占用或锁定；先核对订单。'; return response
        elif not amount: response['message']='请明确申购金额，不自动使用全部余额。'; return response
        command=InvestmentCommand(operation_id='WO'+uuid4().hex,direction=direction,product_id=pid,product_version=product['version'],disclosure_version=product['disclosure']['version'],account_id=account['account_id'],amount_fen=amount if direction=='purchase' else 0,units=quantity or '0.0000')
        # Validate hard bank rules before offering a actionable card; no authorization writes.
        await bank.precheck(user_id,command)
        if active:
            from ...models.wealth import WealthAuthorizationORM
            await db.execute(update(WealthAuthorizationORM).where(WealthAuthorizationORM.user_id==user_id,WealthAuthorizationORM.operation_id==active['operation_id'],WealthAuthorizationORM.status.in_(['AWAITING_CONFIRMATION','AWAITING_MFA','VALID'])).values(status='EXPIRED'))
            await db.commit()
        data['active_command']=command.model_dump(); data['active_plan']=None
        response.update(kind='command',command=command.model_dump(),message='已生成新版本待确认模拟订单；金额修改使用新操作号，旧授权不会用于新参数。请在可信卡片本人确认与模拟验证。')
    elif intent.intent in {'allocate','modify','clarify','recommend'} and slots:
        missing=[key for key in ('budget_fen','reserve_fen','horizon_days','need') if key not in slots]
        if missing: response['message']='请补充：'+ '、'.join({'budget_fen':'本次预算','reserve_fen':'预算内保留现金（可明确为0）','horizon_days':'预计使用期限','need':'资金用途'}[key] for key in missing)
        elif account:
            request=PlanInput(account_id=account['account_id'],budget_fen=slots['budget_fen'],reserve_fen=slots['reserve_fen'],horizon_days=slots['horizon_days'],excluded_products=excluded,need=slots['need']).model_dump()
            previous=data.get('active_plan')
            if previous: request['version']=previous['version']
            plan=await wealth_plan.create(db,user_id,bank,request,previous['plan_id'] if previous else None)
            data['active_plan']={'plan_id':plan['plan_id'],'version':plan['version']}; data['active_command']=None
            data['candidates']=[i['command']['product_id'] for i in plan['items']]
            response.update(kind='plan',plan=plan,message='已保存新版本配置方案，最多5笔；保留现金和不足最低金额的尾款不强制投入。统一预览后本人确认和模拟验证。')
    return response


async def message(db,user_id,bank,text,session_id,version,client_message_id):
    mode=os.getenv('WEALTH_NLU_MODE','offline')
    if mode not in {'offline','model'}: raise ValueError('WEALTH_NLU_MODE_INVALID')
    # Never send credentials or bypass requests to a model. Known fund codes remain valid references.
    if re.search(r'密码|验证码|TOTP|口令|sk-[A-Za-z0-9]|绕过|忽略.*(?:规则|风控|授权)|代答|替我.*评估|提高.*等级',text,re.I):
        return {'mode':mode,'kind':'refusal','message':'请使用可信组件；对话不接收凭据，不代答评估、不绕过授权。'}
    key=user_id+':'+client_message_id
    digest=fingerprint([text,session_id,version])
    existing=await db.get(WealthChatRequestORM,key)
    if existing:
        if existing.digest!=digest: raise WealthPolicyError('消息编号对应不同内容','IDEMPOTENCY_CONFLICT',409)
        if existing.status=='DONE': return existing.response
        if datetime.now(timezone.utc)-existing.started_at.replace(tzinfo=timezone.utc)<timedelta(seconds=180): raise WealthPolicyError('消息仍在处理中，请以原消息编号重试','MESSAGE_PENDING',409)
        await db.delete(existing); await db.commit()
    session=await db.get(WealthConversationORM,session_id) if session_id else None
    if session_id and (session is None or session.user_id!=user_id): raise WealthPolicyError('会话不存在','NOT_FOUND',404)
    if session and session.version!=version: raise WealthPolicyError('会话已更新，请刷新','STALE_PLAN',409)
    sid=session_id or 'WC'+uuid4().hex
    data=dict(session.data) if session else {'history':[],'pending':{},'candidates':[]}
    await db.rollback()
    row=await claim(db,user_id,client_message_id,digest)
    evidence={}
    try:
        products,facts=await bank.products(user_id),await bank.assets(user_id)
        contexts=await bank.call('POST','/contexts',{'subject_user_id':user_id})
        if data.get('context_id') and data['context_id']!=contexts['selected']:
            data.update(pending={},candidates=[],active_command=None,active_plan=None)
        data['context_id']=contexts['selected']
        evidence={}
        if mode=='model':
            context={'research_candidates':data.get('research_candidate_names',[]),'pending':data.get('pending',{}),'candidates':[{'id':p['product_id'],'name':p['name']} for pid in data.get('candidates',[]) for p in products if p['product_id']==pid],
                'products':[{'id':p['product_id'],'name':p['name']} for p in products], 'active':{'direction':data['active_command']['direction'],'product_id':data['active_command']['product_id'],'amount_fen':data['active_command']['amount_fen']} if data.get('active_command') else None,
                'accounts':[f'账户{i}' for i,a in enumerate([a for a in facts['cash'] if a['account_type']!='credit'],1)],
                'history':[h['user'] for h in data.get('history',[])[-4:]]}
            intent,evidence=await understand(text,context)
        else: intent=offline(text)
        response=await action(db,user_id,bank,text,intent,data,products,facts,contexts,mode)
        response.update(model_evidence=evidence,intent=intent.model_dump(),client_message_id=client_message_id)
    except WealthPolicyError as error:
        await db.rollback(); response={'mode':mode,'kind':'assessment_required' if 'ASSESSMENT' in error.code else 'clarification','code':error.code,'message':error.message}
    except Exception as error:
        from ...integrations.bank_api import BankApiError
        await db.rollback()
        if isinstance(error,BankApiError): response={'mode':mode,'kind':'clarification','code':error.code,'message':error.message}
        elif isinstance(error,ValueError) and not str(error).startswith('MODEL_'): response={'mode':mode,'kind':'clarification','message':str(error)[:200]}
        else: response={'mode':mode,'kind':'model_unavailable' if mode=='model' else 'clarification','code':'MODEL_UNAVAILABLE' if mode=='model' else 'CLARIFICATION_REQUIRED','message':'模型请求失败，本轮未生成新的交易参数；可重试或使用可信表单。' if mode=='model' else '无法生成预览，请明确参数。'}
    await lock_profile(db,user_id)
    session=await db.get(WealthConversationORM,sid,with_for_update=True,populate_existing=True)
    if session and session.version!=version:
        await db.rollback()
        raise WealthPolicyError('处理期间会话发生变化，请刷新','STALE_PLAN',409)
    if session is None: session=WealthConversationORM(session_id=sid,user_id=user_id,version=1,data={}); db.add(session)
    else: session.version+=1
    response.update(session_id=sid,version=session.version,model_evidence=evidence)
    data['history']=[*data.get('history',[]),{'user':text,'reply':response}][-20:]; session.data=data
    row=await db.get(WealthChatRequestORM,key); row.status='DONE'; row.response=response
    await db.commit()
    return response
