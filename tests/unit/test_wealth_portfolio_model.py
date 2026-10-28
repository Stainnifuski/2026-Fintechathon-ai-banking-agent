import asyncio
import math
from copy import deepcopy
from fractions import Fraction
import pytest
from backend.app.domain.wealth_portfolio_allocation import *
from backend.app.domain.wealth_portfolio_dpp import *
from backend.app.domain.wealth_portfolio_projection import *
from backend.app.services.orchestration.wealth_portfolio_model import PortfolioModelProvider
from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateError
from backend.app.schemas.wealth_portfolio_candidates import CandidateFilter,NextBatchRequest,SessionSelectionRequest
from tests.fixtures.wealth_portfolio_candidates_v1 import share,NOW
from tests.fixtures.wealth_portfolio_model_v1 import model_fixture


def run(c):return asyncio.run(c)


def test_analytic_A_and_B_independent_formulas():
    A=allocate(4,1000000,[.2,.3,.5]);assert A['continuous_solution']==pytest.approx([.2,.38,.42],abs=1e-12);assert A['actual_p']==pytest.approx(.525)
    B=allocate(4,1000000,[.2,.3,.5],p='.625')
    assert B['continuous_solution']==pytest.approx([.2,.3,.5],abs=1e-12)
    half=math.sqrt(.01/1.25*2/3);slide=math.sqrt(.01/1.25/2)/.8
    for i,v in enumerate([.2,.3,.5]):assert B['ranges'][i]==pytest.approx([v-half,v+half],abs=1e-11)
    assert B['slider']==pytest.approx([.625-slide,.625+slide],abs=1e-11)
    P=problem(4,[.2,.3,.5],p='.625');bad=[.2+half,.3,.5-half]
    assert P.loss(bad)==pytest.approx(.0133333333333333,abs=1e-12)
    assert validate_adjustment(P,B['continuous_solution'],bad)['reason_codes']==['ADJUSTMENT_OUTSIDE_U']


@pytest.mark.parametrize('level,default,pmax',[(2,0,.25),(3,.25,.5),(4,.5,.75),(5,.75,1.)])
def test_policy_explicit_default_and_intervals(level,default,pmax):
    for p in [None,str(default),str(pmax)]:
        result=allocate(level,1000000,[.1,.8,.1],p=p);P=problem(level,[.1,.8,.1],p=p)
        assert result['P_valid'] and integer_valid(P,result['amounts_fen'],1000000)
        for pair in result['range_witnesses']:
            for point in pair:assert validate_adjustment(P,result['continuous_solution'],point)['accepted']
        for point in result['slider_witnesses'] or []:assert validate_adjustment(P,result['continuous_solution'],point)['accepted']
        if p is not None:assert result['actual_p']==pytest.approx(float(p),abs=1e-13)
    with pytest.raises(ModelError,match='PREFERENCE_OUTSIDE_POLICY'):allocate(level,10000,None,p=str(pmax+.01))


def test_R1_no_non_cash_preference_and_all_cash_degenerate():
    r=allocate(1,13,[.2,.3,.5]);assert r['amounts_fen']==[13,0,0] and r['slider'] is None
    with pytest.raises(ModelError,match='PREFERENCE_NOT_APPLICABLE_R1'):allocate(1,100,None,p='0')
    r=allocate(4,1000,None,available=[True,False,False],maintain_caps=[1,0,0]);assert r['amounts_fen']==[1000,0,0] and r['actual_p'] is None
    with pytest.raises(ModelError,match='PREFERENCE_NONCASH_UNAVAILABLE'):allocate(4,1000,None,[True,False,False],[1,0,0],p='.5')


@pytest.mark.parametrize('total',[1,3,7,13,31,101])
def test_small_integer_no_one_fen_risk_breach(total):
    for level in range(1,6):
        try:r=allocate(level,total,[.2,.3,.5])
        except ModelError as e:assert e.code in ['AMOUNT_PRECISION_OUTSIDE_U','AMOUNT_SOLVER_UNCONFIRMED'];continue
        assert integer_valid(problem(level,[.2,.3,.5]),r['amounts_fen'],total)
        if level>1:assert r['amounts_fen'][0]>=math.ceil(total*.1)


