"""Prepare approved real-public/paper-execution dataset from frozen local facts.

No collection, no model parameter edits, no database writes. Entire eligible
intersection is considered; joint_samples are acceptance data, not a whitelist.
"""
import argparse, json, socket
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from backend.app.services.orchestration.wealth_portfolio_data import read, canonical, digest
from backend.app.integrations.wealth_portfolio_data import validate_evidence, read_raw, records, clean
from backend.app.services.orchestration.wealth_portfolio_data import apply_evidence
from backend.app.domain.wealth_portfolio_candidates import issuer_identity, sourced_value
from backend.app.domain.wealth_paper_policy import LEVELS, POLICY_VERSION, PRICING_MODE, validate_policy
from backend.app.domain.wealth_calendar import CALENDAR_VERSION
JOINT_SAMPLES = ['000015', '001286', '005242', '006253', '007331', '016173', '020458']

def prepare(root, version, approved_at, raw_root=None):
    snapshot = read(root, root / '.deps/wealth_portfolio_data/snapshots', version)
    manifest = json.loads((root / 'config/wealth/portfolio_evidence_manifest.json').read_text(encoding='utf-8-sig'))
    codes = {p.product_id for p in snapshot.products if p.source_kind == 'real'}
    entries = [validate_evidence(root, e, manifest['authority_sources'], codes) for e in manifest['records']]
    daily = {d.product_id: d for d in snapshot.daily if d.source_kind == 'real'}
    products = []
    excluded = {}
    legacy_path = root / 'config/wealth/paper_products_legacy_v1.json'
    legacy = json.loads(legacy_path.read_text(encoding='utf-8-sig')) if legacy_path.exists() else {'products': []}
    legacy_codes = {p['product_id'] for p in legacy['products']}
    core_path = root / 'config/wealth/paper_products_core_v2.json'
    core = json.loads(core_path.read_bytes()) if core_path.exists() else {'products': []}
    core_by_code = {p['product_id']: p for p in core['products']}
    for p in snapshot.products:
        if p.source_kind != 'real':
            continue
        apply_evidence(p, entries, snapshot.as_of)
        issuer, problem = issuer_identity(p)
        texts = {f: sourced_value(p, f)[0] for f in ('objective', 'strategy', 'benchmark', 'tracking_target')}
        d = daily[p.product_id]
        nav = d.fields['unit_nav']
        status = d.fields['purchase_status'].value
        reasons = []
        if p.scope_status != 'verified' or p.substantive_category not in LEVELS:
            reasons.append('verified_substantive_category_required')
        if not p.name or not p.product_id.isdigit() or len(p.product_id) != 6:
            reasons.append('verified_exact_identity_required')
        if nav.value is None or nav.status == 'conflict':
            reasons.append('visible_public_unit_NAV_required')
        if nav.value is not None:
            observations = [o for o in nav.observations if o.value == nav.value and o.unit == 'yuan_per_share' and o.as_of and o.observed_at and o.reference.sha256 and (o.as_of <= o.observed_at.date()) and (o.observed_at <= snapshot.as_of)]
            if not observations:
                reasons.append('NAV_unit_date_source_required')
        if status != '开放申购':
            reasons.append('saved_public_sale_status_not_open')
        if reasons:
            excluded[p.product_id] = reasons
            continue
        observation = observations[0]
        quote = {'value': nav.value, 'date': str(observation.as_of), 'available_at': observation.observed_at.isoformat(), 'reference_sha256': observation.reference.sha256, 'source': observation.source, 'date_basis': observation.reason or 'explicit_source_date'}
        level = LEVELS[p.substantive_category]
        policy = {'version': POLICY_VERSION, 'product_id': p.product_id, 'underlying_source_kind': 'real', 'execution_source_kind': 'simulated', 'category': p.substantive_category, 'classification_status': p.classification_status, 'classification_sha256': digest({'code': p.product_id, 'category': p.substantive_category, 'basis': p.classification_basis, 'references': [r.model_dump(mode='json') for r in p.references]}), 'material_sha256': digest({f: p.fields[f].model_dump(mode='json') for f in texts}), 'risk_level': level, 'approved_at': approved_at, 'quote': quote, 'public_ratings': [{'risk_level': e.risk_level, 'reference_sha256': e.reference.sha256, 'channel': e.channel, 'published_on': str(e.published_on) if e.published_on else None} for e in p.risk_evidence], 'classification_conflict': any((p.fields.get(f) and p.fields[f].status == 'conflict' for f in ('raw_category', 'raw_risk', 'formal_risk')))}
        problems = validate_policy(policy, p.product_id, p.substantive_category, level)
        if problems:
            excluded[p.product_id] = problems
            continue
        rules = {'currency': 'CNY', 'min_amount_fen': 10000, 'max_amount_fen': 200000000, 'purchase_fee_rate': '0', 'sales_service_fee_rate': '0', 'redeem_fee_bands': [{'days_lt': 1000000, 'rate': '0'}], 'holding_days': 0, 'confirm_business_days': 1, 'cash_business_days': 2, 'cutoff': '15:00', 'calendar_version': CALENDAR_VERSION, 'cancel_before_pricing': True, 'purchase_paused': False, 'redeem_paused': False, 'rules_source': 'approved_competition_paper_rules_20261013', 'pricing_mode': PRICING_MODE, 'execution_policy': policy, 'holding_allowed': True, 'redemption_allowed': d.fields['redemption_status'].value == '开放赎回'}
        rules['redeem_paused'] = not rules['redemption_allowed']
        public_fields = {f: p.fields[f].model_dump(mode='json') for f in ('issuer_raw', 'issuer_normalized', *texts)}
        history_by_date = {}
        if raw_root:
            for ref in snapshot.input_references:
                if ref.kwargs.get('symbol') != p.product_id or ref.kwargs.get('indicator') != '单位净值走势':
                    continue
                if ref.function not in ('public_unit_nav_history', 'fund_open_fund_info_em') or ref.observed_at > snapshot.as_of:
                    continue
                for raw in records(read_raw(raw_root, ref)):
                    day = str(raw.get('净值日期', ''))[:10]
                    value = clean(raw.get('单位净值'))
                    if not day or value is None or day > quote['date']:
                        continue
                    try:
                        date.fromisoformat(day)
                        number = Decimal(str(value))
                        if not number.is_finite() or number <= 0:
                            continue
                    except (ValueError, ArithmeticError):
                        continue
                    point = {'date': day, 'nav': str(value), 'available_at': ref.observed_at.isoformat(), 'source': 'real_public', 'reference_sha256': ref.sha256}
                    previous = history_by_date.get(day)
                    if previous and previous.get('conflict'):
                        continue
                    if previous and previous['nav'] != point['nav']:
                        history_by_date[day] = {'conflict': True}
                        continue
                    if not previous or point['available_at'] < previous['available_at']:
                        history_by_date[day] = point
        history_by_date[quote['date']] = {'date': quote['date'], 'nav': quote['value'], 'available_at': quote['available_at'], 'source': 'real_public', 'reference_sha256': quote['reference_sha256']}
        history = [v for _, v in sorted(history_by_date.items()) if not v.get('conflict')]
        products.append({'product_id': p.product_id, 'source_product_code': p.product_id, 'name': p.name, 'source': 'akshare', 'manager': issuer, 'kind': 'open_fund', 'category': p.substantive_category, 'share_class': p.share_class, 'risk_level': level, 'product_version': core_by_code[p.product_id]['product_version'] + 1 if p.product_id in core_by_code else 2 if p.product_id in legacy_codes else 1, 'disclosure_version': core_by_code[p.product_id]['disclosure_version'] + 1 if p.product_id in core_by_code else 2 if p.product_id in legacy_codes else 1, 'rules_complete': True, 'rules': rules, 'identity': '真实公开基金资料＋比赛模拟交易', 'underlying_source_kind': 'real', 'execution_source_kind': 'simulated', 'public_fields': public_fields, 'public_sources': [r.model_dump(mode='json') for r in p.references], 'public_action_fields': {f: d.fields[f].model_dump(mode='json') for f in ('purchase_status', 'redemption_status', 'purchase_minimum', 'purchase_limit')}, 'real_unit_nav': nav.value, 'price_source': 'real_public_frozen_NAV', 'investment_objective': texts['objective'], 'investment_strategy': texts['strategy'], 'benchmark': texts['benchmark'], 'tracking_target': texts['tracking_target'], 'public_risk_rating': [e.model_dump(mode='json') for e in p.risk_evidence], 'known_relationships': [e.model_dump(mode='json') for e in p.family_evidence], 'history': history, 'history_boundary': 'saved observations only; observation time is conservative availability, not publisher timestamp', 'history_conflict_dates': [d for d, v in history_by_date.items() if v.get('conflict')]})
    revision = digest({'source_version': version, 'policy': POLICY_VERSION, 'products': products})[:24]
    for p in products:
        p['rules'].update(dataset_id='real-public-paper-v1', dataset_version=revision)
    payload = {'schema_version': 1, 'dataset_id': 'real-public-paper-v1', 'dataset_version': revision, 'mode': 'snapshot', 'source': 'real_public_with_simulated_execution', 'source_snapshot': version, 'approved_at': approved_at, 'policy_version': POLICY_VERSION, 'products': products, 'joint_samples': JOINT_SAMPLES, 'boundary': 'No issuer-rating equivalence; real public facts and simulated execution labelled separately; frozen public NAV anchor; not a production whitelist'}
    return (payload, excluded)

def main():

    def denied(*args, **kwargs):
        raise RuntimeError('NETWORK_FORBIDDEN_OFFLINE_PAPER')
    socket.socket = denied
    socket.create_connection = denied
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--raw-root', type=Path)
    p.add_argument('--version', required=True)
    p.add_argument('--approved-at', required=True)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    payload, excluded = prepare(a.root, a.version, a.approved_at, a.raw_root)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(payload)
    if a.output.exists() and a.output.read_bytes() != raw:
        raise ValueError('IMMUTABLE_DATASET_OUTPUT_CONFLICT')
    a.output.write_bytes(raw)
    print(json.dumps({'dataset_key': payload['dataset_id'] + ':' + payload['dataset_version'], 'products': len(payload['products']), 'samples_present': {c: any((p['product_id'] == c for p in payload['products'])) for c in JOINT_SAMPLES}, 'sha256': digest(payload), 'exclusion_count': len(excluded)}, ensure_ascii=False))
if __name__ == '__main__':
    main()
