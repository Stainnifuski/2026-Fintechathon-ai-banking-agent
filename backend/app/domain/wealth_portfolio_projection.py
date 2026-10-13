"""同集合持仓约束单纯形投影、整数分分配与等权对照。"""
import math
from dataclasses import dataclass,field
from fractions import Fraction
from typing import Sequence
from .wealth_portfolio_allocation import ModelError,TOL

PRODUCT_ETA=.25
PROJECTION_VERSION='portfolio-capped-simplex-v1'


@dataclass
class WeightPart:
    product_id: str
    current_fen: int
    purchase_eligible: bool
    holding_allowed: bool
    evidence: dict=field(default_factory=dict)


@dataclass
class WeightFamily:
    family_id: str
    bucket: str
    current_fen: int
    parts: list[WeightPart]
    cash: bool=False

    @property
    def unlimited(self) -> bool:
        return self.cash or any(p.purchase_eligible for p in self.parts)

    @property
    def cap_fen(self) -> int | None:
        return None if self.unlimited else sum(p.current_fen for p in self.parts if p.holding_allowed)


def simplex(y: Sequence[float], mass: float, caps: Sequence[float | None]) -> list[float]:
    """有上界单纯形KKT二分，固定100次，独立验证残差。"""
    if not math.isfinite(mass) or mass<0 or len(y)!=len(caps) or any(not math.isfinite(v) for v in y):raise ModelError('PROJECTION_INPUT_INVALID')
    if any(c is not None and (not math.isfinite(c) or c<0) for c in caps):raise ModelError('PROJECTION_INPUT_INVALID')
    if not y:
        if mass==0:return []
        raise ModelError('PRODUCT_SET_INFEASIBLE')
    if all(c is not None for c in caps) and math.fsum(caps)<mass-TOL:raise ModelError('PRODUCT_SET_INFEASIBLE')
    if mass==0:return [0.]*len(y)
    lo=min(v-mass for v in y)-1;hi=max(y)+1
    for _ in range(100):
        tau=(lo+hi)/2;w=[min(max(v-tau,0.),c) if c is not None else max(v-tau,0.) for v,c in zip(y,caps)]
        if math.fsum(w)>mass:lo=tau
        else:hi=tau
    tau=(lo+hi)/2;w=[min(max(v-tau,0.),c) if c is not None else max(v-tau,0.) for v,c in zip(y,caps)]
    if abs(math.fsum(w)-mass)>TOL or any(v<0 or (c is not None and v>c+TOL) for v,c in zip(w,caps)):raise ModelError('PROJECTION_SOLVER_UNCONFIRMED')
    return w


def round_family_amounts(weights: Sequence[float], budget_fen: int, total_fen: int, caps: Sequence[int | None]) -> list[int]:
    """最大余数依明确稳定顺序给分，严格原持仓整数上限。"""
    raw=[w*total_fen for w in weights];amounts=[max(0,math.floor(v+1e-9)) for v in raw]
    amounts=[min(a,c) if c is not None else a for a,c in zip(amounts,caps)]
    remaining=budget_fen-sum(amounts)
    if remaining<0 or remaining>len(amounts)+1:raise ModelError('PRODUCT_AMOUNT_SOLVER_UNCONFIRMED')
    order=sorted(range(len(raw)),key=lambda i:(-(raw[i]-amounts[i]),i))
    for i in order:
        if remaining and (caps[i] is None or amounts[i]<caps[i]):amounts[i]+=1;remaining-=1
    if remaining or sum(amounts)!=budget_fen or any(c is not None and a>c for a,c in zip(amounts,caps)):raise ModelError('PRODUCT_AMOUNT_PRECISION_INFEASIBLE')
    return amounts


