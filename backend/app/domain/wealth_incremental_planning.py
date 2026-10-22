"""Incremental cash allocation: keep old positions unless hard policy requires sales."""
from fractions import Fraction
from .wealth_portfolio_allocation import PARAMETERS, ModelError


def ceil(value):
    return -(-value.numerator // value.denominator)


def apportion(total, weights):
    if total < 0 or total > sum(weights) or any(w < 0 for w in weights):
        raise ModelError('PLANNING_ALLOCATION_INVALID')
    if not sum(weights):return [0]*len(weights)
    raw=[Fraction(total*w,sum(weights)) for w in weights]
    values=[int(v) for v in raw]
    for i in sorted(range(len(weights)),key=lambda i:(-(raw[i]-values[i]),i))[:total-sum(values)]:values[i]+=1
    return values


def incremental_targets(level,total,holdings,reserve,available,unavailable,model_amounts,fee_rate='0'):
    if (level not in PARAMETERS or len(holdings)!=3 or type(reserve) is not int
        or not 0<=reserve<=available or any(type(x) is not int or x<0 for x in holdings)
        or sum(holdings)+available+unavailable!=total):raise ModelError('PLANNING_CASH_OUT_OF_RANGE')
    fixed=reserve+unavailable
    cash_min=ceil(Fraction(str(PARAMETERS[level][0]))*total)
    liquid_min=max(fixed+holdings[0],cash_min)
    if level==1:
        targets=[total,0,0];sales=[0,holdings[1],holdings[2]]
    else:
        p=Fraction(str(PARAMETERS[level][2]));n=total-liquid_min
        sell_growth=max(0,holdings[2]-int(p*n))
        sell_stable=max(0,holdings[1]+holdings[2]-n-sell_growth)
        # A conservative fee allowance prevents an exact cap from becoming
        # over-risk solely because redemption fees reduce the final basis.
        rate=Fraction(str(fee_rate))
        if not 0<=rate<1:raise ModelError('PLANNING_FEE_UNPROVEN')
        if (sell_growth or sell_stable) and rate and p:
            extra=ceil(p*rate*(sell_growth+sell_stable)/(1-p*rate))
            sell_growth=min(holdings[2],sell_growth+extra)
        keep_s=holdings[1]-sell_stable;keep_g=holdings[2]-sell_growth
        cash_max=min(total-keep_s-keep_g,total-ceil(Fraction(keep_g,p)) if p else total)
        liquid=max(liquid_min,min(max(model_amounts[0],fixed+holdings[0]),cash_max))
        noncash=total-liquid;model_non=model_amounts[1]+model_amounts[2]
        desired_g=(noncash*model_amounts[2]//model_non) if model_non else 0
        growth=max(keep_g,min(desired_g,int(p*noncash),noncash-keep_s))
        targets=[liquid,noncash-growth,growth];sales=[0,sell_stable,sell_growth]
    retained=[h-s for h,s in zip(holdings,sales)]
    purchases=[targets[i]-retained[i]-(fixed if i==0 else 0) for i in range(3)]
    if min(purchases)<0 or sum(purchases)!=available-reserve+sum(sales):raise ModelError('PLANNING_ALLOCATION_INVALID')
    return {'targets':targets,'sales':sales,'purchases':purchases,'fixed_cash_fen':fixed}


def product_targets(families,bucket_target,sale_amount,total,fixed_cash=0):
    """Pro-rata sale of every old product; distribute buys only to explicit choices."""
    fs=sorted(families,key=lambda f:f.family_id)
    parts=sorted([(f,p) for f in fs for p in f.parts],key=lambda fp:fp[1].product_id)
    reductions=apportion(sale_amount,[p.current_fen for _,p in parts])
    values={p.product_id:p.current_fen-r for (_,p),r in zip(parts,reductions)}
    for (_,p),reduction in zip(parts,reductions):
        q=p.evidence.get('qualification',{})
        if reduction and q.get('redemption_allowed') is not True:raise ModelError('PLANNING_REDEMPTION_UNAVAILABLE')
    extra=bucket_target-fixed_cash-sum(values.values())
    legal=[p for _,p in parts if p.purchase_eligible]
    if extra<0:raise ModelError('PLANNING_ALLOCATION_INVALID')
    if extra and not legal:raise ModelError('PLANNING_SELECT_BUCKET_PRODUCT')
    for i,p in enumerate(legal):values[p.product_id]+=extra//len(legal)+(i<extra%len(legal))
    result=[]
    for f in fs:
        amount=fixed_cash if f.cash else sum(values[p.product_id] for p in f.parts)
        result.append({'family_id':f.family_id,'bucket':f.bucket,'cash':f.cash,'current_fen':f.current_fen,
            'target_fen':amount,'parts':[{'product_id':p.product_id,'current_fen':p.current_fen,
                'target_fen':values[p.product_id],'evidence':p.evidence} for p in f.parts]})
    if sum(r['target_fen'] for r in result)!=bucket_target:raise ModelError('PLANNING_ALLOCATION_INVALID')
    return {'targets':result}
