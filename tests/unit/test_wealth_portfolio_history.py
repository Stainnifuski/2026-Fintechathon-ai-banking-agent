"""Step09 parser/method/scenario/time barriers; analytic inputs are NOT market evidence."""
import json
from datetime import datetime,timezone
from pathlib import Path
import pytest
from backend.app.domain.wealth_portfolio_acceptance import (
    parse_history_series,cumulative_wealth_index,research_buy_hold,
    require_point_in_time,buy_and_hold_series,total_return_metrics,
)
from backend.app.integrations.wealth_collection_cache import read_protected_bytes
from scripts.wealth_portfolio_history import public_nav,public_text,canonical,sha

def parse(rows,**kw):
    return parse_history_series(rows,'date','value',unit=kw.get('unit','CNY_per_share'),kind=kw.get('kind','unit_nav'),cutoff='2025-12-31')

@pytest.mark.parametrize('value',[None,True,'1.0',0,-1,float('inf'),float('nan')])
def test_invalid_nav_never_fills_zero(value):
    with pytest.raises(ValueError):parse([{'date':'2025-01-02','value':value}])

@pytest.mark.parametrize('rows',[
    [{'value':1}], [{'date':None,'value':1}], [{'date':'2025-02-30','value':1}],
    [{'date':'2025-01-02','value':1},{'date':'2025-01-02','value':2}],
    [{'date':'2025-01-03','value':1},{'date':'2025-01-02','value':2}],
])
def test_missing_invalid_unordered_duplicate_dates_fail_closed(rows):
    with pytest.raises(ValueError):parse(rows)

@pytest.mark.parametrize('unit,kind',[('unknown','unit_nav'),('percent','unit_nav'),('wealth_index','cumulative_return')])
def test_unknown_or_incompatible_unit_rejected(unit,kind):
    with pytest.raises(ValueError):parse([{'date':'2025-01-02','value':1}],unit=unit,kind=kind)

def test_future_rows_not_early_consumed_and_gap_not_annualized():
    s=parse([{'date':'2025-01-02','value':1},{'date':'2025-01-10','value':2},{'date':'2026-01-01','value':4}])
    assert s['values']==[1,2] and s['future_rows_excluded']==1
    r=total_return_metrics(s['dates'],s['values']);assert r['annualized_volatility'] is None and r['daily_volatility'] is None

def test_documented_cumulative_percentage_is_not_direct_diff():
    kw={'zero_percent_anchor':'2000-01-01','method_verified':True,'events_verified':True}
    p=cumulative_wealth_index([0,10,-1,18.8],unit='percent',**kw)
    d=cumulative_wealth_index([0,.1,-.01,.188],unit='decimal',**kw)
    assert p==pytest.approx([1,1.1,.99,1.188]) and p==pytest.approx(d)
    assert p[2]/p[1]-1==pytest.approx(-.1) # differs from cumulative difference -.11

@pytest.mark.parametrize('kwargs',[
    {'method_verified':False},{'events_verified':False},{'zero_percent_anchor':None},{'unit':'unknown'},
])
def test_method_unknown_events_anchor_units_not_guessed(kwargs):
    kw={'unit':'percent','zero_percent_anchor':'2000-01-01','method_verified':True,'events_verified':True};kw.update(kwargs)
    with pytest.raises(ValueError):cumulative_wealth_index([0,10],**kw)

@pytest.mark.parametrize('v',[-100,float('nan'),True])
def test_cumulative_invalid_wealth_refused(v):
    with pytest.raises(ValueError):cumulative_wealth_index([0,v],unit='percent',zero_percent_anchor='2000-01-01',method_verified=True,events_verified=True)

