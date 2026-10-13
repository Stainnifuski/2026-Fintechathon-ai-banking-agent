"""A0 DTO validation only. Future fixture expectations are NOT execution tests."""
import json
from copy import deepcopy
from pathlib import Path
import pytest
from pydantic import ValidationError
from backend.app.schemas.wealth_portfolio import (PortfolioPlan, PortfolioSelection,
    CandidateGroup, ProductCandidate, RecommendationFilter, AssetSummary, PositionFact)
from scripts.generate_wealth_portfolio_fixtures import generate
from datetime import datetime, timezone
from backend.app.integrations.wealth_portfolio_fields import adapt_product_share

ROOT = Path(__file__).resolve().parents[1]/'fixtures/wealth_portfolio_contract_v1'

@pytest.mark.parametrize('name', [f'F{i:02}' for i in range(1,25)])
def test_fixture_reproducible_and_contract(name):
    from backend.app.services.orchestration.wealth_portfolio_cli import fixture_path
    case = json.loads(fixture_path(name).read_text(encoding='utf-8'))
    expected=generate()[name]
    assert {k:case[k] for k in expected} == expected
    assert case['data_status'] == 'fixture_only'
    data = case['input']
    if 'plan' in data: PortfolioPlan.model_validate(data['plan'])
    if 'selection' in data: PortfolioSelection.model_validate(data['selection'])
    if 'group' in data: CandidateGroup.model_validate(data['group'])
    if 'family' in data:
        ProductCandidate.model_validate(data['family'])
        ProductCandidate.model_validate(data['only_c'])
    for item in data.get('filters', []): RecommendationFilter.model_validate(item)
    if 'bundle' in data: AssetSummary.model_validate(data['bundle']['summary'])

@pytest.mark.parametrize('change', [
    {'budget_fen':100}, {'basis_fen':True}, {'growth_ratio_ex_cash':'62.5'},
    {'growth_ratio_ex_cash':0.625}, {'as_of':'2026-10-05T04:00:00'},
    {'model_status':'researched_model'}, {'version':0}, {'growth_ratio_ex_cash':'invalid'}])
def test_plan_rejects_bad_units_unknown_fields_or_fixture_claim(change):
    data = generate()['F01']['input']['plan']; data.update(change)
    with pytest.raises(ValidationError): PortfolioPlan.model_validate(data)

def test_duplicate_families_and_bad_risk_rejected():
    group = generate()['F03']['input']['group']
    group['items'].append(deepcopy(group['items'][0]))
    with pytest.raises(ValidationError): CandidateGroup.model_validate(group)
    group['items'].pop(); group['items'][0]['share_classes'][0]['product_risk_level']=6
    with pytest.raises(ValidationError): CandidateGroup.model_validate(group)

def test_no_legacy_five_item_limit_and_unknown_value_not_zero():
    selection = PortfolioSelection.model_validate(generate()['F11']['input']['selection'])
    assert len(selection.items)==7
    summary = generate()['F10']['input']['bundle']['summary']
    assert AssetSummary.model_validate(summary).total_assets_fen is None
    summary['total_assets_fen']=800000
    with pytest.raises(ValidationError): AssetSummary.model_validate(summary)

def test_disclosure_and_product_versions_required():
    candidate = generate()['F09']['input']['family']
    del candidate['share_classes'][0]['disclosure_version']
    with pytest.raises(ValidationError): ProductCandidate.model_validate(candidate)

def test_percent_total_and_decimal_units_rejected():
    plan = generate()['F01']['input']['plan']; plan['buckets'][0]['target_ratio']='0.21'
    with pytest.raises(ValidationError): PortfolioPlan.model_validate(plan)
    selection = generate()['F11']['input']['selection']; selection['items'][0]['target_ratio_total']='50'
    with pytest.raises(ValidationError): PortfolioSelection.model_validate(selection)

def test_legacy_product_mapping_does_not_guess_versions_fees_or_risk():
    row={'product_id':'fixture-product','version':3,'status':'on_sale',
        'disclosure':{'version':2,'data_source':'synthetic'},'rules':{
            'purchase_fee_rate':'0.001','redeem_fee_bands':[{'days_lt':7,'rate':'0.015'}]}}
    share=adapt_product_share(row,datetime.now(timezone.utc))
    assert share.product_version==3 and share.disclosure_version==2
    assert share.product_risk_level is None and share.share_class is None
    assert share.fees.sales_service_fee_rate is None and share.tradable is False
    assert share.performance.one_year.value_percent is None
    assert share.fees.redemption_fee_rules==row['rules']['redeem_fee_bands']
    del row['disclosure']['version']
    with pytest.raises(ValueError,match='explicit'): adapt_product_share(row,datetime.now(timezone.utc))