def test_explicit_p_unrepresentable_and_caps():
    with pytest.raises(ModelError,match='AMOUNT_PRECISION_PREFERENCE_UNREPRESENTABLE'):allocate(4,7,None,p='.625')
    r=allocate(4,10000,None,[True,False,False],[1,'0.12','0.07'])
    assert r['amounts_fen'][1]<=1200 and r['amounts_fen'][2]<=700
    assert not integer_valid(problem(2,None),[10,67,23],100)
    with pytest.raises(ModelError,match='PREFERENCE_INVALID'):problem(4,None,p='NaN')
    with pytest.raises(ModelError,match='NUMERIC_INPUT_INVALID'):problem(4,[float('nan'),0,1])
    with pytest.raises(ModelError,match='NO_CONFIGURABLE_ASSETS'):allocate(4,0,None)


def test_fixed_text_counts_and_idf_no_cross_fields():
    assert tokens({'objective':'ＡＢＡＢ！Z','strategy':'CD'})=={'ab':2,'ba':1,'z':1,'cd':1}
    provider,_,facts,_=model_fixture();f,index,now=run(provider.candidates.current('fixture-user'))
    from backend.app.domain.wealth_portfolio_candidates import pipeline
    pool=pipeline(index,CandidateFilter(mode='general'),f,'fixture-user',now).family_candidates[:2]
    pool[0].text_evidence={'objective':'ABAB','strategy':'Z'};pool[1].text_evidence={'objective':'AB','strategy':'Q'}
    corpus=build_corpus(pool);assert corpus.idf['ab']==1 and corpus.idf['z']==pytest.approx(1+math.log(3/2))
    assert corpus.counts[pool[0].family_id]['ab']==2
    textnorm=math.sqrt((1+math.log(2))**2+(1+math.log(3/2))**2*2)
    assert corpus.vectors[pool[0].family_id]['text:ab']==pytest.approx(math.sqrt(.8)*(1+math.log(2))/textnorm)
    for v in corpus.vectors.values():assert dot(v,v)==pytest.approx(1)


def determinant(A):
    if len(A)==0:return 1.
    if len(A)==1:return A[0][0]
    return sum((-1)**j*A[0][j]*determinant([row[:j]+row[j+1:] for row in A[1:]]) for j in range(len(A)))


def test_DPP_matches_independent_determinant_and_stops_duplicates():
    provider,_,_,_=model_fixture();facts,index,now=run(provider.candidates.current('fixture-user'))
    from backend.app.domain.wealth_portfolio_candidates import pipeline
    pool=pipeline(index,CandidateFilter(mode='general'),facts,'fixture-user',now).family_candidates;corpus=build_corpus(pool);result=greedy(corpus,set())
    chosen=[]
    for _ in range(3):
        options={f:determinant([[dot(corpus.vectors[a],corpus.vectors[b]) for b in chosen+[f]] for a in chosen+[f]]) for f in corpus.vectors if f not in chosen}
        winner=min(options,key=lambda f:(-options[f],f));chosen.append(winner)
    assert result['items']==chosen and len(result['items'])==3
    assert result['trace'][1]['residual_squared']<1 and result['trace'][1]['residual_squared']>1e-10
    repeated=deepcopy(pool[:3])
    for p in repeated:p.text_evidence=deepcopy(pool[0].text_evidence);p.issuer_identity=pool[0].issuer_identity;p.substantive_category=pool[0].substantive_category
    c=build_corpus(repeated);assert len(greedy(c,set())['items'])==1 and len(mmr(c,set())['items'])==1
    assert greedy(c,set())['stop_reason']=='feature_rank_exhausted'
    assert greedy(c,set())['items']==[min(p.family_id for p in repeated)]


