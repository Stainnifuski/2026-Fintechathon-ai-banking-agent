"""Versioned static purchase disclosure, shared by compact/full reads and A.

Quote values/timestamps are not product terms. Public observation provenance
is separately bound by execution_policy's material/classification fingerprints.
No text trimming, numeric coercion or missing-to-null filling is performed.
"""
from copy import deepcopy

CONTENT_VERSION = 'wealth-static-disclosure-v2'
DISCLOSURE_FIELDS = (
    'version', 'identity', 'nature', 'manager', 'seller', 'risk', 'fees',
    'liquidity', 'holding_period_days', 'pricing', 'settlement', 'cancellation',
    'limitations', 'dataset_id', 'dataset_version', 'data_source', 'rules_source',
    'underlying_source_kind', 'execution_source_kind', 'source_product_code',
    'project_policy_version', 'issuer_risk_rating', 'official_reference',
    'source_rules', 'rules_adaptations', 'quick_redemption',
    'runtime_pricing_policy_version', 'base_pricing', 'base_limitations', 'public_fields',
)


def effective_disclosure(disclosure, rules):
    from .wealth_paper_policy import PRICING_MODES
    if rules.get('pricing_mode') not in PRICING_MODES:
        return disclosure
    # A serialized effective body is already versioned. Preserve it verbatim:
    # reapplying the overlay must not hide a changed pricing/limitations body.
    if 'runtime_pricing_policy_version' in disclosure:
        return disclosure
    from ..services.bank.wealth_quotes import POLICY_VERSION
    return {**disclosure,
        'base_pricing': disclosure.get('base_pricing', disclosure.get('pricing')),
        'base_limitations': disclosure.get('base_limitations', disclosure.get('limitations')),
        'pricing': '预览使用最近已公布的可靠净值；最终按订单计价日净值，未公布时等待，不使用虚构未来价格。',
        'limitations': '真实公开基金资料；比赛模拟资金、费用和交易，不代表真实代销或收益保证。',
        'runtime_pricing_policy_version': POLICY_VERSION}


def static_disclosure(disclosure, rules):
    value = effective_disclosure(disclosure, rules)
    result = {key: deepcopy(value[key]) for key in DISCLOSURE_FIELDS if key in value}
    # Objective/strategy/benchmark and issuer text are disclosure content too.
    # Keep their semantic values/status, without re-transmitting raw observation
    # paths/times. The independently checked material hash binds that provenance.
    if isinstance(result.get('public_fields'), dict):
        result['public_fields'] = {name: {k:v for k,v in fact.items()
            if k in ('value','status','reason')} if isinstance(fact,dict) else fact
            for name,fact in result['public_fields'].items()}
    return result


def product_content(row):
    keys = ('product_id', 'version', 'name', 'kind', 'category',
        'product_risk_level', 'identity', 'fund_family_id', 'dataset_key', 'share_class')
    rules = deepcopy(row.get('rules', {}))
    # The original quote anchor is provenance, not a mutable product term.
    # Identity/rating/material/fees/limits and all policy versions remain bound.
    if isinstance(rules.get('execution_policy'), dict):
        rules['execution_policy'].pop('quote', None)
    return {'content_contract': CONTENT_VERSION,
        **{key: row.get(key) for key in keys}, 'rules': rules,
        'disclosure': static_disclosure(row.get('disclosure', {}), row.get('rules', {}))}
