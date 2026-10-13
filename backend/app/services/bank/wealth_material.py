"""Read existing immutable dataset material for an exact current bank version.

This exposes already-defined fields; it never supplies missing objectives,
strategy, rating, share relationships or benchmark definitions.
"""
from ...models.wealth_research import WealthDatasetORM, WealthResearchProductORM


async def current_material(db, version, *, records=None, datasets=None):
    rules = version.rules
    if not rules.get('dataset_id') or not rules.get('dataset_version'):
        return {}
    key = rules['dataset_id'] + ':' + rules['dataset_version']
    dataset = datasets.get(key) if datasets is not None else await db.get(WealthDatasetORM, key)
    record = records.get(key + ':' + version.product_id) if records is not None else await db.get(WealthResearchProductORM, key + ':' + version.product_id)
    if dataset is None or record is None:
        return {}
    raw = record.data
    # No name matching or borrowing older/different rules as current evidence.
    if (raw.get('product_id') != version.product_id
            or raw.get('product_version') != version.version
            or raw.get('risk_level') != version.risk_level
            or raw.get('category') != version.category
            or raw.get('kind') != version.kind
            or raw.get('rules') != rules):
        return {}
    for field in ('share_class', 'fund_family_id'):
        if rules.get(field) is not None and raw.get(field) != rules[field]:
            return {}
    result = {field: raw[field] for field in (
        'share_class', 'fund_family_id', 'investment_objective',
        'investment_strategy', 'benchmark', 'tracking_target')
        if field in raw and raw[field] is not None}
    result['material_origin'] = {
        'source': 'immutable_project_simulation_dataset', 'dataset_key': key,
        'dataset_fingerprint': dataset.manifest['fingerprint'],
        'product_id': version.product_id, 'product_version': version.version,
        'fields': sorted(result),
    }
    return result
