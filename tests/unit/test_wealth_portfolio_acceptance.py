"""评价口径解析例与第06步实质验收空白，不调用上游或生产数据库。"""
import math
from pathlib import Path
from copy import deepcopy
import pytest
from backend.app.domain.wealth_portfolio_acceptance import total_return_metrics,buy_and_hold_series,allocation_comparison,product_comparison
from backend.app.domain.wealth_portfolio_allocation import allocate,ETA,DELTA,intervals,problem,project
from backend.app.domain.wealth_portfolio_projection import PRODUCT_ETA,WeightFamily,WeightPart,project_products
from backend.app.services.orchestration.wealth_portfolio_model import adapt_candidate_group
from backend.app.domain.wealth_portfolio_candidates import pipeline
from backend.app.schemas.wealth_portfolio_candidates import CandidateFilter
from scripts.wealth_portfolio_acceptance import repeated_flows,explanations,sensitivity
from scripts.wealth_portfolio_acceptance import request,record_hash
from backend.app.schemas.wealth_portfolio_lifecycle import PortfolioCreate
from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateError
from backend.app.domain.wealth_portfolio_allocation import ModelError
from tests.fixtures.wealth_portfolio_lifecycle_v1 import lifecycle_fixture
from tests.fixtures.wealth_portfolio_candidates_v1 import NOW


def test_analytic_total_return_sample_std_peak_and_drawdown():
    out=total_return_metrics(['2026-09-01','2026-09-02','2026-09-03','2026-09-04'],[100.,110.,99.,118.8],consecutive_trading_days=True)
    assert out['total_return']==pytest.approx(.188)
    # 日回报 .1,-.1,.2；样本方差 7/300。
    assert out['daily_volatility']==pytest.approx(math.sqrt(7/300))
    assert out['annualized_volatility']==pytest.approx(math.sqrt(7/300)*math.sqrt(252))
    assert out['maximum_drawdown']==pytest.approx(.1)


def test_missing_or_nonconsecutive_never_fakes_annualization():
    one=total_return_metrics(['2026-09-01'],[1.])
    assert one['daily_volatility'] is None and one['total_return']==one['maximum_drawdown']==0
    gap=total_return_metrics(['2026-09-01','2026-09-10','2026-09-15'],[1.,.9,1.])
    assert gap['annualized_volatility'] is None and gap['annualization_convention'] is None


@pytest.mark.parametrize('dates,indices',[([],[]),(['2026-09-01'],[0]),(['2026-09-01'],[float('nan')]),(['2026-09-02','2026-09-01'],[1,2]),(['2026-09-01','2026-09-01'],[1,2])])
def test_return_inputs_fail_closed(dates,indices):
    with pytest.raises(ValueError):total_return_metrics(dates,indices)


def test_buy_hold_uses_initial_units_not_daily_constant_weights():
    actual=buy_and_hold_series([[1,2,1],[1,1,2]],[.5,.5])
    assert actual==[1,1.5,1.5] # 每日再平衡则末期1.875，与买入持有不同。
    with pytest.raises(ValueError,match='CASH_RETURN_UNKNOWN'):buy_and_hold_series([[1,2]],[.5],cash_weight=.5)
    assert buy_and_hold_series([[1,2]],[.5],cash_weight=.5,cash_index=[1,1])==[1,1.5]


def test_reference_default_matches_actual_geometry_and_product_projection():
    h=[.2,.3,.5];actual=allocate(4,1000000,h);r=allocation_comparison(4,h)
    assert r['x_star']==pytest.approx(actual['continuous_solution'])
    for a,b in zip(r['ranges'],actual['ranges']):assert a==pytest.approx(b)
    assert r['slider']==pytest.approx(actual['slider'])
    fs=[WeightFamily('a','stable',100,[WeightPart('A',100,False,True)]),WeightFamily('b','stable',0,[WeightPart('C',0,True,True)])]
    assert product_comparison(fs,600,1000)['amounts_fen']==[x['target_fen'] for x in project_products(fs,600,1000)['targets']]


def test_sensitivity_does_not_move_formal_parameters_or_delta_star():
    result=sensitivity();d=[r for r in result['rows'] if r['parameter']=='delta']
    assert d[0]['x_star']==d[1]['x_star']==d[2]['x_star']
    assert d[0]['slider_width']<d[1]['slider_width']<d[2]['slider_width']
    assert ETA==PRODUCT_ETA==.25 and DELTA==.01
    assert all(r.get('comparison_only') and r['hard_constraints_passed'] for r in result['rows'])
    assert next(r for r in result['rows'] if r['parameter']=='unknown_h')['distance_to_current'] is None
    for r in result['rows']:
        if r['parameter']=='eta_product':assert r['amounts_fen'][0]<=100000 and r['equal_status']=='baseline_infeasible'


@pytest.mark.asyncio
async def test_three_actual_service_flows_repeat_business_end_state():
    r=await repeated_flows();assert r['repetitions']==3 and len(set(r['hashes']))==1
    assert len(r['sample']['selected_create']['selected_positions'])==6
    assert r['sample']['small_adjustments']['version']==4 and r['sample']['preference']['version']==5


@pytest.mark.asyncio
async def test_explanations_match_policy_facts_and_missing_status():
    rows=await explanations();assert len(rows)==6
    r1=rows[0];assert [b['target_fen'] for b in r1['plan']['buckets']]==[1000000,0,0] and r1['plan']['growth_ratio_ex_cash'] is None
    r4=rows[2];assert r4['plan']['growth_ratio_ex_cash']=='0.625'
    assert rows[-1]['code']=='ASSET_SUMMARY_UNAVAILABLE'
    assert all('收益相关性' in r['explanation'] for r in rows[:-1])


@pytest.mark.asyncio
async def test_unknown_material_external_unavailable_carries_causes():
    s,data,facts,*_=lifecycle_fixture();f,index,now=await s.provider.candidates.current('fixture-user')
    for p in index.products.values():p.fields['formal_risk'].status='missing';p.fields['formal_risk'].value=None;p.risk_evidence=[]
    raw=await s.provider.candidates.candidates('fixture-user',CandidateFilter(mode='general'))
    group=adapt_candidate_group(raw,index,CandidateFilter(mode='general'))
    assert raw['status']=='no_eligible_products' and group.status=='unavailable'
    assert not group.items and 'PRODUCT_RISK_UNKNOWN' in group.reason_codes


@pytest.mark.asyncio
async def test_assessment_downgrade_closes_old_draft_without_mutation():
    s,_,facts,*_=lifecycle_fixture();p=await s.create('fixture-user',PortfolioCreate(context_id='fixture-context'))
    before=record_hash(s,p)
    facts.assessment.capacity_level=2
    s.provider.asset_source.bundle['assessment']['capacity_level']=2
    with pytest.raises((CandidateError,ModelError)):
        await s.revise('fixture-user',p.portfolio_id,request(p,growth_ratio_ex_cash='.51'))
    with pytest.raises((CandidateError,ModelError)):
        await s.get('fixture-user',p.portfolio_id,'fixture-context')
    assert record_hash(s,p)==before