def test_projection_non_equal_new_single_zero_cap_and_equal_baseline():
    fs=[WeightFamily('a','stable',400,[WeightPart('A',400,True,True)]),WeightFamily('b','stable',0,[WeightPart('B',0,True,True)])]
    result=project_products(fs,600,1000);assert [t['target_fen'] for t in result['targets']]==[340,260]
    fs[0].parts[0].purchase_eligible=False
    assert project_products(fs,900,1000)['targets'][0]['target_fen']<=400
    fs[0].current_fen=fs[0].parts[0].current_fen=100
    result=project_products(fs,600,1000);assert result['equal_weight']['status']=='baseline_infeasible'
    with pytest.raises(ModelError,match='PRODUCT_SET_INFEASIBLE'):project_products(fs[:1],101,1000)
    fs=[WeightFamily('a','stable',0,[WeightPart('A',0,True,True)]),WeightFamily('b','stable',0,[WeightPart('B',0,True,True)])]
    assert [x['target_fen'] for x in project_products(fs,600,1000)['targets']]==[300,300]
    assert project_products(fs[:1],600,1000)['targets'][0]['target_fen']==600
    assert project_products(fs,0,1000)['HHI'] is None
    assert validate_manual_products(fs,{'a':400,'b':200},600,1000)['retained_targets_fen']=={'a':400,'b':200}


def test_cash_component_receivable_and_specific_share_no_increase():
    fs=[WeightFamily('__cash_asset__','cash_management',500,[],True),WeightFamily('f','cash_management',200,[WeightPart('C',200,False,True),WeightPart('A',0,True,True)])]
    r=project_products(fs,800,1000);cash,product=r['targets'];assert r['cash_included'];assert cash['target_fen']+product['target_fen']==800
    assert next(p for p in product['parts'] if p['product_id']=='C')['target_fen']<=200


@pytest.mark.parametrize('filter',[CandidateFilter(mode='general'),CandidateFilter(mode='category',category='bond_fund')])
def test_formal_code_order_provider_independent_recommendation(filter):
    provider,_,_,_=model_fixture();r=run(provider.recommend('fixture-user',filter));assert r.status=='ok' and len(r.candidate_groups[0].items)>=1
    assert r.metadata['selection']['algorithm']=='portfolio-code-category-round-robin-v1' and r.metadata['fixture_only']


@pytest.mark.parametrize('level',[1,2,4])
def test_provider_plan_contract_and_cash_not_payable(level):
    provider,_,_,_=model_fixture(level,True);r=run(provider.plan('fixture-user'));assert r.status=='ok';p=r.plan
    assert sum(b.target_fen for b in p.buckets)==1000000 and r.metadata['available_cash_fen']==190000 and p.selected_positions==[]
    assert p.model_status=='fixture' and r.financial_effect_status=='not_evaluated'
    if level==4:assert r.metadata['model']['continuous_solution']==pytest.approx([.2,.38,.42])


def test_same_planning_facts_have_same_candidate_content_across_entries():
    provider,_,_,_=model_fixture()
    plans=[run(provider.plan('fixture-user')).plan for _ in range(4)]
    def visible_content(plan):
        return [(group.bucket,[(item.family_id,item.name,item.reason,
                    [share.product_id for share in item.share_classes]) for item in group.items])
                for group in plan.candidate_groups]
    assert all(visible_content(plan)==visible_content(plans[0]) for plan in plans[1:])
    assert all([(bucket.bucket,bucket.target_fen) for bucket in plan.buckets]
               ==[(bucket.bucket,bucket.target_fen) for bucket in plans[0].buckets] for plan in plans[1:])
    assert all('RANDOM_PLANNING_DISPLAY' not in item.reason_codes
               for plan in plans for group in plan.candidate_groups for item in group.items)


def test_risk_gate_not_replaced_by_DPP_manual_or_old_cursor():
    provider,data,facts,_=model_fixture(2);data.products[2].fields['formal_risk'].value=4;facts.offers['Q01A'].risk_level=4
    with pytest.raises(ModelError,match='NEW_SELECTION_INELIGIBLE'):run(provider.plan('fixture-user',selected=[share('Q01A','0')]))


def test_missing_source_no_fixture_fallback():
    provider,_,_,_=model_fixture();provider.candidates.source=None
    with pytest.raises(CandidateError,match='ASSESSMENT_SOURCE_UNAVAILABLE'):run(provider.plan('fixture-user'))


def test_selected_retained_across_deterministic_batches():
    provider,_,_,_=model_fixture();engine=provider.candidates
    result=run(engine.candidates('fixture-user',CandidateFilter(mode='general')));s=result['session'];version=s['pool_version']
    selected=run(engine.select('fixture-user',SessionSelectionRequest(session_id=s['session_id'],expected_version=s['version'],items=[share(f'Q{i:02d}A') for i in range(6)])));s=selected['session'];assert len(s['selected'])==6
    nxt=run(engine.next_batch('fixture-user',NextBatchRequest(session_id=s['session_id'],expected_version=s['version'],cursor=s['cursor'])))
    assert nxt['session']['pool_version']==version and len(nxt['session']['selected'])==6

