"""B0契约消费端样例：比例及快照语义；不声明A生产模型已经实现。"""
import json
from decimal import Decimal
from pathlib import Path
import pytest
from tests.fixtures.wealth_portfolio_contract_v1.provider import RecordedPortfolioProvider

FIXTURES = Path(__file__).parents[1] / 'fixtures' / 'wealth_portfolio_contract_v1'


def test_f01_envelope_ratios_and_snapshot_identity():
    data = json.loads((FIXTURES / 'f01.json').read_text(encoding='utf-8'))
    snapshot, plan = data['snapshot'], data['plan']
    assert snapshot['schema_version'] == 'wealth-asset-snapshot-v1'
    assert plan['schema_version'] == 'wealth-portfolio-plan-v1'
    assert snapshot['basis_status'] == plan['basis_status'] == 'fixture_only'
    assert plan['model_status'] == 'fixture'
    assert snapshot['assessment'] is None  # fixture must not authorize a customer
    for key in ('snapshot_id', 'context_id', 'asset_scope_id', 'basis_fen'):
        assert snapshot[key] == plan[key]
    cash = sum(account['balance_fen'] for account in snapshot['accounts'])
    assert cash + snapshot['receivable_fen'] == plan['basis_fen'] == 1000000
    assert {bucket['bucket'] for bucket in plan['buckets']} == {'cash_management', 'stable', 'growth'}
    ratios = {bucket['bucket']: Decimal(bucket['target_ratio']) for bucket in plan['buckets']}
    assert sum(ratios.values()) == 1
    assert ratios['growth'] / (1 - ratios['cash_management']) == Decimal(plan['growth_ratio_ex_cash']) == Decimal('0.625')
    for bucket in plan['buckets']:
        assert Decimal(bucket['min_ratio']) <= Decimal(bucket['target_ratio']) <= Decimal(bucket['max_ratio'])
        assert int(Decimal(bucket['target_ratio']) * plan['basis_fen']) == bucket['target_fen']
    assert not plan['candidate_groups'] and not plan['selected_positions']


def test_f18_all_accounts_and_held_cash_are_not_counted_twice():
    snapshot = json.loads((FIXTURES / 'f18.json').read_text(encoding='utf-8'))['snapshot']
    assert len(snapshot['accounts']) == 2
    assert snapshot['account_ids'] == [account['account_id'] for account in snapshot['accounts']]
    assert sum(account['available_fen'] for account in snapshot['accounts']) == 700000
    cash = sum(account['balance_fen'] for account in snapshot['accounts'])
    assert sum(account['held_fen'] for account in snapshot['accounts']) == 100000
    positions = sum(position['market_value_fen'] for position in snapshot['positions'])
    assert cash + positions + snapshot['receivable_fen'] == snapshot['total_assets_fen'] == snapshot['basis_fen'] == 1000000


def test_f10_missing_valuation_does_not_become_zero_assets():
    snapshot = json.loads((FIXTURES / 'f10.json').read_text(encoding='utf-8'))['snapshot']
    assert not snapshot['valuation_complete']
    assert snapshot['total_assets_fen'] is None and snapshot['basis_fen'] is None
    assert snapshot['positions'][0]['market_value_fen'] is None
    assert snapshot['missing_fields']


def test_recorded_provider_preserves_inputs_and_rejects_unrecorded_revisions():
    provider = RecordedPortfolioProvider()
    plan = provider.plan()
    selection = {
        'portfolio_id': plan['portfolio_id'], 'expected_version': plan['version'],
        'snapshot_id': plan['snapshot_id'], 'context_id': plan['context_id'], 'items': [],
    }
    assert provider.revise(selection) == plan
    assert provider.recommend() == []
    # Returned objects may be edited by a consumer without altering the shared source.
    provider.snapshot()['accounts'].clear()
    assert provider.snapshot()['accounts']
    plan['version'] = 999
    assert provider.plan()['version'] == 1
    with pytest.raises(LookupError):
        provider.revise({**selection, 'expected_version': 999})
    with pytest.raises(LookupError):
        provider.revise({**selection, 'items': [{'target_ratio_total': '0.9'}]})
    with pytest.raises(LookupError):
        provider.plan('F10')
