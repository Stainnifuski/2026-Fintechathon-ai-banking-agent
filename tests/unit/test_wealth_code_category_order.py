from types import SimpleNamespace
import asyncio
import pytest
from backend.app.domain.wealth_portfolio_order import CodeCategorySelector
from backend.app.domain.wealth_portfolio_candidates import ProductIndex,pipeline
from backend.app.schemas.wealth_portfolio_candidates import CandidateFilter
from backend.app.schemas.wealth_portfolio_data import FieldFact
from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateService,CandidateError,SnapshotIndexCache
from tests.fixtures.wealth_portfolio_candidates_v1 import fixture_pool,FixtureSource,NOW

def pool():
    return [SimpleNamespace(family_id=c,default_share=SimpleNamespace(product_id=c),substantive_category=t)
        for t,c in [('bond','000005'),('bond','000001'),('mixed','000010'),('mixed','000011'),('equity','000020'),('equity','000021'),('money_market','000030'),('money_market','000031')]]

def test_general_continues_rotation_and_category_is_numeric_code_order():
    selector=CodeCategorySelector();items=pool()
    first,pos=selector.select_batch(items,set(),3)
    assert first==['000030','000001','000010']
    second,pos=selector.select_batch(items,set(first),3,pos)
    assert second==['000020','000031','000005']
    assert selector.select([p for p in items if p.substantive_category=='bond'],set(),3)==['000001','000005']
    assert selector.select(items,{p.family_id for p in items},3)==[]
    assert selector.last_result['financial_rank'] is False

def test_optional_issuer_and_body_do_not_gate_general_selection():
    data,facts=fixture_pool(2)
    for product in data.products:
        for key in ('issuer_raw','issuer_normalized','objective','strategy','benchmark','tracking_target'):
            product.fields[key]=FieldFact(status='missing',reason='not provided')
    result=pipeline(ProductIndex(data),CandidateFilter(mode='general'),facts,facts.user_id,NOW)
    assert result.status=='ok' and len(result.family_candidates)==2
    assert all(f.issuer_identity is None for f in result.family_candidates)
    assert not any('TEXT' in r or 'ISSUER' in r for rs in result.exclusions.values() for r in rs)

def test_removed_theme_refused_before_facts_read():
    class NoRead:
        async def read_current(self,*args):raise AssertionError('removed mode must not read facts')
    service=CandidateService(NoRead(),SnapshotIndexCache(lambda v:None),CodeCategorySelector())
    with pytest.raises(CandidateError,match='THEME_MODE_REMOVED') as error:
        asyncio.run(service.candidates('U1',CandidateFilter(mode='theme',theme_key='high_dividend')))
    assert error.value.status_code==410

def test_current_purchase_suspension_does_not_erase_proven_redemption_permission():
    from backend.app.schemas.wealth_portfolio_candidates import CurrentSimulationEvidence
    from backend.app.domain.wealth_portfolio_eligibility import qualify_purchase
    data,facts=fixture_pool(1);p=data.products[0];offer=facts.offers[p.product_id]
    p.source_kind='simulated';p.fields['formal_risk'].status='simulated'
    facts.mode='simulated';facts.source_id='protected-local-bank';facts.channel='local-competition-bank'
    facts.assessment.source_id=facts.source_id;facts.assessment.source_kind='simulated'
    offer.source_id=facts.source_id;offer.source_kind='simulated';offer.channel=facts.channel
    offer.valid_from=offer.valid_until=None;offer.purchase_allowed=False;offer.holding_allowed=offer.redemption_allowed=True
    offer.current_simulation=CurrentSimulationEvidence(provenance='protected-local-bank-current-products',product_id=p.product_id,
        product_version=p.product_version,disclosure_version=p.disclosure_version,observed_at=NOW,business_at=NOW,
        calendar_version='mock-cn-2026-v1',cutoff='15:00',open_interval_days=1,nav_date=NOW.date(),available_at=NOW,nav='1',status='on_sale',purchase_paused=True,purchase_open=True,data_stale=False)
    result=qualify_purchase(p,facts,facts.user_id,NOW)
    assert not result.purchase_eligible and 'PURCHASE_STOPPED' in result.reason_codes
    assert result.holding_allowed is True and result.redemption_allowed is True

@pytest.mark.parametrize('matches',[True,False])
def test_protected_policy_reference_requires_exact_current_db_hash(matches):
    from backend.app.services.orchestration.wealth_portfolio_joint import ServerReads
    from backend.app.domain.wealth_paper_policy import policy_sha
    policy={'product_id':'000009','risk_level':1,'classification_status':'verified'}
    class Bank:
        async def call(self,*args):
            return [{'product_id':'000009','rules':{},'execution_policy_sha256':policy_sha(policy) if matches else 'f'*64}]
    index=SimpleNamespace(policy_references={'000009':(policy_sha(policy),policy)})
    rows=asyncio.run(ServerReads(None,'U1',Bank(),index).get('/api/v1/wealth/products'))
    assert rows[0]['rules']['execution_policy']==(policy if matches else None)
