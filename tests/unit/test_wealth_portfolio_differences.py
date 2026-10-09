"""Execution arithmetic guards; targets come from the shared recording."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from backend.app.services.orchestration.wealth_selection import difference_actions
from backend.app.integrations.wealth_authorization import WealthPolicyError
from backend.app.schemas.wealth_selection import PortfolioPrepare
from pydantic import ValidationError


def case():
    fixture=json.loads((Path(__file__).parents[1]/'fixtures/wealth_portfolio_contract_v1/f11.json').read_text(encoding='utf8'))
    return fixture['plan'],fixture['snapshot']


def test_f11_all_seven_targets_and_revised_amounts_are_preserved():
    plan,snapshot=case()
    actions=difference_actions(plan,snapshot)
    assert len(actions)==7 and sum(a['amount_fen'] for a in actions)==snapshot['basis_fen']


@pytest.mark.parametrize('kind,code',[
    ('duplicate_family','INVALID_PORTFOLIO'),('bad_ratio','INVALID_PORTFOLIO'),
    ('mismatched_amount','INVALID_PORTFOLIO'),('bad_bucket','INVALID_PORTFOLIO'),
    ('overallocated','INVALID_PORTFOLIO'),('missing_valuation','VALUATION_INCOMPLETE'),
    ('wrong_account','ACCOUNT_CONTEXT_MISMATCH'),('cash_held','INSUFFICIENT_AVAILABLE_FUNDS')])
def test_targets_are_not_silently_normalized_or_overfunded(kind,code):
    plan,snapshot=case()
    if kind=='duplicate_family':plan['selected_positions'][1]['family_id']=plan['selected_positions'][0]['family_id']
    elif kind=='bad_ratio':plan['selected_positions'][0]['target_ratio_total']='NaN'
    elif kind=='mismatched_amount':plan['selected_positions'][0]['target_fen']+=1
    elif kind=='bad_bucket':plan['buckets'][0]['target_ratio']='0.99'
    elif kind=='overallocated':plan['cash_residual_fen']=1000
    elif kind=='missing_valuation':snapshot['valuation_complete']=False;snapshot['basis_fen']=None
    elif kind=='wrong_account':plan['selected_positions'][0]['account_id']='someone-else'
    else:snapshot['accounts'][0]['available_fen']-=1000
    with pytest.raises(WealthPolicyError) as error:difference_actions(plan,snapshot)
    assert error.value.code==code


def test_unknown_current_positions_are_retained_not_automatically_sold():
    plan,snapshot=case()
    snapshot['accounts'][0]['balance_fen']-=100000;snapshot['accounts'][0]['available_fen']-=100000
    snapshot['positions']=[dict(account_id='fixture-account',product_id='existing-retained',product_version=1,
        market_value_fen=100000,units='1000.0000',redeemable_units='0.0000',bucket='stable',nav='1.0000')]
    plan['selected_positions'][1]['target_ratio_total']='0';plan['selected_positions'][1]['target_fen']=0
    result=difference_actions(plan,snapshot)
    assert len(result)==6 and all(a['direction']=='purchase' for a in result)
    assert 'existing-retained' not in {a['product_id'] for a in result}


def test_full_redemption_uses_total_units_and_does_not_call_locked_units_redeemable():
    plan,snapshot=case()
    position=deepcopy(plan['selected_positions'][1])
    snapshot['positions']=[{**position,'market_value_fen':100000,'units':'1000.0000','redeemable_units':'0.0000','nav':'1.0000'}]
    snapshot['accounts'][0]['balance_fen']-=100000;snapshot['accounts'][0]['available_fen']-=100000
    plan['selected_positions'][1]['target_ratio_total']='0';plan['selected_positions'][1]['target_fen']=0
    plan['cash_residual_fen']=100000
    plan['buckets'][0]['target_ratio']='0.30';plan['buckets'][0]['target_fen']=300000;plan['buckets'][0]['max_ratio']='0.35'
    plan['buckets'][1]['target_ratio']='0.20';plan['buckets'][1]['target_fen']=200000;plan['buckets'][1]['min_ratio']='0.15'
    with pytest.raises(WealthPolicyError) as error:difference_actions(plan,snapshot)
    assert error.value.code=='INSUFFICIENT_REDEEMABLE_UNITS'
    snapshot['positions'][0]['redeemable_units']='1000.0000'
    actions=difference_actions(plan,snapshot)
    sell=next(a for a in actions if a['direction']=='redeem')
    assert sell['units']=='1000.0000' and sell['amount_fen']==0
    assert all(a['status']=='WAITING_SETTLEMENT_AND_REREAD_CASH' for a in actions if a['direction']=='purchase')


def test_prepare_rejects_client_identity_plan_and_boolean_version():
    for extra in ({'subject_user_id':'U2'},{'plan':{}},{'expected_version':True}):
        with pytest.raises(ValidationError):PortfolioPrepare.model_validate({'expected_version':1,'snapshot_id':'x',**extra})
