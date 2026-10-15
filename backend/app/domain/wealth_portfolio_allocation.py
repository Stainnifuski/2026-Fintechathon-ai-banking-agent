"""三板块严格凸问题的活跃集/椭圆几何解及按分校验，无网格搜索。"""
import itertools
import math
from dataclasses import dataclass
from fractions import Fraction
from decimal import Decimal,InvalidOperation
from typing import Sequence

BUCKETS = ('cash_management','stable','growth')
POLICY_VERSION = 'portfolio-policy-r1-r5-v1'
MODEL_VERSION = 'portfolio-geometric-projection-v1'
ETA = .25
DELTA = .01
TOL = 1e-10
PARAMETERS = {1:(1.,None,None),2:(.1,0.,.25),3:(.1,.25,.50),4:(.1,.5,.75),5:(.1,.75,1.)}


class ModelError(ValueError):
    def __init__(self, code: str, details: dict | None = None) -> None:
        super().__init__(code); self.code=code; self.details=details or {}


def finite(values: Sequence[float]) -> None:
    if any(not math.isfinite(v) for v in values): raise ModelError('NUMERIC_INPUT_INVALID')


def x3(z: Sequence[float]) -> list[float]:
    return [z[0],1-z[0]-z[1],z[1]]


def metric(a: Sequence[float], b: Sequence[float]) -> float:
    return 2*a[0]*b[0]+a[0]*b[1]+a[1]*b[0]+2*a[1]*b[1]


def inverse(a: Sequence[float]) -> list[float]:
    return [(2*a[0]-a[1])/3,(2*a[1]-a[0])/3]


@dataclass
class Problem:
    level: int
    p: str | None
    b: list[float]
    h: list[float] | None
    eta: float
    bounds: list[Fraction]
    constraints: list[tuple[float,float,float]]
    request_constraints: list[tuple[float,float,float]]
    center: list[float]

    def loss(self, x: Sequence[float]) -> float:
        return math.fsum((a-b)**2 for a,b in zip(x,self.b))+self.eta*math.fsum((a-b)**2 for a,b in zip(x,self.h or x))

    def valid(self, z: Sequence[float], request: bool = False, tol: float = TOL) -> bool:
        return all(a*z[0]+b*z[1] <= d+tol for a,b,d in (self.request_constraints if request else self.constraints))


def problem(level: int, h: Sequence[float] | None, available: Sequence[bool] = (True,True,True),
            maintain_caps: Sequence[str | float | Fraction] = ('1','1','1'), p: str | None = None) -> Problem:
    """默认偏好仅影响b；显式偏好才增加等式。未分类完整估值时h=None/eta=0。"""
    if type(level) is not int or level not in PARAMETERS: raise ModelError('ASSESSMENT_LEVEL_INVALID')
    cmin,p0,pmax=PARAMETERS[level]
    preference=None
    if p is not None:
        try:
            value=Decimal(p)
            if not value.is_finite():raise ValueError()
            preference=Fraction(value)
        except (ValueError,ZeroDivisionError,InvalidOperation): raise ModelError('PREFERENCE_INVALID')
        if level==1: raise ModelError('PREFERENCE_NOT_APPLICABLE_R1')
        if not 0<=preference<=Fraction(str(pmax)): raise ModelError('PREFERENCE_OUTSIDE_POLICY')
    if h is not None:
        finite(h)
        if len(h)!=3 or min(h)<0 or abs(math.fsum(h)-1)>TOL: raise ModelError('CURRENT_STRUCTURE_INVALID')
    if len(available)!=3 or len(maintain_caps)!=3: raise ModelError('AVAILABILITY_INVALID')
    caps=[Fraction(1) if available[i] or i==0 else Fraction(str(maintain_caps[i])) for i in range(3)]
    if any(v<0 or v>1 for v in caps): raise ModelError('AVAILABILITY_INVALID')
    ref= float(preference) if preference is not None else p0
    b=[1.,0.,0.] if level==1 else [.2,.8*(1-ref),.8*ref]
    eta=ETA if h is not None else 0.
    y=[(b[i]+eta*(h[i] if h is not None else 0))/(1+eta) for i in range(3)]
    # z=(c,g)，s=1-c-g；包含允许退化的直线/单点。
    A=[(-1.,0.,-cmin),(0.,-1.,0.),(1.,1.,1.),(-1.,-1.,float(caps[1])-1),(0.,1.,float(caps[2]))]
    if level==1: A += [(1.,0.,1.),(0.,1.,0.),(-1.,-1.,-1.)]
    else: A.append((pmax,1.,pmax))
    R=list(A)
    if preference is not None: R.extend([(float(preference),1.,float(preference)),(-float(preference),-1.,-float(preference))])
    return Problem(level,p,b,list(h) if h is not None else None,eta,caps,A,R,[y[0],y[2]])


