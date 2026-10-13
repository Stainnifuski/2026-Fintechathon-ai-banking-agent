"""Regenerate shared F01–F24. Synthetic contract examples, NOT an allocation model."""
import json
from copy import deepcopy
from pathlib import Path

NOW = '2026-10-05T04:00:00+00:00'


def bundle() -> dict:
    return {'summary': {'schema_version': 'wealth-asset-summary-v1', 'asset_scope_id': 'fixture-scope',
        'account_ids': ['fixture-account'], 'snapshot_id': 'fixture-snapshot', 'as_of': NOW,
        'business_at': NOW, 'cash_balance_fen': 1000000, 'held_fen': 0, 'available_cash_fen': 1000000,
        'positions_market_value_fen': 0, 'redemption_receivable_fen': 0, 'total_assets_fen': 1000000,
        'valuation_complete': True, 'missing_fields': [], 'data_source': 'fixture_only'},
        'context_id': 'fixture-context', 'context_epoch': None, 'basis_status': 'fixture_only',
        'accounts': [{'account_id': 'fixture-account', 'account_type': 'savings', 'balance_fen': 1000000,
                      'held_fen': 0, 'available_fen': 1000000}], 'lots': [], 'orders': [],
        'assessment': {'assessment_id': 'fixture-assessment', 'capacity_level': 4, 'purchase_allowed': True,
            'expires_at': '2027-10-01T00:00:00Z', 'confirmed': True},
        'data_versions': {'contract': '00-v0.3', 'data': 'fixture_only'}}


def candidate(family: str='fixture-family', code: str='fixture-product', risk: int=2, share: str='A') -> dict:
    performance = {'metric': 'nav_change', 'value_percent': None, 'status': 'unavailable',
        'reason': 'fixture has no market history', 'source': 'fixture_only'}
    return {'family_id': family, 'name': family, 'bucket': 'stable', 'reason': 'contract fixture only',
        'reason_codes': ['FIXTURE_ONLY'], 'evidence': {'source': 'fixture_only'}, 'share_classes': [{
            'product_id': code, 'product_version': 1, 'disclosure_version': 1, 'share_class': share,
            'product_risk_level': risk, 'status': 'on_sale', 'tradable': True, 'min_amount_fen': 100,
            'fees': {'purchase_fee_rate': None, 'sales_service_fee_rate': None,
                'redemption_fee_rules': None, 'missing_fields': ['all_fees']},
            'performance': {'one_year': performance, 'latest_day': {**performance, 'metric': 'daily_nav_change'}},
            'detail_ref': '/products/'+code+'/detail', 'data_source': 'fixture_only', 'data_as_of': NOW}]}


def plan() -> dict:
    return {'schema_version': 'wealth-portfolio-plan-v1', 'portfolio_id': 'fixture-plan', 'version': 1,
        'snapshot_id': 'fixture-snapshot', 'asset_scope_id': 'fixture-scope', 'context_id': 'fixture-context',
        'as_of': NOW, 'basis_kind': 'linked_bank_assets', 'basis_status': 'fixture_only', 'basis_fen': 1000000,
        'provider_id': 'fixture_only', 'provider_version': '00-v0.3', 'model_status': 'fixture',
        'buckets': [{'bucket': b, 'min_ratio': lo, 'target_ratio': target, 'max_ratio': hi,
            'target_fen': amount, 'current_fen': 0, 'reason': 'fixed interface example, not model parameters'}
            for b, lo, target, hi, amount in [('cash_management', '0.15', '0.20', '0.25', 200000),
                ('stable', '0.25', '0.30', '0.35', 300000), ('growth', '0.45', '0.50', '0.55', 500000)]],
        'growth_ratio_ex_cash': '0.625', 'candidate_groups': [], 'selected_positions': [],
        'cash_residual_fen': 200000, 'data_versions': {'fixture': '00-v0.3'}, 'missing_fields': [],
        'reasons': ['FIXTURE_ONLY']}


