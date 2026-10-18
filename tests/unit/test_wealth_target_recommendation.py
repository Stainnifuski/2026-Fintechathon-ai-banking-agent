"""Target matching must not compute an allocation or read portfolio valuation."""
import pytest
from pydantic import ValidationError
from backend.app.schemas.wealth_portfolio_candidates import CandidateFilter
from backend.app.schemas.wealth_portfolio_lifecycle import PortfolioRecommendation
from backend.app.domain.wealth_portfolio_candidates import ProductIndex,pipeline
from tests.fixtures.wealth_portfolio_candidates_v1 import fixture_pool,NOW
from tests.fixtures.wealth_portfolio_lifecycle_v1 import lifecycle_fixture


@pytest.mark.parametrize('query',['产品名','F00A'])
def test_name_matches_only_requested_product_and_preserves_risk_gate(query):
    data,facts=fixture_pool(2)
    data.products[0].name='目标产品名称'
    query=data.products[0].product_id if query=='F00A' else query
    index=ProductIndex(data);filter=CandidateFilter(mode='name',name_query=query)
    result=pipeline(index,filter,facts,facts.user_id,NOW)
    assert result.matched_codes==[data.products[0].product_id]
    assert result.status=='ok'
    facts.assessment.capacity_level=1
    result=pipeline(index,filter,facts,facts.user_id,NOW)
    assert result.status=='risk_mismatch' and not result.family_candidates


@pytest.mark.parametrize('fields',[{'name_query':' '},{'name_query':'目标','bucket':'stable'},{'name_query':'目标','category':'bond_fund'}])
def test_name_filter_rejects_empty_or_allocation_bucket(fields):
    with pytest.raises(ValidationError):CandidateFilter(mode='name',**fields)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['category','name'])
async def test_target_recommendation_never_calls_allocation_or_asset_model(mode):
    service,data,*_=lifecycle_fixture()
    def forbidden(*args,**kwargs):raise AssertionError('target recommendation called allocation/valuation')
    service.provider.asset_source=None
    service.provider.plan=forbidden
    service.provider._facts=forbidden
    filter=CandidateFilter(mode='category',category='bond_fund') if mode=='category' else CandidateFilter(mode='name',name_query=data.products[0].product_id)
    group=await service.recommend('fixture-user',PortfolioRecommendation(context_id='fixture-context',filter=filter))
    assert group.status=='ok' and group.items
    assert not service.store.rows
    assert all('板块' not in card.reason for card in group.items)
    assert group.bucket is None
