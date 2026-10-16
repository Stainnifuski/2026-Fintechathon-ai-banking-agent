"""Canonical reference/target contract and explicit payer boundary (isolated unit data)."""
from copy import deepcopy
import pytest
from backend.app.services.orchestration.wealth_selection import execution_targets,difference_actions
from backend.app.integrations.wealth_authorization import WealthPolicyError


def sample():
    snapshot={'basis_fen':1000000,'valuation_complete':True,'receivable_fen':0,'positions':[],
        'accounts':[{'account_id':'A1','account_type':'savings','available_fen':1000000}]}
    ref={'product_id':'exact-001','product_version':1,'share_class':None,'target_ratio_total':'0.1'}
    detail={**ref,'disclosure_version':2,'family_id':'exact-code:exact-001','bucket':'stable','target_fen':100000,'current_fen':0,'name':'isolated contract unit','account_ids':[],'role':'selected'}
    buckets=[{'bucket':b,'min_ratio':'0','target_ratio':r,'max_ratio':'1','target_fen':f} for b,r,f in [('cash_management','0.9',900000),('stable','0.1',100000),('growth','0',0)]]
    return {'basis_fen':1000000,'missing_fields':[],'selected_positions':[ref],'advised_positions':[detail],'cash_residual_fen':900000,'buckets':buckets},snapshot

def test_unique_account_uses_authoritative_A_target_and_disclosure_no_input_mutation():
    plan,snapshot=sample();before=deepcopy(plan)
    mapped,payer=execution_targets(plan,snapshot)
    assert plan==before and payer=='A1'
    assert mapped['selected_positions'][0]['disclosure_version']==2
    assert difference_actions(mapped,snapshot)[0]['amount_fen']==100000
    assert set(plan['selected_positions'][0])=={'product_id','product_version','share_class','target_ratio_total'}

def test_multiple_accounts_never_choose_first_or_foreign_account():
    plan,snapshot=sample();snapshot['accounts'].append({'account_id':'A2','account_type':'savings','available_fen':100000})
    with pytest.raises(WealthPolicyError) as e:execution_targets(plan,snapshot)
    assert e.value.code=='PAYMENT_ACCOUNT_REQUIRED'
    with pytest.raises(WealthPolicyError) as e:execution_targets(plan,snapshot,'foreign')
    assert e.value.code=='ACCOUNT_CONTEXT_MISMATCH'
    mapped,payer=execution_targets(plan,snapshot,'A2')
    assert payer=='A2' and difference_actions(mapped,snapshot)[0]['account_id']=='A2'

def test_missing_target_or_disclosure_does_not_invent_amount_or_version():
    plan,snapshot=sample();plan['advised_positions'][0].pop('disclosure_version')
    with pytest.raises(WealthPolicyError) as e:execution_targets(plan,snapshot)
    assert e.value.code=='PORTFOLIO_TARGET_DETAIL_MISSING'

def test_same_code_across_accounts_maintained_not_counted_as_duplicate_fund():
    plan,snapshot=sample();snapshot['accounts'].append({'account_id':'A2','account_type':'savings','available_fen':100000})
    snapshot['positions']=[{'account_id':a,'product_id':'exact-001','product_version':1,'market_value_fen':50000} for a in ['A1','A2']]
    mapped,payer=execution_targets(plan,snapshot,'A1')
    assert len(mapped['selected_positions'])==2
    assert sum(p['target_fen'] for p in mapped['selected_positions'])==100000
    assert difference_actions(mapped,snapshot)==[]
    plan['advised_positions'][0]['target_fen']=50000;plan['selected_positions'][0]['target_ratio_total']='0.05'
    with pytest.raises(WealthPolicyError) as e:execution_targets(plan,snapshot,'A1')
    assert e.value.code=='ACCOUNT_ALLOCATION_REQUIRED'