def vertices(A: list[tuple[float,float,float]]) -> list[list[float]]:
    values=[]
    for (a,b,d),(e,f,q) in itertools.combinations(A,2):
        det=a*f-b*e
        if abs(det)<1e-14:continue
        z=[(d*f-b*q)/det,(a*q-d*e)/det]
        if all(m*z[0]+n*z[1]<=r+TOL for m,n,r in A): values.append(z)
    return values


def project(P: Problem, request: bool = True) -> list[float]:
    """候选为中心、每边正交投影和每个顶点；严格凸目标选择唯一最小值。"""
    A=P.request_constraints if request else P.constraints
    candidates=vertices(A);y=P.center
    if P.valid(y,request):candidates.append(y[:])
    for a,b,d in A:
        v=inverse([a,b]);den=a*v[0]+b*v[1]
        if den<=0:continue
        t=(a*y[0]+b*y[1]-d)/den;z=[y[0]-t*v[0],y[1]-t*v[1]]
        if P.valid(z,request):candidates.append(z)
    if not candidates:raise ModelError('CONSTRAINT_SET_INFEASIBLE')
    z=min(candidates,key=lambda x:P.loss(x3(x)))
    finite(z)
    if not P.valid(z,request) or abs(sum(x3(z))-1)>TOL:raise ModelError('SOLVER_VALIDATION_FAILED')
    stationarity=min((metric([z[0]-P.center[0],z[1]-P.center[1]],[v[0]-z[0],v[1]-z[1]]) for v in vertices(A)),default=0.)
    if stationarity < -TOL:raise ModelError('SOLVER_OPTIMALITY_UNCONFIRMED')
    if P.p is not None and 1-z[0]<=TOL:raise ModelError('PREFERENCE_NONCASH_UNAVAILABLE')
    return x3(z)


def boundary_points(P: Problem, star: Sequence[float], direction: list[float]) -> list[list[float]]:
    """线性目标在多边形与目标椭圆交集的极点：顶点、边界交点和椭圆切点。"""
    y=P.center;radius2=(P.loss(star)+DELTA-P.loss(x3(y)))/(1+P.eta)
    if radius2 < -TOL:raise ModelError('LOSS_BUDGET_INVALID')
    radius2=max(0.,radius2);candidates=vertices(P.constraints)+[[star[0],star[2]]]
    v=inverse(direction);den=direction[0]*v[0]+direction[1]*v[1]
    if den>0:
        scale=math.sqrt(radius2/den)
        candidates.extend([[y[0]+sign*scale*v[0],y[1]+sign*scale*v[1]] for sign in (-1,1)])
    for a,b,d in P.constraints:
        normal=inverse([a,b]);den=a*normal[0]+b*normal[1]
        t=(a*y[0]+b*y[1]-d)/den;base=[y[0]-t*normal[0],y[1]-t*normal[1]]
        offset=[base[0]-y[0],base[1]-y[1]];remaining=radius2-metric(offset,offset)
        if remaining < -TOL:continue
        tangent=[-b,a];norm=metric(tangent,tangent)
        if norm<=0:continue
        scale=math.sqrt(max(0.,remaining)/norm)
        candidates.extend([[base[0]+sign*scale*tangent[0],base[1]+sign*scale*tangent[1]] for sign in (-1,1)])
    return [z for z in candidates if P.valid(z) and P.loss(x3(z))<=P.loss(star)+DELTA+TOL]


def validate_adjustment(P: Problem, star: Sequence[float], x: Sequence[float]) -> dict:
    """当前页只验证原U，绝不重新选择b或优化回默认值。"""
    finite(x);reasons=[]
    if len(x)!=3 or min(x)<0 or abs(math.fsum(x)-1)>TOL:reasons.append('ADJUSTMENT_SUM_OR_SIGN')
    elif not P.valid([x[0],x[2]]):reasons.append('ADJUSTMENT_OUTSIDE_P')
    loss=P.loss(x) if len(x)==3 else None
    if loss is not None and loss>P.loss(star)+DELTA+TOL:reasons.append('ADJUSTMENT_OUTSIDE_U')
    return {'accepted':not reasons,'reason_codes':reasons,'retained_weights':list(x) if not reasons else None,'loss':loss}


