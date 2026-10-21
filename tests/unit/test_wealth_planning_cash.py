import pytest
from backend.app.domain.wealth_planning_cash import cash_budget
from backend.app.domain.wealth_portfolio_allocation import ModelError
from tests.fixtures.wealth_portfolio_lifecycle_v1 import lifecycle_fixture
from backend.app.schemas.wealth_portfolio_lifecycle import PortfolioCreate, SelectedShare, PortfolioRecommendation
from backend.app.schemas.wealth_portfolio_candidates import CandidateFilter


@pytest.mark.parametrize('reserve,expected,cash', [
    (10000,[20000,40000,40000],10000),
    (20000,[20000,40000,40000],20000),
    (60000,[60000,20000,20000],60000),
    (100000,[100000,0,0],100000),
])
def test_cash_is_liquid_and_excess_cash_preserves_model_split(reserve,expected,cash):
    assert cash_budget([20000,40000,40000],100000,reserve,100000)==(expected,cash)


def test_frozen_and_receivable_cannot_be_invested_and_rounding_is_conservative():
    amounts,cash=cash_budget([20000,30000,50000],100000,10000,70000,30000)
    assert cash==40000 and amounts==[40000,22500,37500]
    assert sum(cash_budget([2,3,5],10,7,10)[0])==10
    for reserve in [-1,100001,True,1.5]:
        with pytest.raises(ModelError):cash_budget([20000,40000,40000],100000,reserve,100000)


@pytest.mark.asyncio
@pytest.mark.parametrize('total',[1000000,4988191])
async def test_cash_budget_reaches_stored_plan_and_rejects_stale_balance(total):
    service,data,facts,bundle,_=lifecycle_fixture(holding=False)
    bundle=service.provider.asset_source.bundle
    bundle['accounts'][0].update(balance_fen=total,available_fen=total)
    bundle['summary'].update(cash_balance_fen=total,available_cash_fen=total,total_assets_fen=total)
    available=bundle['accounts'][0]['available_fen']
    selected=[SelectedShare(product_id=code,product_version=1,share_class='A') for code in ['Q00A','Q01A','Q03A']]
    request=PortfolioCreate(context_id='fixture-context',selected=selected,cash_reserve_fen=available//2,available_cash_fen=available)
    plan=await service.create('fixture-user',request)
    assert plan.cash_residual_fen==available//2
    assert sum(b.target_fen for b in plan.buckets)==plan.basis_fen
    assert sum(p.target_fen for p in plan.advised_positions)+plan.cash_residual_fen==plan.basis_fen
    assert plan.buckets[0].target_fen>=plan.cash_residual_fen
    record=await service.store.read(plan.portfolio_id,'fixture-user')
    assert record.metadata['planning_cash']['reserve_fen']==available//2
    with pytest.raises(ModelError,match='PLANNING_AVAILABLE_CASH_CHANGED'):
        await service.create('fixture-user',request.model_copy(update={'available_cash_fen':available+1}))


@pytest.mark.asyncio
async def test_low_reserve_is_exact_and_liquid_product_fills_the_difference():
    service,*_=lifecycle_fixture(holding=False)
    facts,index,now,asset=await service.provider._facts('fixture-user')
    available=sum(a.available_fen for a in asset.snapshot.accounts)
    selected=[SelectedShare(product_id=code,product_version=1,share_class='A') for code in ['Q00A','Q01A','Q03A']]
    plan=await service.create('fixture-user',PortfolioCreate(context_id='fixture-context',selected=selected,cash_reserve_fen=0,available_cash_fen=available))
    assert plan.cash_residual_fen==0
    liquid=sum(p.target_fen for p in plan.advised_positions if p.bucket=='cash_management')
    assert liquid==plan.buckets[0].target_fen and liquid>0
    from backend.app.services.orchestration.wealth_selection import execution_targets
    executable,_=execution_targets(plan.model_dump(mode='json'),asset.snapshot.model_dump(mode='json'))
    assert sum(p['target_fen'] for p in executable['selected_positions'])==plan.basis_fen


@pytest.mark.asyncio
async def test_cash_plan_cannot_be_revised_to_manual_preference():
    service,*_=lifecycle_fixture(holding=False)
    _,_,_,asset=await service.provider._facts('fixture-user')
    available=sum(a.available_fen for a in asset.snapshot.accounts)
    plan=await service.create('fixture-user',PortfolioCreate(context_id='fixture-context',cash_reserve_fen=available,available_cash_fen=available))
    from backend.app.schemas.wealth_portfolio import PortfolioSelection
    from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateError
    revision=PortfolioSelection(portfolio_id=plan.portfolio_id,expected_version=plan.version,snapshot_id=plan.snapshot_id,context_id=plan.context_id,items=plan.selected_positions,revision_kind='preference',growth_ratio_ex_cash='0.5')
    with pytest.raises(CandidateError,match='PLANNING_MODEL_RATIO_FIXED'):
        await service.revise('fixture-user',plan.portfolio_id,revision)


@pytest.mark.asyncio
async def test_planning_batches_exclude_current_cards_and_keep_bucket():
    service,*_=lifecycle_fixture(holding=False)
    filter=CandidateFilter(mode='general',bucket='growth')
    first=await service.recommend('fixture-user',PortfolioRecommendation(context_id='fixture-context',filter=filter))
    ids={c.family_id for c in first.items}
    second=await service.recommend('fixture-user',PortfolioRecommendation(context_id='fixture-context',filter=filter,excluded_family_ids=list(ids)))
    assert not ids.intersection(c.family_id for c in second.items)
    assert all(c.bucket=='growth' for c in first.items+second.items)
