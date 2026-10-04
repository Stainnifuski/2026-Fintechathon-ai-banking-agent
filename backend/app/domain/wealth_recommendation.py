"""Replaceable recommendation contract and independent bank-fact validation."""
from copy import deepcopy
from decimal import Decimal
from datetime import datetime
from typing import Protocol
from .wealth_allocation import allocate, candidates, WEIGHTS
from .wealth import purchase


class RecommendationProvider(Protocol):
    provider_id: str
    provider_version: str

    def recommend(self, inputs: dict) -> dict: ...
    def research(self, inputs: dict) -> dict: ...


class DeterministicRuleProvider:
    provider_id = 'mock-allocation-v1'
    provider_version = '1'

    def research(self, inputs: dict) -> dict:
        eligible=research_eligible(inputs)
        return {'schema_version':'wealth-recommendation-v1','provider_id':self.provider_id,
            'provider_version':self.provider_version,'conditional':True,'as_of':inputs['as_of'],
            'constraints':deepcopy(inputs['constraints']),'data_versions':deepcopy(inputs.get('data_versions',{})),
            'items':[{'product_id':p['product_id'],'product_version':p['version'],
                'reason':'符合已明确的约束；未确定的期限、目标及到账要求仍需结合产品规则判断。',
                'reason_codes':['CONDITIONAL_RESEARCH'],'evidence':{'nav_date':p.get('nav_date')}} for p in eligible[:5]],
            'warnings':['未确定条件不会被填成默认值；条件候选不是可执行配置。']}

    def recommend(self, inputs: dict) -> dict:
        parameters=deepcopy(inputs['allocation'])
        products=constraint_products(inputs['products'],inputs.get('constraints',{}))
        parameters['capacity']=preference_capacity(parameters['capacity'],inputs.get('constraints',{}))
        result = allocate(products, **parameters)
        result.update(schema_version='wealth-recommendation-v1', provider_id=self.provider_id,
            provider_version=self.provider_version, as_of=inputs['as_of'],
            target_category_weights=deepcopy(WEIGHTS[parameters['capacity']]),
            constraints=deepcopy(inputs.get('constraints',{})),
            data_versions=deepcopy(inputs.get('data_versions',{})),
            warnings=['模拟规则配置，非最优解或收益预测'],
            summary=['按风险、费用和流动性规则筛选产品。','未分配预算保留为现金，交易仍需本人确认和模拟授权。'])
        constraints=inputs.get('constraints',{})
        goal=constraints.get('investment_goal',{}).get('value')
        goal_text={'reduce_volatility':'偏向减少波动','preserve_value':'以保值为目标，但不承诺保本','balanced':'兼顾波动与增长','growth':'争取增长，可能承受亏损'}.get(goal,'目标未明确')
        liquidity=constraints.get('liquidity_requirement',{}).get('value',{})
        liquidity_text={'immediate':'仅选择符合模拟快赎与无持有期规则的产品；仍受服务窗口和每日限额约束',
            'few_business_days':'按明确的到账等待要求筛选','flexible':'可接受产品规定的到账等待'}.get(liquidity.get('mode'),'到账偏好未指定，逐项显示产品规则')
        result['summary']=[f"{goal_text}；按正式风险评估及{parameters['horizon_days']}天可投资期限筛选。",
            liquidity_text+'。','预算尾款留为现金，交易仍需本人确认及模拟授权。']
        for item in result['items']:
            product=next(p for p in inputs['products'] if p['product_id']==item['product_id'])
            item.update(reason_codes=['RISK_MATCH','LIQUIDITY_MATCH','COST_PRIORITY'],
                evidence={'product_id':product['product_id'],'product_version':product['version'],
                    'nav_date':product.get('nav_date'),'calendar_version':product['rules']['calendar_version']},
                reason=f'{goal_text}，符合已明确风险及期限约束，按费用与流动性规则选择。')
        return result


def preference_capacity(capacity: int, constraints: dict) -> int:
    goal=constraints.get('investment_goal',{}).get('value')
    if goal in {'reduce_volatility','preserve_value'}:return min(capacity,2)
    if goal=='balanced':return min(capacity,3)
    return capacity


def constraint_products(products: list[dict], constraints: dict) -> list[dict]:
    """Explicit liquidity preferences narrow eligibility, never alter formal assessment."""
    excluded=constraints.get('excluded_categories',{}).get('value',[])
    liquidity=constraints.get('liquidity_requirement',{}).get('value',{})
    mode=liquidity.get('mode');limit=liquidity.get('max_business_days')
    result=[]
    for product in products:
        rules=product['rules']
        if product['category'] in excluded:continue
        if mode=='immediate':
            quick=rules.get('quick_redemption',{})
            if not quick or quick.get('enabled') is False or rules['holding_days']>0:continue
        elif mode=='few_business_days' and limit is not None:
            if rules['cash_business_days']>limit or rules['holding_days']>0:continue
        result.append(product)
    return result


def research_eligible(inputs: dict) -> list[dict]:
    rows=constraint_products(inputs['products'],inputs['constraints'])
    capacity=preference_capacity(inputs['capacity'],inputs['constraints'])
    if inputs.get('horizon_days') is not None:
        return candidates(rows,capacity,inputs['horizon_days'],inputs.get('excluded',[]))
    return [p for p in rows if p['product_risk_level']<=capacity and p['status']=='on_sale' and p['nav'] is not None
        and not p['rules']['purchase_paused'] and p['product_id'] not in inputs.get('excluded',[])
        and not p.get('data_stale',False) and p.get('purchase_open',True)]


