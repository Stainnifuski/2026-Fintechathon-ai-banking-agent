"""Static disclosure contract; no market observation is a product revision."""
from copy import deepcopy
import pytest
from backend.app.domain.wealth_product_content import product_content, effective_disclosure
from backend.app.services.orchestration.wealth_portfolio_data import digest
from tests.unit.test_wealth_portfolio_evidence import row


def paper():
    value=row('000003',2,'bond','open_fund')
    value['rules']={'pricing_mode':'frozen-public-nav-v1','execution_policy':{
        'version':'test-policy','material_sha256':'a'*64,'quote':{'value':'1','date':'2026-10-01'}}}
    value['disclosure'].update(risk='本金可能亏损',fees={'purchase':'0'},
        pricing='原始冻结计价说明',limitations='原始模拟交易边界')
    return value


def test_raw_effective_and_json_roundtrip_have_one_binding():
    import json
    raw=paper();effective=deepcopy(raw)
    effective['disclosure']=effective_disclosure(raw['disclosure'],raw['rules'])
    assert digest(product_content(raw))==digest(product_content(effective))
    assert product_content(effective)==product_content(json.loads(json.dumps(effective)))
    assert product_content(raw)['disclosure']['base_pricing']=='原始冻结计价说明'


@pytest.mark.parametrize('field', ['risk','fees','liquidity','pricing','limitations','manager','version'])
def test_same_version_terms_changes_are_visible(field):
    raw=paper();changed=deepcopy(raw);changed['disclosure'][field]='changed without version bump'
    assert digest(product_content(raw))!=digest(product_content(changed))


def test_policy_revision_is_bound_but_quote_observation_is_not():
    raw=paper();changed=deepcopy(raw)
    changed.update(nav='9',nav_date='2026-10-15',available_at='2026-10-15T00:00:00Z',observed_at='later')
    changed['rules']['execution_policy']['quote']={'value':'9','date':'2026-10-15','observed_at':'later'}
    assert product_content(raw)==product_content(changed)
    changed['rules']['execution_policy']['version']='new-policy'
    assert product_content(raw)!=product_content(changed)


@pytest.mark.parametrize('field', ['pricing','limitations','runtime_pricing_policy_version','base_pricing'])
def test_effective_body_tampering_is_not_hidden_by_reapplying_overlay(field):
    raw=paper();raw['disclosure']=effective_disclosure(raw['disclosure'],raw['rules'])
    changed=deepcopy(raw);changed['disclosure'][field]='unapproved body'
    assert product_content(raw)!=product_content(changed)


def test_public_strategy_text_bound_without_observation_timestamps():
    raw=paper();raw['disclosure']['public_fields']={'strategy':{
        'value':'原始策略正文','status':'verified','observations':[{'observed_at':'old'}]}}
    changed=deepcopy(raw);changed['disclosure']['public_fields']['strategy']['observations'][0]['observed_at']='new'
    assert product_content(raw)==product_content(changed)
    changed['disclosure']['public_fields']['strategy']['value']='同版本改变策略'
    assert product_content(raw)!=product_content(changed)
