"""离线描述性评价；不参与推荐排序、不改变正式政策或购买资格。"""
import math
import statistics
from dataclasses import replace
from copy import deepcopy
from datetime import date,datetime
from .wealth_portfolio_allocation import problem,project,intervals,x3,TOL,ModelError
from .wealth_portfolio_projection import simplex,round_family_amounts


def total_return_metrics(dates,indices,*,consecutive_trading_days=False):
    """输入必须先经总回报/事件证据验收，连续交易日须外部日历核对。"""
    if len(dates)!=len(indices) or not indices:raise ValueError('RETURN_SERIES_MISSING')
    days=[date.fromisoformat(d) for d in dates]
    if days!=sorted(set(days)):raise ValueError('RETURN_DATES_INVALID')
    if any(type(v) is bool or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in indices):raise ValueError('RETURN_INDEX_INVALID')
    returns=[b/a-1 for a,b in zip(indices,indices[1:])]
    peak=indices[0];dd=0.
    for value in indices:peak=max(peak,value);dd=max(dd,1-value/peak)
    vol=statistics.stdev(returns) if len(returns)>=2 else None
    return {'status':'available','start':dates[0],'end':dates[-1],'observations':len(indices),'total_return':indices[-1]/indices[0]-1,'daily_volatility':vol,
            'maximum_drawdown':dd,'annualized_volatility':vol*math.sqrt(252) if consecutive_trading_days and vol is not None else None,
            'annualization_convention':252 if consecutive_trading_days else None}


def buy_and_hold_series(component_indices,weights,*,cash_weight=0.,cash_index=None):
    """一次初始权重的价值指数；不每日再平衡，不默认现金收益为0。"""
    if not component_indices or len(component_indices)!=len(weights):raise ValueError('COMPOSITION_INVALID')
    n=len(component_indices[0])
    if n==0 or any(len(s)!=n for s in component_indices):raise ValueError('COMMON_WINDOW_REQUIRED')
    if any(type(w) not in (int,float) or not math.isfinite(w) or w<0 for w in [*weights,cash_weight]) or abs(sum(weights)+cash_weight-1)>1e-12:raise ValueError('WEIGHTS_INVALID')
    if cash_weight and cash_index is None:raise ValueError('CASH_RETURN_UNKNOWN')
    sources=list(component_indices)+([cash_index] if cash_weight else [])
    if any(len(s)!=n or any(type(x) not in (int,float) or not math.isfinite(x) or x<=0 for x in s) for s in sources):raise ValueError('RETURN_INDEX_INVALID')
    values=[sum(w*s[t]/s[0] for w,s in zip(weights,component_indices)) for t in range(n)]
    if cash_weight:values=[v+cash_weight*cash_index[t]/cash_index[0] for t,v in enumerate(values)]
    return values


def parse_history_series(records,date_field,value_field,*,unit,kind,cutoff):
    """Strict ordered exact-share series adapter; not a return-method certification.

    Percentage values need a proved zero-percent anchor before conversion by
    cumulative_wealth_index. NAV and accumulated NAV remain distinct.
    """
    if unit not in ('CNY_per_share','percent','decimal','wealth_index'):raise ValueError('HISTORY_UNIT_UNKNOWN')
    if kind not in ('unit_nav','accumulated_nav','cumulative_return','wealth_index'):raise ValueError('HISTORY_KIND_UNKNOWN')
    expected={'unit_nav':'CNY_per_share','accumulated_nav':'CNY_per_share','wealth_index':'wealth_index'}
    if kind in expected and unit!=expected[kind]:raise ValueError('HISTORY_UNIT_MISMATCH')
    if kind=='cumulative_return' and unit not in ('percent','decimal'):raise ValueError('HISTORY_UNIT_MISMATCH')
    dates=[];values=[];excluded=0
    limit=date.fromisoformat(cutoff)
    for row in records:
        if not isinstance(row.get(date_field),str):raise ValueError('HISTORY_DATE_MISSING')
        d=date.fromisoformat(row[date_field])
        v=row.get(value_field)
        if type(v) not in (int,float) or not math.isfinite(v):raise ValueError('HISTORY_VALUE_INVALID')
        if kind!='cumulative_return' and v<=0:raise ValueError('HISTORY_VALUE_NONPOSITIVE')
        if dates and d<=date.fromisoformat(dates[-1]):raise ValueError('HISTORY_DATES_UNORDERED_OR_DUPLICATE')
        dates.append(d.isoformat());values.append(v)
    kept=[(d,v) for d,v in zip(dates,values) if date.fromisoformat(d)<=limit]
    excluded=len(dates)-len(kept)
    return {'dates':[d for d,v in kept],'values':[v for d,v in kept],'unit':unit,'kind':kind,'future_rows_excluded':excluded}