def test_asset_missing_zero_unclassified_and_F08_F18_F10_preserved():
    from backend.app.services.orchestration.wealth_portfolio import FixtureSnapshotSource,build_snapshot
    from scripts.generate_wealth_portfolio_fixtures import generate
    for name in ['F08','F18']:
        s=build_snapshot(generate()[name]['input']['bundle']);assert s.total_assets_fen==1000000
    assert not build_snapshot(generate()['F10']['input']['bundle']).valuation_complete
    provider,_,_,b=model_fixture()
    b['accounts'][0].update(balance_fen=0,available_fen=0);b['summary'].update(cash_balance_fen=0,available_cash_fen=0,total_assets_fen=0)
    provider.asset_source=FixtureSnapshotSource(b)
    assert run(provider.plan('fixture-user')).status=='no_configurable_assets'
    provider,data,facts,b=model_fixture(4,True)
    b['lots'][0].update(market_value_fen=None,nav=None,nav_date=None)
    b['summary'].update(positions_market_value_fen=None,total_assets_fen=None,valuation_complete=False,missing_fields=['missing_NAV'])
    provider.asset_source=FixtureSnapshotSource(b);r=run(provider.plan('fixture-user'));assert r.status=='unavailable' and r.plan is None
    provider,data,facts,b=model_fixture(4,True);b['lots'][0]['category']='unknown';provider.asset_source=FixtureSnapshotSource(b)
    r=run(provider.plan('fixture-user'));assert r.metadata['model']['eta']==0 and r.metadata['model']['h'] is None and r.metadata['eta_disabled_reasons']


def test_overrisk_holding_maintenance_allowed_no_new_ac_bypass():
    provider,data,facts,b=model_fixture(2,True)
    for p in data.products:
        if p.product_id=='Q03A':p.fields['formal_risk'].value=4;facts.offers[p.product_id].risk_level=4
    r=run(provider.plan('fixture-user'));assert r.status=='ok'
    parts=[p for out in r.metadata['product_projections'].values() for f in out['targets'] for p in f['parts'] if p['product_id']=='Q03A']
    assert parts and parts[0]['target_fen']<=500000 and parts[0]['direction']=='maintain_or_reduce'
    facts.offers['Q03A'].holding_allowed=None
    provider.candidates.indexes.cache.clear();r=run(provider.plan('fixture-user'))
    assert all(p['product_id']!='Q03A' for out in r.metadata['product_projections'].values() for f in out['targets'] for p in f['parts'])
    assert any(row['position']['product_id']=='Q03A' for row in r.metadata['positions'])


def test_pure_manual_validator_preserves_user_values_and_rejects_caps():
    from backend.app.services.orchestration.wealth_portfolio_data import read_asset_model_facts
    provider,data,_,_=model_fixture(4,True);r=run(provider.plan('fixture-user'))
    facts,index,now=run(provider.candidates.current('fixture-user'));asset=run(read_asset_model_facts(provider.asset_source,'fixture-user',list(index.products.values())))
    meta=r.metadata['validation_contract'];amounts=r.metadata['model']['amounts_fen'];targets={f['family_id']:f['target_fen'] for o in r.metadata['product_projections'].values() for f in o['targets']}
    # 在同一板块主动移100分，合法值必须原样保留，不优化回去。
    stable=[f['family_id'] for f in r.metadata['product_projections']['stable']['targets']];targets[stable[0]]+=100;targets[stable[1]]-=100
    check=provider.validate_weights('fixture-user',facts,index,now,asset,meta,targets,amounts)
    assert check['accepted'] and check['retained_targets_fen']==targets
    with pytest.raises(ModelError,match='MODEL_FACTS_CHANGED'):provider.validate_weights('fixture-user',facts,index,now,asset,{**meta,'snapshot_id':'old'},targets,amounts)
    facts.offers[meta['selected_references'][0]['product_id']].purchase_allowed=False
    with pytest.raises(ModelError,match='MODEL_FACTS_CHANGED'):provider.validate_weights('fixture-user',facts,index,now,asset,meta,targets,amounts)