def conditional_research(inputs: dict, provider: RecommendationProvider | None = None) -> dict:
    provider=provider or get_provider()
    result=provider.research(deepcopy(inputs))
    eligible={p['product_id']:p for p in research_eligible(inputs)}
    if (result.get('schema_version')!='wealth-recommendation-v1' or result.get('provider_id')!=provider.provider_id
        or result.get('provider_version')!=provider.provider_version or result.get('data_versions')!=inputs.get('data_versions',{})
        or result.get('constraints')!=inputs['constraints'] or result.get('conditional') is not True or result.get('as_of')!=inputs['as_of']):
        raise ValueError('RECOMMENDATION_OUTPUT_INVALID')
    seen=set()
    for item in result.get('items',[]):
        p=eligible.get(item.get('product_id'))
        if not p or p['product_id'] in seen or item.get('product_version')!=p['version'] or 'amount_fen' in item or not item.get('reason_codes') or not item.get('evidence') or not isinstance(item.get('reason'),str):
            raise ValueError('RECOMMENDATION_OUTPUT_INVALID')
        seen.add(p['product_id'])
    return result


_providers: dict[str, RecommendationProvider]={'mock-allocation-v1':DeterministicRuleProvider()}


def register_provider(provider: RecommendationProvider) -> None:
    """Explicit deployment/test injection, never a model-supplied import or tool."""
    if not provider.provider_id or not provider.provider_version:
        raise ValueError('PROVIDER_IDENTITY_REQUIRED')
    _providers[provider.provider_id]=provider


def get_provider() -> RecommendationProvider:
    import os
    identity=os.getenv('WEALTH_RECOMMENDATION_PROVIDER','mock-allocation-v1')
    if identity not in _providers: raise ValueError('RECOMMENDATION_PROVIDER_UNKNOWN')
    return _providers[identity]


def validate_result(result: dict, inputs: dict) -> dict:
    """Never silently repair an invalid provider's executable parameters."""
    args=inputs['allocation'];budget=args['budget_fen']
    def reject(): raise ValueError('RECOMMENDATION_OUTPUT_INVALID')
    if result.get('schema_version')!='wealth-recommendation-v1' or not result.get('provider_id') or not result.get('provider_version'):
        reject()
    if result.get('as_of')!=inputs['as_of'] or result.get('data_versions')!=inputs.get('data_versions',{}):reject()
    if result.get('budget_fen')!=budget or result.get('reserve_fen')!=args['reserve_fen']:reject()
    weights=result.get('target_category_weights',{})
    if not weights or any(k not in {'cash','bond','mixed','index','equity'} or type(v) is not int or not 0<=v<=100 for k,v in weights.items()) or sum(weights.values())!=100:reject()
    eligible={p['product_id']:p for p in candidates(constraint_products(inputs['products'],inputs.get('constraints',{})),
        preference_capacity(args['capacity'],inputs.get('constraints',{})),args['horizon_days'],args['excluded'])} if args.get('purchase_allowed',True) else {}
    items=result.get('items',[])
    if not isinstance(items,list) or len(items)>args.get('max_items',3):reject()
    spent=fees=0;seen=set();families=set();actual={}
    for item in items:
        pid=item.get('product_id');product=eligible.get(pid)
        amount=item.get('amount_fen')
        if not product or pid in seen or type(amount) is not int:reject()
        rules=product['rules'];family=product.get('fund_family_id') or pid
        if family in families:reject()
        if not rules['min_amount_fen']<=amount<=rules['max_amount_fen']:reject()
        if item.get('product_version')!=product['version'] or item.get('disclosure_version')!=product['disclosure']['version'] or item.get('category')!=product['category']:reject()
        if product.get('available_at') and datetime.fromisoformat(product['available_at'])>datetime.fromisoformat(inputs['as_of']):reject()
        if amount>(budget-args['reserve_fen'])*60//100:reject()
        if not isinstance(item.get('reason'),str) or not 1<=len(item['reason'])<=200 or not item.get('reason_codes') or not isinstance(item.get('evidence'),dict):reject()
        fee=purchase(amount,product['nav'],rules['purchase_fee_rate']).fee_fen
        item['estimated_fee_fen']=fee
        item['budget_percent']=format(Decimal(amount)*100/Decimal(budget),'.2f') if budget else '0.00'
        actual[product['category']]=actual.get(product['category'],0)+amount
        spent+=amount;fees+=fee;seen.add(pid);families.add(family)
    unallocated=result.get('unallocated_fen');retained=result.get('retained_cash_fen')
    if type(unallocated) is not int or type(retained) is not int or unallocated<0:reject()
    if spent>budget-args['reserve_fen'] or unallocated!=budget-args['reserve_fen']-spent or retained!=budget-spent or result.get('investment_fen')!=spent:reject()
    cash=inputs['available_cash_fen']-spent
    if cash<0 or cash<inputs.get('minimum_account_cash_fen',0):reject()
    result.update(estimated_fee_fen=fees,account_remaining_cash_fen=cash,
        actual_category_weights={k:format(Decimal(v)*100/Decimal(budget),'.2f') for k,v in actual.items()} if budget else {},
        validation_version='wealth-provider-guard-v1')
    return result


def recommend(inputs: dict, provider: RecommendationProvider | None = None) -> dict:
    provider=provider or get_provider()
    # Providers receive detached JSON facts, never ORM/session or authorization secrets.
    result=provider.recommend(deepcopy(inputs))
    if result.get('provider_id')!=provider.provider_id or result.get('provider_version')!=provider.provider_version:
        raise ValueError('RECOMMENDATION_PROVIDER_IDENTITY_INVALID')
    return validate_result(result,inputs)