def test_explicit_scenario_buy_hold_drift_and_actual_cash_unknown_stays_refused():
    assumptions={'cash_return':'constant_index_research_assumption','external_fees':'not_modelled','rebalance':'none'}
    out=research_buy_hold([[1,2,1],[1,1,2]],[.4,.4],cash_weight=.2,assumptions=assumptions,families=['one','one'])
    assert out['series']==pytest.approx([1,1.4,1.4]) and out['end_weights']==pytest.approx([2/7,4/7])
    assert out['initial_family_hhi_ex_cash']==pytest.approx(.64)
    assert out['end_family_hhi_ex_cash']==pytest.approx((6/7)**2)
    assert out['gross_scenario'] and out['data_kind']=='hypothetical_fixed_portfolio'
    with pytest.raises(ValueError,match='CASH_RETURN_UNKNOWN'):buy_and_hold_series([[1,2]],[.8],cash_weight=.2)
    with pytest.raises(ValueError,match='RESEARCH_ASSUMPTIONS_REQUIRED'):research_buy_hold([[1,2]],[1])
    unknown=research_buy_hold([[1,2]],[1],assumptions=assumptions)
    assert unknown['initial_family_hhi_ex_cash'] is None

def test_today_text_future_and_same_day_unpublished_prices_rejected():
    f={'value':2,'reference_sha256':'a'*64,'available_at':'2025-01-02T15:00:00+08:00','applicable_from':'2020-01-01T00:00:00+08:00'}
    assert require_point_in_time({'price':f},'2025-01-02T14:00:00+08:00',['price'])['status']=='blocked'
    assert require_point_in_time({'price':f},'2025-01-02T16:00:00+08:00',['price'])['status']=='available'
    assert require_point_in_time({'text':{'value':'today text','observed_at':'2026-10-10T00:00:00Z'}},'2025-01-02T16:00:00+08:00',['text'])['status']=='blocked'
    f['available_at']='2026-01-01T00:00:00Z';assert require_point_in_time({'risk':f},'2025-01-02T16:00:00+08:00',['risk'])['status']=='blocked'

def test_js_exact_share_without_exec_and_china_date():
    timestamp=datetime(2025,1,1,16,tzinfo=timezone.utc).timestamp()*1000
    text='var fS_code="000015";var ishb=false;var Data_netWorthTrend='+json.dumps([{'x':timestamp,'y':1}])+';'
    assert public_nav(text,'000015','2025-12-31')['dates']==['2025-01-02']
    with pytest.raises(ValueError,match='HISTORY_SHARE_MISMATCH'):public_nav(text,'000016','2025-12-31')
    with pytest.raises(ValueError,match='MONEY_YIELD_NOT_NAV'):public_nav(text.replace('false','true'),'000015','2025-12-31')

def test_fingerprint_and_path_error_and_canonical_repeat(tmp_path):
    p=tmp_path/'raw';p.mkdir();f=p/'a.json';f.write_bytes(canonical({'b':1,'a':2}))
    assert read_protected_bytes(tmp_path,'raw/a.json',sha(f.read_bytes()),'raw')==f.read_bytes()
    with pytest.raises(ValueError,match='FINGERPRINT'):read_protected_bytes(tmp_path,'raw/a.json','0'*64,'raw')
    with pytest.raises(ValueError,match='PATH_DENIED'):read_protected_bytes(tmp_path,'../a','0'*64,'raw')
    assert canonical({'a':2,'b':1})==canonical({'b':1,'a':2})

def test_explicit_gb2312_method_page_decodes_without_replacement():
    value='<meta http-equiv="Content-Type" content="text/html; charset=gb2312" />分红方式'
    text,encoding=public_text(value.encode('gb2312'))
    assert encoding=='gb2312' and '分红方式' in text
    with pytest.raises(ValueError,match='ENCODING'):public_text(b'<meta charset="unsupported" />')

@pytest.mark.parametrize('weights',[[True],[float('nan')],[-1]])
def test_invalid_buy_hold_weights_rejected(weights):
    with pytest.raises(ValueError):buy_and_hold_series([[1,2]],weights)