def cumulative_wealth_index(values,*,unit,zero_percent_anchor,method_verified,events_verified):
    """Only documented cumulative returns (not differences) may become G=1+R."""
    if not method_verified or not events_verified:raise ValueError('TOTAL_RETURN_METHOD_UNPROVEN')
    if not zero_percent_anchor:raise ValueError('CUMULATIVE_ANCHOR_UNKNOWN')
    if unit not in ('percent','decimal'):raise ValueError('HISTORY_UNIT_UNKNOWN')
    scale=100 if unit=='percent' else 1
    if not values or any(type(v) not in (int,float) or not math.isfinite(v) or 1+v/scale<=0 for v in values):raise ValueError('RETURN_INDEX_INVALID')
    return [1+v/scale for v in values]


def research_buy_hold(component_indices,weights,*,cash_weight=0.,assumptions=None,families=None):
    """Separate explicit gross research scenario, never actual fee/cash facts."""
    if assumptions!={'cash_return':'constant_index_research_assumption','external_fees':'not_modelled','rebalance':'none'}:
        raise ValueError('RESEARCH_ASSUMPTIONS_REQUIRED')
    if not component_indices:raise ValueError('COMPOSITION_INVALID')
    series=buy_and_hold_series(component_indices,weights,cash_weight=cash_weight,
                               cash_index=[1.]*len(component_indices[0]) if cash_weight else None)
    end=[w*s[-1]/s[0]/series[-1] for w,s in zip(weights,component_indices)]
    def hhi(ws):return sum(w*w for w in ws)
    initial_family=end_family=None
    if families is not None:
        if len(families)!=len(weights) or any(not f for f in families):raise ValueError('FAMILY_IDENTITY_UNPROVEN')
        def grouped(ws):return [sum(w for w,f in zip(ws,families) if f==key) for key in sorted(set(families))]
        initial_family=hhi(grouped(weights));end_family=hhi(grouped(end))
    return {'data_kind':'hypothetical_fixed_portfolio','gross_scenario':True,'assumptions':assumptions,'series':series,
            'initial_product_hhi_ex_cash':hhi(weights),'end_product_hhi_ex_cash':hhi(end),'end_weights':end,
            'initial_family_hhi_ex_cash':initial_family,'end_family_hhi_ex_cash':end_family,'family_status':'verified' if families is not None else 'unknown_not_unique_products'}


def require_point_in_time(facts,decision_at,required_fields):
    """A research gate only: observed today does not establish historical validity."""
    decision=datetime.fromisoformat(decision_at.replace('Z','+00:00'))
    if decision.tzinfo is None:raise ValueError('DECISION_TIME_ZONE_REQUIRED')
    missing=[]
    for field in required_fields:
        f=facts.get(field,{})
        try:
            published=datetime.fromisoformat(f['available_at'].replace('Z','+00:00'))
            start=datetime.fromisoformat(f['applicable_from'].replace('Z','+00:00'))
            end=datetime.fromisoformat(f['applicable_to'].replace('Z','+00:00')) if f.get('applicable_to') else None
            valid=f.get('value') is not None and f.get('reference_sha256') and published.tzinfo and start.tzinfo and (end is None or end.tzinfo) and published<=decision and start<=decision and (end is None or decision<=end)
        except (KeyError,ValueError,TypeError):valid=False
        if not valid:missing.append(field)
    return {'status':'blocked' if missing else 'available','decision_at':decision_at,'missing_fields':missing}