def test_output_other_share_null_keeps_internal_identity():
    from backend.app.services.orchestration.wealth_portfolio_model import adapt_candidate_group
    provider,data,facts,_=model_fixture()
    for p in data.products[:2]:
        p.share_class='B' if p.product_id.endswith('A') else 'R';facts.offers[p.product_id].share_class=p.share_class
        for e in p.family_evidence:e.share_classes[p.product_id]=p.share_class
    r=run(provider.recommend('fixture-user',CandidateFilter(mode='general',bucket='cash_management')))
    item=next(f for f in r.candidate_groups[0].items if f.family_id=='fixture-family-00')
    assert all(s.share_class is None for s in item.share_classes)
    assert item.evidence['default_share']['share_class']=='B'


def test_dpp_corpus_data_version_changes_and_no_quadratic_gram():
    provider,_,_,_=model_fixture();facts,index,now=run(provider.candidates.current('fixture-user'))
    from backend.app.domain.wealth_portfolio_candidates import pipeline
    pool=pipeline(index,CandidateFilter(mode='general'),facts,'fixture-user',now).family_candidates
    selector=DppSelector();selector.select(pool,set(),3);v=selector.last_result['corpus_version'];selector.select(pool,{pool[0].family_id},3)
    assert selector.builds==1 and selector.last_result['corpus_version']==v
    changed=deepcopy(pool)
    for p in changed:p.data_version='new-source-data'
    selector.select(changed,set(),3);assert selector.builds==2 and selector.last_result['corpus_version']!=v
    c=selector.corpus(changed);assert isinstance(c.vectors[pool[0].family_id],dict) and not hasattr(c,'gram')


def test_normal_time_re_read_does_not_break_pool_version():
    from datetime import timedelta
    provider,_,_,_=model_fixture();e=provider.candidates;r=run(e.candidates('fixture-user',CandidateFilter(mode='general')));s=r['session']
    e.clock=lambda:NOW+timedelta(seconds=1)
    n=run(e.next_batch('fixture-user',NextBatchRequest(session_id=s['session_id'],expected_version=s['version'],cursor=s['cursor'])))
    assert n['items'] and e.indexes.loads==1


def test_evaluation_empty_metrics_and_same_pool_baseline_failure():
    from backend.app.domain.wealth_portfolio_evaluation import evaluate_selector,evaluate_weights
    empty=evaluate_selector(build_corpus([]),'DPP',set(),set(),0)
    assert empty['matching_compliance'] is None and empty['qualification_compliance'] is None and empty['display_coverage'] is None and empty['ILAD'] is None
    fs=[WeightFamily('a','stable',100,[WeightPart('A',100,False,True)]),WeightFamily('b','stable',0,[WeightPart('B',0,True,True)])]
    out=project_products(fs,600,1000);ev=evaluate_weights({'stable':out},{'a':100,'b':0,'unselected_old':300},1000,{'actual_p':None},None)
    assert ev['equal_status']=='baseline_infeasible' and ev['equal_adjustment_proxy'] is None
    assert ev['projection_adjustment_proxy']==pytest.approx(.4)
    assert evaluate_weights({'stable':out},{},1000,{'actual_p':None},None,False)['projection_adjustment_proxy'] is None


def decimal_solve(matrix,vector):
    from decimal import Decimal,localcontext
    with localcontext() as ctx:
        ctx.prec=60;A=[[Decimal(str(v)) for v in row]+[Decimal(str(vector[i]))] for i,row in enumerate(matrix)]
        for col in range(len(A)):
            pivot=next((r for r in range(col,len(A)) if abs(A[r][col])>Decimal('1e-45')),None)
            if pivot is None:return None
            A[col],A[pivot]=A[pivot],A[col];d=A[col][col];A[col]=[x/d for x in A[col]]
            for row in range(len(A)):
                if row!=col:
                    d=A[row][col];A[row]=[a-d*b for a,b in zip(A[row],A[col])]
        return [float(row[-1]) for row in A]


