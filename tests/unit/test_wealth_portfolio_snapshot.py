from copy import deepcopy
import pytest
from pydantic import ValidationError
from scripts.generate_wealth_portfolio_fixtures import generate
from backend.app.services.orchestration.wealth_portfolio import build_snapshot, SnapshotError
from backend.app.integrations.wealth_portfolio_fields import adapt_legacy_asset_details
from backend.app.schemas.wealth_portfolio import PortfolioInput

@pytest.mark.parametrize('name', ['F01','F08','F10','F18'])
def test_summary_snapshot_same_basis_and_input_immutable(name):
    case=generate()[name]; bundle=case['input']['bundle']; saved=deepcopy(bundle)
    snapshot=build_snapshot(bundle)
    assert bundle==saved
    assert snapshot.basis_fen==case['expected']['basis_fen']
    assert snapshot.snapshot_id==bundle['summary']['snapshot_id']
    if 'available_cash_fen' in case['expected']:
        assert sum(a.available_fen for a in snapshot.accounts)==case['expected']['available_cash_fen']

def test_full_scope_lots_grouped_and_receivable_once():
    s=build_snapshot(generate()['F18']['input']['bundle'])
    assert len(s.accounts)==2 and s.total_assets_fen==1000000
    assert len(s.positions)==1 and s.positions[0].units=='750.0000'
    assert len(s.positions[0].lot_ids)==2 and s.receivable_fen==50000

@pytest.mark.parametrize('change', ['scope','cash','duplicate','version','nav','units','credit','receivable'])
def test_bad_source_fails_without_fabricating(change):
    b=generate()['F18']['input']['bundle']
    if change=='scope': b['accounts'].pop()
    elif change=='cash': b['accounts'][0]['balance_fen']+=100
    elif change=='duplicate': b['lots'].append(deepcopy(b['lots'][0]))
    elif change=='version': del b['lots'][0]['product_version']
    elif change=='nav': b['lots'][0]['nav_date']='2027-01-01'
    elif change=='units': b['lots'][0]['units']='500'
    elif change=='credit': b['accounts'][0]['account_type']='credit'
    elif change=='receivable': b['orders'][0]['details']['actual']['cash_fen']=None
    with pytest.raises((SnapshotError,ValidationError)): build_snapshot(b)

def test_old_single_context_is_not_full_scope():
    with pytest.raises(SnapshotError,match='full AssetSummary'):
        build_snapshot({'bank_assets_fen':1000000,'cash':[],'lots':[]})

def test_beijing_date_boundary_and_missing_assessment():
    b=generate()['F08']['input']['bundle']; b['assessment']=None
    b['summary']['as_of']=b['summary']['business_at']='2026-10-04T17:00:00Z'
    b['lots'][0]['nav_date']='2026-10-05'
    assert 'assessment' in build_snapshot(b).missing_fields
    b['lots'][0]['nav_date']='2026-10-06'
    with pytest.raises(ValidationError): build_snapshot(b)

def test_opaque_version_id_mapped_only_with_catalog_match():
    b=generate()['F08']['input']['bundle']; del b['lots'][0]['product_version']
    b['lots'][0]['version_id']='opaque-key'
    b['products']=[{'product_id':'fixture-product','version_id':'opaque-key','version':2}]
    assert build_snapshot(b).positions[0].product_version==2

def test_legacy_adapter_requires_full_b_summary_and_no_required_budget_or_term():
    b=generate()['F18']['input']['bundle']
    legacy={'cash':b['accounts'],'lots':b['lots'],'orders':b['orders'],'as_of':b['summary']['as_of']}
    mapped=adapt_legacy_asset_details(b['summary'],legacy,[],b['assessment'],basis_status='fixture_only')
    s=build_snapshot(mapped)
    payload={'schema_version':'wealth-portfolio-input-v1','snapshot':s.model_dump(mode='json'),
        'products':[],'features':{},'user_preferences':{}}
    assert PortfolioInput.model_validate(payload).snapshot.total_assets_fen==1000000
    payload['budget_fen']=100000
    with pytest.raises(ValidationError): PortfolioInput.model_validate(payload)
    legacy['as_of']='2026-10-01T00:00:00Z'
    with pytest.raises(ValueError,match='observation times'):
        adapt_legacy_asset_details(b['summary'],legacy,[],b['assessment'])
    legacy['as_of']=b['summary']['as_of']
    legacy['cash'].pop()
    with pytest.raises(SnapshotError): build_snapshot(adapt_legacy_asset_details(
        b['summary'],legacy,[],b['assessment'],basis_status='fixture_only'))