def lot(lot_id: str='fixture-lot', value: int=200000, units: str='1000.0000') -> dict:
    return {'lot_id': lot_id, 'account_id': 'fixture-account', 'product_id': 'fixture-product',
        'version_id': 'fixture-product:1', 'product_version': 1, 'category': 'bond', 'units': units,
        'redeemable_units': units, 'market_value_fen': value, 'nav': '2.00000000', 'nav_date': '2026-09-30'}


def generate() -> dict:
    cases = {}
    def add(n: int, inputs: dict, expected: dict, owner: str='A_contract') -> None:
        cases[f'F{n:02}'] = {'fixture_id': f'F{n:02}', 'contract_version': '00-v0.3',
            'data_status': 'fixture_only', 'owner': owner, 'input': inputs, 'expected': expected}
    add(1, {'bundle': bundle(), 'plan': plan()}, {'basis_fen': 1000000, 'growth_ratio_ex_cash': '0.625'})
    r1 = plan()
    for b in r1['buckets']:
        b.update(min_ratio='1' if b['bucket']=='cash_management' else '0',
            target_ratio='1' if b['bucket']=='cash_management' else '0', max_ratio='1' if b['bucket']=='cash_management' else '0',
            target_fen=1000000 if b['bucket']=='cash_management' else 0)
    r1.update(growth_ratio_ex_cash=None, cash_residual_fen=1000000)
    add(2, {'capacity_level': 1, 'plan': r1}, {'growth_ratio_ex_cash': None})
    group = {'bucket': None, 'filter': {'mode': 'theme', 'theme_key': 'high_dividend'},
        'status': 'ok', 'items': [candidate()], 'risk_explanation': None}
    add(3, {'capacity_level': 2, 'matched_risks': [2, 4], 'group': group}, {'status': 'ok', 'risk_explanation': None}, 'A2_future')
    add(4, {'capacity_level': 2, 'group': {**group, 'status': 'risk_mismatch', 'items': [],
        'risk_explanation': {'user_level': 2, 'product_levels': [4]}}}, {'status': 'risk_mismatch'}, 'A2_future')
    add(5, {'group': {**group, 'status': 'no_theme_match', 'items': []}}, {'status': 'no_theme_match'}, 'A2_future')
    add(6, {'holding_units': '1000.0000', 'nav': '2', 'market_value_fen': 100000},
        {'resolved_units': '500.0000', 'holding_ratio': '0.5', 'estimated_market_value_fen': 100000, 'notice': 'estimated; cash not guaranteed'}, 'B')
    add(7, {'holding_units': '1000.0000', 'nav': '2', 'units': '200.0000'},
        {'holding_ratio': '0.2', 'estimated_market_value_fen': 40000}, 'B')
    b = bundle(); b['accounts'][0].update(balance_fen=800000, held_fen=100000, available_fen=700000)
    b['summary'].update(cash_balance_fen=800000, held_fen=100000, available_cash_fen=700000, positions_market_value_fen=200000)
    b['lots'] = [lot()]
    add(8, {'bundle': b}, {'basis_fen': 1000000, 'available_cash_fen': 700000})
    family = candidate(); family['share_classes'].append(candidate(code='fixture-C', share='C')['share_classes'][0])
    only_c = deepcopy(family); only_c['share_classes'] = only_c['share_classes'][1:]
    add(9, {'family': family, 'only_c': only_c}, {'family_seats': 1, 'default': 'A', 'fallback': 'C'}, 'A2_B_future')
    incomplete = deepcopy(b); incomplete['lots'][0].update(market_value_fen=None, nav=None, nav_date=None)
    incomplete['summary'].update(positions_market_value_fen=None, total_assets_fen=None, valuation_complete=False,
        missing_fields=['positions.fixture-lot.nav'])
    add(10, {'bundle': incomplete}, {'basis_fen': None, 'valuation_complete': False})
    selection = {'portfolio_id': 'fixture-plan', 'expected_version': 1, 'snapshot_id': 'fixture-snapshot',
        'context_id': 'fixture-context', 'items': [{'product_id': f'fixture-{i}', 'product_version': 1,
            'share_class': None, 'target_ratio_total': '0.1'} for i in range(7)]}
    add(11, {'selection': selection}, {'selected_count': 7, 'confirmation_count': 1}, 'A_contract_B_execution_future')
    add(12, {'position_keys': ['p1', 'p2'], 'market_value_fen': 100000}, {'status': 'clarify', 'orders_created': 0}, 'B')
    add(13, {'balance_fen': 800000, 'held_fen': 0, 'purchase_fen': 100000},
        {'preview': [800000, 0, 800000], 'accepted': [800000, 100000, 700000], 'cancelled': [800000, 0, 800000]}, 'B')
    add(14, {'filters': [{'mode': 'general'}, {'mode': 'category', 'category': 'bond_fund'},
        {'mode': 'theme', 'theme_key': 'high_dividend'}, {'mode': 'category', 'category': 'equity_fund'}]},
        {'no_text_parser': True}, 'A_contract_B_actions_future')
    add(15, {'llm_enabled': False}, {'model_call_count': 0, 'full_manual_flow_required': True}, 'A_contract_B_execution_future')
    add(16, {'filters': [{'mode': 'category', 'category': 'money_fund'}, {'mode': 'category', 'category': 'mixed_fund'}],
        'page': 2, 'market_value_fen': 123456}, {'parameter_driven': True}, 'A2_B_future')
    add(17, {'old_schema': 'wealth-subtask-v1', 'new_schema': 'wealth-action-v1'}, {'not_interchangeable': True}, 'B')
    multi = bundle(); multi['summary'].update(account_ids=['fixture-account', 'fixture-account-2'],
        cash_balance_fen=800000, held_fen=100000, available_cash_fen=700000,
        positions_market_value_fen=150000, redemption_receivable_fen=50000)
    multi['accounts'][0].update(balance_fen=600000, held_fen=100000, available_fen=500000)
    multi['accounts'].append({'account_id': 'fixture-account-2', 'account_type': 'checking', 'balance_fen': 200000,
        'held_fen': 0, 'available_fen': 200000})
    multi['lots'] = [lot(value=100000, units='500.0000'), lot('fixture-lot-2', value=50000, units='250.0000')]
    multi['orders'] = [{'operation_id': 'fixture-receivable', 'account_id': 'fixture-account-2', 'direction': 'redeem',
        'status': 'PENDING_CASH', 'details': {'actual': {'cash_fen': 50000}}}]
    add(18, {'bundle': multi}, {'total_assets_fen': 1000000, 'basis_fen': 1000000, 'available_cash_fen': 700000})
    add(19, {'transactions': [{'type': 'purchase', 'amount_fen': 20000}, {'type': 'investment_purchase', 'amount_fen': 100000},
        {'type': 'investment_redemption', 'amount_fen': 110000}]}, {'history_count': 3, 'bill_count': 1, 'bill_fen': 20000}, 'B')
    add(20, {'opening_fen': 100000, 'invested_fen': 50000, 'closing_fen': 150000}, {'pnl_fen': 0}, 'B')
    add(21, {'opening_position_fen': 100000, 'closing_receivable_fen': 110000, 'next_period_cash_fen': 110000},
        {'pricing_pnl_fen': 10000, 'arrival_pnl_fen': 0, 'cumulative_pnl_fen': 10000}, 'B')
    add(22, {'fees_in_nav': True, 'purchase_fee_in_cashflow': True}, {'deduct_again': False}, 'B')
    add(23, {'order_ids': ['order-1'], 'position_keys': ['position-1'], 'periods': ['day', 'month', 'year', 'cumulative']},
        {'independent_selection': True, 'order_id_redeemable': False}, 'B')
    add(24, {'history_cost_missing': True, 'daily_valuation': None}, {'daily_pnl_fen': None, 'status': 'partial'}, 'B')
    return cases


if __name__ == '__main__':
    target = Path(__file__).resolve().parents[1]/'tests/fixtures/wealth_portfolio_contract_v1'
    target.mkdir(parents=True, exist_ok=True)
    for name, case in generate().items():
        path = target/(name+'.json')
        if not path.exists() and (target/(name.lower()+'.json')).exists():
            path = target/(name.lower()+'.json')
        # Preserve B's independent recorded fields in the shared case.
        existing = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        path.write_text(json.dumps({**existing, **case}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