@pytest.mark.parametrize('level',[2,3,4,5])
def test_independent_3d_high_precision_KKT_reference(level):
    # 生产为2维投影；参照为3维Decimal60位KKT活跃集穷举，规模固定。
    import itertools
    for h,p,available,caps in [([.1,.2,.7],None,[True,True,True],[1,1,1]),([.8,.1,.1],None,[True,False,True],[1,.12,1]),([.2,.3,.5],str(PARAMETERS[level][1]),[True,True,True],[1,1,1])]:
        P=problem(level,h,available,caps,p);star=project(P);eta=P.eta;y=[(P.b[i]+eta*h[i])/(1+eta) for i in range(3)]
        inequalities=[([-1,0,0],-.1),([0,-1,0],0),([0,0,-1],0),([PARAMETERS[level][2],0,1],PARAMETERS[level][2]),([0,1,0],float(P.bounds[1])),([0,0,1],float(P.bounds[2]))]
        equalities=[([1,1,1],1)]
        if p is not None:equalities.append(([float(p),0,1],float(p)))
        candidates=[]
        for count in range(3):
            for active in itertools.combinations(inequalities,count):
                rows=equalities+list(active);m=len(rows);A=[[0.]*(3+m) for _ in range(3+m)];rhs=y+[v for row,v in rows]
                for i in range(3):A[i][i]=1
                for j,(row,b) in enumerate(rows):
                    for i in range(3):A[i][3+j]=row[i];A[3+j][i]=row[i]
                z=decimal_solve(A,rhs)
                if z and all(sum(a*b for a,b in zip(row,z[:3]))<=bound+1e-10 for row,bound in inequalities):candidates.append(z[:3])
        reference=min(candidates,key=lambda z:sum((z[i]-y[i])**2 for i in range(3)))
        assert star==pytest.approx(reference,abs=2e-10)

def test_no_holding_permission_never_valid_maintain_target():
    with pytest.raises(ModelError,match='HOLDING_PERMISSION_UNPROVEN'):
        project_products([WeightFamily('f','stable',100,[WeightPart('A',100,True,False)])],100,1000)


def test_declared_rank_two_and_numerical_kernel_errors():
    provider,_,_,_=model_fixture();facts,index,now=run(provider.candidates.current('fixture-user'))
    from backend.app.domain.wealth_portfolio_candidates import pipeline
    pool=deepcopy(pipeline(index,CandidateFilter(mode='general'),facts,'fixture-user',now).family_candidates[:3])
    for p in pool:p.issuer_identity='same';p.substantive_category='bond';p.text_evidence={'strategy':'AB'}
    pool[-1].text_evidence={'strategy':'CD'};c=build_corpus(pool)
    assert len(greedy(c,set())['items'])==2
    with pytest.raises(ValueError,match='FEATURE_KERNEL_INVALID'):greedy(Corpus('bad',{'bad':{'x':2}},['x'],{'x':1},{'bad':{}},1),set())
    for p in pool:p.text_evidence={'strategy':'！ -'}
    with pytest.raises(ValueError,match='FEATURE_TEXT_EMPTY'):build_corpus(pool)


def test_current_page_vs_new_explicit_preference_semantics():
    A=allocate(4,1000000,[.2,.3,.5]);P=problem(4,[.2,.3,.5]);adjust=[.2,.35,.45]
    assert validate_adjustment(P,A['continuous_solution'],adjust)['retained_weights']==adjust
    B=allocate(4,1000000,[.2,.3,.5],p='.625')
    assert B['continuous_solution']!=A['continuous_solution'] and B['actual_p']==pytest.approx(.625)


def test_full_fact_version_changes_during_output_rejected():
    provider,_,_,_=model_fixture();source=provider.candidates.source
    original=source.read_current
    async def changed(user_id,at):
        v=await original(user_id,at)
        if source.calls>=2:v.offers_version=2
        return v
    source.read_current=changed
    with pytest.raises(ModelError,match='MODEL_FACTS_CHANGED'):run(provider.plan('fixture-user'))


def test_no_selected_non_cash_target_returns_partial_not_shifted_to_cash():
    provider,_,_,_=model_fixture();r=run(provider.plan('fixture-user',selected=[]))
    assert r.status=='partial' and r.plan is None
    assert 'stable:PRODUCT_SET_INFEASIBLE' in r.missing_fields and r.metadata['model']['amounts_fen'][1]>0