def intervals(P: Problem, star: Sequence[float]) -> dict:
    ranges=[];witness=[]
    for direction,offset in [([1.,0.],0.),([-1.,-1.],1.),([0.,1.],0.)]:
        points=boundary_points(P,star,direction)
        if not points:raise ModelError('INTERVAL_SOLVER_UNCONFIRMED')
        pair=[min(points,key=lambda z:direction[0]*z[0]+direction[1]*z[1]),max(points,key=lambda z:direction[0]*z[0]+direction[1]*z[1])]
        ranges.append([max(0.,min(1.,offset+direction[0]*z[0]+direction[1]*z[1])) for z in pair]);witness.append([x3(z) for z in pair])
    slider=None;slider_witness=None;c=star[0]
    if 1-c>TOL:
        # 固定c后的平方二次式，中心g=(y_g-(c-y_c)/2)。
        center=P.center[1]-(c-P.center[0])/2
        base=[c,center];remaining=(P.loss(star)+DELTA-P.loss(x3(base)))/(1+P.eta)
        if remaining < -TOL:raise ModelError('SLIDER_SOLVER_UNCONFIRMED')
        width=math.sqrt(max(0.,remaining)/2);lo,hi=center-width,center+width
        for a,b,d in P.constraints:
            if b>0:hi=min(hi,(d-a*c)/b)
            elif b<0:lo=max(lo,(d-a*c)/b)
            elif a*c>d+TOL:raise ModelError('SLIDER_SOLVER_UNCONFIRMED')
        if lo>hi+TOL:raise ModelError('SLIDER_SOLVER_UNCONFIRMED')
        slider=[max(0.,lo/(1-c)),min(1.,hi/(1-c))];slider_witness=[x3([c,lo]),x3([c,hi])]
    return {'ranges':ranges,'range_witnesses':witness,'slider':slider,'slider_witnesses':slider_witness}


def integer_valid(P: Problem, amounts: Sequence[int], total: int, request: bool = True) -> bool:
    """整数分硬约束使用Fraction精确比较，浮点容差不能放过一分钱。"""
    if type(total) is not int or any(type(a) is not int for a in amounts):return False
    if len(amounts)!=3 or min(amounts)<0 or sum(amounts)!=total or total<=0:return False
    c,s,g=[Fraction(a,total) for a in amounts]
    if P.level==1:return c==1 and s==g==0 and P.p is None
    cmin,_,pmax=PARAMETERS[P.level]
    if c<Fraction(str(cmin)) or g>Fraction(str(pmax))*(1-c) or s>P.bounds[1] or g>P.bounds[2]:return False
    if request and P.p is not None and (g!=Fraction(P.p)*(1-c) or c==1):return False
    return True


def round_amounts(P: Problem, star: Sequence[float], total: int) -> dict:
    """有界局部/可表示性修正，不遍历T。精度不足与未证实求解失败分别记录。"""
    if type(total) is not int or total<=0:raise ModelError('NO_CONFIGURABLE_ASSETS' if total==0 else 'TOTAL_ASSETS_INVALID')
    candidates=[]
    if P.p is not None:
        p=Fraction(P.p);den=p.denominator;center=total*(1-star[0]);base=int(round(center/den))
        for j in range(max(1,base-3),base+4):
            noncash=j*den;g=j*p.numerator;amount=[total-noncash,noncash-g,g]
            if integer_valid(P,amount,total):candidates.append(amount)
        if not candidates:raise ModelError('AMOUNT_PRECISION_PREFERENCE_UNREPRESENTABLE',{'denominator':den,'total_fen':total})
    else:
        c0=int(round(total*star[0]));g0=int(round(total*star[2]))
        for c in range(c0-3,c0+4):
            for g in range(g0-3,g0+4):
                amount=[c,total-c-g,g]
                if integer_valid(P,amount,total):candidates.append(amount)
        if not candidates:raise ModelError('AMOUNT_SOLVER_UNCONFIRMED')
    valid=[a for a in candidates if P.loss([v/total for v in a])<=P.loss(star)+DELTA+1e-12]
    if not valid:raise ModelError('AMOUNT_PRECISION_OUTSIDE_U')
    amount=min(valid,key=lambda a:(math.fsum((a[i]/total-star[i])**2 for i in range(3)),tuple(a)))
    actual=[v/total for v in amount]
    return {'amounts_fen':amount,'actual_ratios':actual,'actual_p':None if amount[0]==total else amount[2]/(total-amount[0]),'P_valid':True,'U_valid':True}


def allocate(level: int, total: int, h: Sequence[float] | None, available: Sequence[bool]=(True,True,True),
             maintain_caps: Sequence[str | float | Fraction]=('1','1','1'), p: str | None=None) -> dict:
    P=problem(level,h,available,maintain_caps,p);star=project(P);ranges=intervals(P,star);amounts=round_amounts(P,star,total)
    return {'policy_version':POLICY_VERSION,'model_version':MODEL_VERSION,'b':P.b,'h':P.h,'eta':P.eta,'delta':DELTA,
            'constraints':P.constraints,'request_constraints':P.request_constraints,'continuous_solution':star,
            'objective':P.loss(star),'tolerance':TOL,'integer_constraint_tolerance_fen':0,'converged':True,
            'P_valid':P.valid([star[0],star[2]],True),'U_valid':True,'solver_method':'active_constraints_ellipse_geometry','sum_residual':abs(math.fsum(star)-1),'max_request_constraint_violation':max(0.,max(a*star[0]+b*star[2]-d for a,b,d in P.request_constraints)),'optimality_min_directional_derivative':min((metric([star[0]-P.center[0],star[2]-P.center[1]],[v[0]-star[0],v[1]-star[2]]) for v in vertices(P.request_constraints)),default=0.),'integer_U_residual':P.loss(amounts['actual_ratios'])-P.loss(star)-DELTA,**ranges,**amounts}