def project_products(families: Sequence[WeightFamily], budget_fen: int, total_fen: int) -> dict:
    """不改变板块预算，保留家族与具体份额独立许可证据。"""
    if total_fen<=0 or budget_fen<0 or len({f.family_id for f in families})!=len(families):raise ModelError('PRODUCT_SET_INVALID')
    if any(f.current_fen<0 or (not f.cash and sum(p.current_fen for p in f.parts)!=f.current_fen) for f in families):raise ModelError('HOLDING_VALUATION_INVALID')
    if any(p.current_fen>0 and not p.holding_allowed for f in families for p in f.parts):raise ModelError('HOLDING_PERMISSION_UNPROVEN')
    n=len(families)
    if not n:
        if budget_fen:raise ModelError('PRODUCT_SET_INFEASIBLE')
        return {'targets':[],'equal_weight':{'status':'available','weights':[]},'HHI':None,'cash_included':False}
    mass=budget_fen/total_fen;h=[f.current_fen/total_fen for f in families];u=[mass/n]*n
    y=[(a+PRODUCT_ETA*b)/1.25 for a,b in zip(u,h)];cap_fen=[f.cap_fen for f in families]
    caps=[c/total_fen if c is not None else None for c in cap_fen]
    if all(c is not None for c in cap_fen) and sum(cap_fen)<budget_fen:raise ModelError('PRODUCT_SET_INFEASIBLE')
    w=simplex(y,mass,caps);amounts=round_family_amounts(w,budget_fen,total_fen,cap_fen)
    targets=[]
    for family,weight,amount in zip(families,w,amounts):
        parts=[];remaining=amount
        # 原不可增买份额先维持不超过原持仓，任何余额只能给合格具体份额。
        for part in sorted(family.parts,key=lambda p:(p.purchase_eligible,p.product_id)):
            if part.holding_allowed and not part.purchase_eligible:
                target=min(part.current_fen,remaining);remaining-=target
                parts.append({'product_id':part.product_id,'target_fen':target,'current_fen':part.current_fen,'direction':'maintain_or_reduce','evidence':part.evidence})
        legal=sorted([p for p in family.parts if p.purchase_eligible],key=lambda p:p.product_id)
        if remaining and not family.cash and not legal:raise ModelError('SPECIFIC_SHARE_HOLDING_LIMIT')
        for i,part in enumerate(legal):
            target=remaining if i==0 else 0
            parts.append({'product_id':part.product_id,'target_fen':target,'current_fen':part.current_fen,'direction':'purchase_eligible_target','evidence':part.evidence})
        targets.append({'family_id':family.family_id,'bucket':family.bucket,'cash':family.cash,'current_fen':family.current_fen,'target_fen':amount,'continuous_ratio':weight,'cap_fen':family.cap_fen,'parts':parts})
    ew_valid=all(c is None or Fraction(budget_fen,n)<=c for c in cap_fen)
    return {'version':PROJECTION_VERSION,'eta_product':PRODUCT_ETA,'y':y,'h':h,'equal_reference':u,'targets':targets,
            'equal_weight':{'status':'available' if ew_valid else 'baseline_infeasible','weights':u,'reason_codes':[] if ew_valid else ['HOLD_ONLY_CAP']},
            'HHI':math.fsum((a/budget_fen)**2 for a in amounts) if budget_fen else None,
            'equal_HHI':1/n if budget_fen and ew_valid else None,'cash_included':any(f.cash for f in families)}


def validate_manual_products(families: Sequence[WeightFamily], targets: dict[str,int], budget_fen: int, total_fen: int) -> dict:
    """合法用户金额原样保留，不调用投影；具体份额资格由provider再次核对。"""
    reasons=[]
    if set(targets)!={f.family_id for f in families}:reasons.append('MANUAL_PRODUCT_SET_MISMATCH')
    if any(type(a) is not int or a<0 for a in targets.values()):return {'accepted':False,'reason_codes':['MANUAL_PRODUCT_AMOUNT_INVALID'],'retained_targets_fen':None}
    if sum(targets.values())!=budget_fen:reasons.append('MANUAL_PRODUCT_BUDGET_MISMATCH')
    for f in families:
        if f.cap_fen is not None and targets.get(f.family_id,0)>f.cap_fen:reasons.append('SPECIFIC_SHARE_HOLDING_LIMIT')
    return {'accepted':not reasons,'reason_codes':sorted(set(reasons)),'retained_targets_fen':dict(targets) if not reasons else None}


