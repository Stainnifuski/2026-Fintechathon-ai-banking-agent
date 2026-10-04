from copy import deepcopy
import pytest
from backend.app.domain.wealth_allocation import allocate
from backend.app.domain.wealth_recommendation import DeterministicRuleProvider,recommend,register_provider,get_provider


def inputs():
    products=[]
    for index,category in enumerate(['cash','bond','mixed'],1):
        products.append({'product_id':f'P{index}','name':category,'product_risk_level':index,'status':'on_sale',
            'category':category,'version':1,'nav':'1.00000000','nav_date':'2026-09-30','disclosure':{'version':1},
            'rules':{'purchase_fee_rate':'0.003','purchase_paused':False,'holding_days':0,'confirm_business_days':1,
                'cash_business_days':1,'calendar_version':'synthetic-weekdays-v1','min_amount_fen':10000,'max_amount_fen':2000000}})
    return {'products':products,'allocation':{'budget_fen':1000000,'reserve_fen':0,'capacity':3,'horizon_days':180,
            'excluded':[],'purchase_allowed':True},'as_of':'2026-10-03T10:00:00+08:00',
            'available_cash_fen':1922900,'minimum_account_cash_fen':500000,'data_versions':{'data':'fixture'}}


def test_rule_provider_keeps_existing_allocations_and_reconciles_fees():
    source=inputs();old=allocate(source['products'],**source['allocation'])
    result=recommend(source)
    assert [i['amount_fen'] for i in result['items']]==[i['amount_fen'] for i in old['items']]
    assert result['investment_fen']+result['retained_cash_fen']==1000000
    assert result['account_remaining_cash_fen']==1922900-result['investment_fen']
    assert result['estimated_fee_fen']==sum(i['estimated_fee_fen'] for i in result['items'])
    assert all(i['reason_codes'] and i['evidence'] for i in result['items'])


def test_provider_can_be_replaced_without_service_weights(monkeypatch):
    class CashOnlyProvider(DeterministicRuleProvider):
        provider_id='test-replacement'
        def recommend(self,source):
            result=super().recommend(source)
            result.update(items=[],investment_fen=0,retained_cash_fen=1000000,unallocated_fen=1000000,
                target_category_weights={'cash':100})
            return result
    register_provider(CashOnlyProvider());monkeypatch.setenv('WEALTH_RECOMMENDATION_PROVIDER','test-replacement')
    assert get_provider().provider_id=='test-replacement'
    assert recommend(inputs())['target_category_weights']=={'cash':100}


@pytest.mark.parametrize('fault',['amount','version','risk','future','cash','sum','weights','identity'])
def test_invalid_provider_output_is_rejected_not_clipped(fault):
    source=inputs()
    class InvalidProvider(DeterministicRuleProvider):
        def recommend(self,detached):
            result=super().recommend(detached)
            if fault=='amount':result['items'][0]['amount_fen']=2000000
            if fault=='version':result['items'][0]['product_version']=2
            if fault=='risk':result['items'][0]['product_id']='UNAPPROVED'
            if fault=='sum':result['unallocated_fen']+=1
            if fault=='weights':result['target_category_weights']={'cash':101}
            if fault=='identity':result['provider_id']='forged'
            return result
    if fault=='future':source['products'][0]['available_at']='2026-10-04T10:00:00+08:00'
    if fault=='cash':source['minimum_account_cash_fen']=1500000
    with pytest.raises(ValueError,match='RECOMMENDATION_'):recommend(source,InvalidProvider())


def test_provider_cannot_mutate_bank_inputs():
    source=inputs();before=deepcopy(source)
    recommend(source)
    assert source==before


def test_goal_and_liquidity_narrow_candidates_without_raising_formal_risk():
    source=inputs();source['constraints']={'investment_goal':{'value':'preserve_value'},
        'liquidity_requirement':{'value':{'mode':'immediate','max_business_days':None}}}
    # Only the cash product provides quick redemption; growth target cannot upgrade R1.
    source['products'][0]['rules']['quick_redemption']={'enabled':True}
    result=recommend(source)
    assert all(i['product_id']=='P1' for i in result['items'])
    assert result['target_category_weights']=={'cash':40,'bond':60}
    assert result['constraints']['investment_goal']['value']=='preserve_value'


def test_conditional_provider_research_preserves_unknown_and_has_no_amounts():
    from backend.app.domain.wealth_recommendation import conditional_research
    source=inputs();source.update(capacity=3,horizon_days=None,constraints={'horizon':{'state':'unknown'}})
    result=conditional_research(source)
    assert result['conditional'] and result['constraints']['horizon']['state']=='unknown'
    assert result['items'] and all('amount_fen' not in item for item in result['items'])
