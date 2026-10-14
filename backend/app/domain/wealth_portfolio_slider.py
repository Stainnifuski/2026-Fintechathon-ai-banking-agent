"""Fixed-cash executable decimal lattice; original P/U and projection unchanged."""
import math
from decimal import Decimal
from .wealth_portfolio_allocation import problem,validate_adjustment,integer_valid,BUCKETS,ModelError
from .wealth_portfolio_projection import WeightFamily,WeightPart,project_products

def executable_slider(plan,metadata,decisions):
    total=plan.basis_fen;cash=plan.buckets[0].target_fen;noncash=total-cash
    empty={'bounds':None,'step':None,'reason':'NO_EXECUTABLE_STEP'}
    if noncash<=0 or metadata['model']['h'] is None:return empty
    contract=metadata['validation_contract'];calc=contract['calculation']
    level=next((q.user_level for q in decisions.values() if q.user_level is not None),None)
    if level is None:
        # R1/all cash already returned; missing product qualifications cannot
        # manufacture a finite range.
        return empty
    P=problem(level,calc['h'],contract['available'],contract['caps'],contract['p'])
    star=calc['continuous_solution'];c=cash/total
    center=P.center[1]-(c-P.center[0])/2
    remaining=(P.loss(star)+calc['delta']-P.loss([c,1-c-center,center]))/(1+P.eta)
    if remaining<0:return empty
    width=math.sqrt(remaining/2);lo,hi=center-width,center+width
    for a,b,d in P.constraints:
        if b>0:hi=min(hi,(d-a*c)/b)
        elif b<0:lo=max(lo,(d-a*c)/b)
        elif a*c>d+1e-12:return empty
    # Decimal ratios must produce integer cents. Use at most 0.1 percentage
    # points, and coarsen only when this denominator cannot represent cents.
    power=0;n=noncash
    while n%10==0 and power<3:power+=1;n//=10
    denominator=10**power
    low=max(0,math.ceil(lo/(1-c)*denominator+1e-9))
    high=min(denominator,math.floor(hi/(1-c)*denominator-1e-9))
    families={b:[] for b in BUCKETS}
    current={p.product_id:p.current_fen for p in plan.advised_positions}
    for bucket,projection in metadata['product_projections'].items():
        for row in projection['targets']:
            parts=[]
            for part in row['parts']:
                q=decisions.get(part['product_id'])
                if q is None:return empty
                parts.append(WeightPart(part['product_id'],part.get('current_fen',current.get(part['product_id'],0)),q.purchase_eligible,q.holding_allowed is True))
            held=row.get('current_fen',sum(p.current_fen for p in parts))
            if row['cash'] and 'current_fen' not in row:held=sum(a['balance_fen'] for a in metadata['snapshot']['accounts'])+metadata['snapshot']['receivable_fen']
            families[bucket].append(WeightFamily(row['family_id'],bucket,held,parts,row['cash']))
    valid=[]
    for tick in range(low,high+1):
        g=noncash*tick//denominator;amounts=[cash,noncash-g,g]
        if not integer_valid(P,amounts,total,request=False) or not validate_adjustment(P,star,[a/total for a in amounts])['accepted']:continue
        try:
            projected={b:project_products(families[b],amounts[i],total) for i,b in enumerate(BUCKETS)}
        except ModelError:continue
        okay=True
        for result in projected.values():
            for family in result['targets']:
                for part in family['parts']:
                    q=decisions[part['product_id']];increase=part['target_fen']-part['current_fen']
                    if increase>0:
                        offer=q.evidence.get('bank_fact',{})
                        minimum=offer.get('minimum_fen');maximum=offer.get('maximum_fen')
                        if minimum is None or increase<minimum or maximum is not None and increase>maximum:okay=False
        if okay:valid.append(tick)
    if not valid:return empty
    nearest=min(valid,key=lambda tick:abs(tick/denominator-float(plan.growth_ratio_ex_cash or 0)))
    start=end=nearest;available=set(valid)
    while start-1 in available:start-=1
    while end+1 in available:end+=1
    if start==end:return empty
    return {'bounds':[str(Decimal(start)/denominator),str(Decimal(end)/denominator)],
            'step':str(Decimal(1)/denominator),'reason':None}