def allocation_comparison(level,h,*,eta=.25,delta=.01,available=(True,True,True),caps=('1','1','1'),p=None):
    """仅评估参照：生产常量无调参入口；复用生产问题及几何投影。"""
    if eta not in (0,.25,.5) or delta not in (.005,.01,.02):raise ValueError('COMPARISON_PARAMETER_INVALID')
    original=problem(level,h,available,caps,p)
    effective_eta=eta if h is not None else 0.
    center=[(original.b[i]+effective_eta*(h[i] if h is not None else 0))/(1+effective_eta) for i in (0,2)]
    P=replace(original,eta=effective_eta,center=center)
    star=project(P)
    # 不修改模块DELTA：椭圆半径与固定活钱切片在此解析计算。
    y=P.center;r2=max(0.,(P.loss(star)+delta-P.loss(x3(y)))/(1+P.eta))
    from .wealth_portfolio_allocation import vertices,inverse,metric
    ranges=[]
    for direction,offset in [([1.,0.],0.),([-1.,-1.],1.),([0.,1.],0.)]:
        candidates=vertices(P.constraints)+[[star[0],star[2]]];v=inverse(direction);den=sum(a*b for a,b in zip(direction,v))
        scale=math.sqrt(r2/den)
        candidates += [[y[0]+sign*scale*v[0],y[1]+sign*scale*v[1]] for sign in (-1,1)]
        for a,b,d in P.constraints:
            normal=inverse([a,b]);den=a*normal[0]+b*normal[1];t=(a*y[0]+b*y[1]-d)/den;base=[y[0]-t*normal[0],y[1]-t*normal[1]]
            rem=r2-metric([base[0]-y[0],base[1]-y[1]],[base[0]-y[0],base[1]-y[1]])
            if rem>=-TOL:
                tangent=[-b,a];scale=math.sqrt(max(0.,rem)/metric(tangent,tangent))
                candidates += [[base[0]+s*scale*tangent[0],base[1]+s*scale*tangent[1]] for s in (-1,1)]
        valid=[z for z in candidates if P.valid(z) and P.loss(x3(z))<=P.loss(star)+delta+TOL]
        vals=[offset+sum(a*b for a,b in zip(direction,z)) for z in valid];ranges.append([min(vals),max(vals)])
    c=star[0];slider=None
    if 1-c>TOL:
        middle=y[1]-(c-y[0])/2;rem=(P.loss(star)+delta-P.loss(x3([c,middle])))/(1+P.eta);width=math.sqrt(max(0.,rem)/2);lo,hi=middle-width,middle+width
        for a,b,d in P.constraints:
            if b>0:hi=min(hi,(d-a*c)/b)
            elif b<0:lo=max(lo,(d-a*c)/b)
        slider=[lo/(1-c),hi/(1-c)]
    assert P.valid([star[0],star[2]],True)
    return {'comparison_only':True,'eta':eta,'effective_eta':effective_eta,'delta':delta,'x_star':star,'hard_constraints_passed':True,
            'distance_to_current':math.sqrt(sum((a-b)**2 for a,b in zip(star,h))) if h is not None else None,
            'original_target_loss':original.loss(star),'ranges':ranges,'slider':slider,
            'slider_width':slider[1]-slider[0] if slider else None,'h_status':'known' if h is not None else 'unknown_not_zero'}


def product_comparison(families,budget_fen,total_fen,eta=.25):
    """同集合固定份额上限的参照；不替代provider购买资格门禁。"""
    if eta not in (0,.25,.5):raise ValueError('COMPARISON_PARAMETER_INVALID')
    if not families:raise ValueError('PRODUCT_SET_MISSING')
    if any(p.current_fen and not p.holding_allowed for f in families for p in f.parts):raise ModelError('HOLDING_PERMISSION_UNPROVEN')
    mass=budget_fen/total_fen;u=[mass/len(families)]*len(families);h=[f.current_fen/total_fen for f in families]
    cap_fen=[f.cap_fen for f in families];caps=[None if c is None else c/total_fen for c in cap_fen]
    w=simplex([(a+eta*b)/(1+eta) for a,b in zip(u,h)],mass,caps);amounts=round_family_amounts(w,budget_fen,total_fen,cap_fen)
    assert sum(amounts)==budget_fen and all(c is None or a<=c for a,c in zip(amounts,cap_fen))
    return {'comparison_only':True,'eta_product':eta,'amounts_fen':amounts,'hard_constraints_passed':True,'caps_fen':cap_fen,
            'HHI':sum((a/budget_fen)**2 for a in amounts) if budget_fen else None,'adjustment_proxy':.5*sum(abs(a/total_fen-b) for a,b in zip(amounts,h)),
            'equal_status':'available' if all(c is None or budget_fen/len(families)<=c for c in cap_fen) else 'baseline_infeasible'}
