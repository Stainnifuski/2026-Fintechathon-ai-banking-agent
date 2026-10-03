"""Mixed-pool source and identity evaluations; not paid-model accuracy tests."""
import json
from pathlib import Path
import pytest
from backend.app.services.bank.wealth_preload import CONFIG, bank_products

CASES = json.loads((Path(__file__).parents[1] / 'fixtures/wealth_supplement_eval.json').read_text(encoding='utf-8'))

@pytest.fixture(scope='module')
def pool():
    return bank_products()

@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_template_identity_and_single_source(case, pool):
    templates = json.loads((CONFIG / 'bank_rules.json').read_text(encoding='utf-8'))['templates']
    source = templates[case['template']]
    selected = [p for p in pool if p['rules']['source_template_id'] == case['template']]
    assert selected
    for product in selected:
        assert product['identity'] == case['expected_identity']
        assert product['simulated'] and product['source_product_code'] is None
        assert product['official_reference'] == source['source']
        assert product['source_rules'] == source['source_rules']
        assert product['rules_adaptations'][:len(source['adaptations'])] == source['adaptations']
        if product['product_id'] not in {'B017','B018'}:
            assert product['rules_adaptations'] == source['adaptations']
        assert product['official_reference']['sha256'] and product['official_reference']['url'].startswith('https://')
        assert product['official_reference']['original_current_sale_verified'] is False
        assert product['fund_family_id'] == 'BANK-TEMPLATE-' + case['template']
        assert all('income_per_10000' not in point for point in product['history'])
    if case['template'] == 'closed':
        assert source['source']['original_expiry'] < '2026-10-02'
