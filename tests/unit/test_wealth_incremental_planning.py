from fractions import Fraction
import pytest
from backend.app.domain.wealth_incremental_planning import incremental_targets,apportion
from backend.app.domain.wealth_incremental_planning import product_targets
from backend.app.domain.wealth_portfolio_projection import WeightFamily,WeightPart
from backend.app.domain.wealth_portfolio_allocation import ModelError


def test_above_soft_target_does_not_sell_old_positions():
    result=incremental_targets(4,1000000,[0,300000,500000],0,190000,10000,[200000,380000,420000])
    assert result['sales']==[0,0,0]
    assert result['targets']==[200000,300000,500000]
    assert result['purchases']==[190000,0,0]


def test_only_hard_growth_excess_is_sold_and_new_cash_can_cure_excess():
    result=incremental_targets(3,1000000,[100000,0,800000],0,100000,0,[200000,600000,200000])
    assert result['sales']==[0,0,350000]
    assert result['targets'][2]==450000
    cured=incremental_targets(3,1000000,[100000,300000,400000],0,200000,0,[200000,600000,200000])
    assert cured['sales']==[0,0,0]
    assert cured['targets'][2]>=400000


def test_fee_allowance_and_proportional_rounding():
    r=incremental_targets(3,1000000,[100000,0,800000],0,100000,0,[200000,600000,200000],'.015')
    sale=r['sales'][2];fee=(sale*15+999)//1000
    assert 800000-sale<=Fraction(1,2)*(900000-fee)
    assert apportion(100,[100,200,300])==[17,33,50]
    assert sum(apportion(101,[100,200,300]))==101


def test_proportional_sales_cover_every_old_product_and_do_not_invent_buys():
    fs=[WeightFamily(str(i),'growth',v,[WeightPart(str(i),v,False,True,{'qualification':{'redemption_allowed':True}})]) for i,v in enumerate([100,200,300])]
    result=product_targets(fs,499,101,600)
    targets=[r['parts'][0]['target_fen'] for r in result['targets']]
    assert targets==[83,166,250]
    assert sum(targets)==499
    fs[0].parts[0].evidence['qualification']['redemption_allowed']=False
    with pytest.raises(ModelError,match='PLANNING_REDEMPTION_UNAVAILABLE'):product_targets(fs,499,101,600)


def test_existing_holdings_are_kept_and_new_money_only_goes_to_selected_products():
    fs=[WeightFamily('old','stable',300,[WeightPart('old',300,False,True)]),WeightFamily('new','stable',0,[WeightPart('new',0,True,True)])]
    result=product_targets(fs,500,0,1000)
    assert {p['product_id']:p['target_fen'] for r in result['targets'] for p in r['parts']}=={'old':300,'new':200}


def test_stable_redemption_fee_also_reserves_room_at_growth_cap():
    # Minimum liquid allocation requires stable sales; their fees also reduce
    # the denominator of the growth cap even if growth was exactly at the cap.
    result=incremental_targets(3,1000000,[0,500000,450000],0,50000,0,[200000,600000,200000],'.015')
    fee=(sum(result['sales'])*15+999)//1000
    assert result['sales'][1]>0 and result['sales'][2]>0
    assert 450000-result['sales'][2]<=Fraction(1,2)*(900000-fee)


def test_formal_provider_keeps_above_soft_holdings_and_separates_suggestion():
    import asyncio
    from tests.fixtures.wealth_portfolio_model_v1 import model_fixture
    provider,_,_,_=model_fixture(holding=True)
    result=asyncio.run(provider.plan('fixture-user',selected=[],cash_reserve_fen=190000,available_cash_fen=190000))
    assert result.status=='ok',result.missing_fields
    assert result.plan.planning['redemption_totals_fen']==[0,0,0]
    assert result.plan.planning['new_investment_fen']==0
    assert [b.target_fen for b in result.plan.buckets]==[200000,300000,500000]
    assert result.plan.planning['suggested_amounts_fen']!=[b.target_fen for b in result.plan.buckets]


@pytest.mark.parametrize('bands,expected',[(None,'0'),([],'0'),([{'days_lt':7,'rate':'0.015'},{'days_lt':30,'rate':'0.005'}],'0.015')])
@pytest.mark.parametrize('cash_budget',[False,True])
def test_existing_holdings_use_formal_fees_dto_in_initial_and_cash_planning(bands,expected,cash_budget):
    import asyncio
    from backend.app.schemas.wealth_portfolio import Fees
    from tests.fixtures.wealth_portfolio_model_v1 import model_fixture
    provider,_,facts,_=model_fixture(holding=True)
    facts.offers['Q01A'].fee_rules=Fees(purchase_fee_rate='0',sales_service_fee_rate='0',
        redemption_fee_rules=bands,missing_fields=[])
    arguments={'selected':[],'cash_reserve_fen':190000,'available_cash_fen':190000} if cash_budget else {}
    result=asyncio.run(provider.plan('fixture-user',**arguments))
    assert result.status=='ok',result.missing_fields
    assert result.plan.planning_reference['fee_rate']==expected


@pytest.mark.parametrize('level',range(1,6))
@pytest.mark.parametrize('reserve',[0,50000,200000])
def test_all_amounts_conserve_money_and_hard_limits(level,reserve):
    from backend.app.domain.wealth_portfolio_allocation import integer_valid,problem
    r=incremental_targets(level,1000000,[100000,300000,400000],reserve,200000,0,[200000,400000,400000])
    assert integer_valid(problem(level,None),r['targets'],1000000,request=False)
    assert sum(r['purchases'])==200000-reserve+sum(r['sales'])
    assert r['sales'][0]==0
    assert all(x>=0 for x in r['purchases'])
