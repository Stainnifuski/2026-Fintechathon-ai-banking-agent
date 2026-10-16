"""A0既有产品字段适配；不排序、不执行资格筛选、不补造披露版本。"""
from datetime import datetime
from ..schemas.wealth_portfolio import ShareClass


def adapt_product_share(product: dict, observed_at: datetime) -> ShareClass:
    """研究目录或银行产品原料转为份额事实，缺必要版本时拒绝。"""
    rules = product.get('rules') or {}
    source = product.get('source') or product.get('data_source') or product.get('disclosure', {}).get('data_source')
    version = product.get('product_version', product.get('version'))
    disclosure_version = product.get('disclosure_version', product.get('disclosure', {}).get('version'))
    if version is None or disclosure_version is None:
        raise ValueError('explicit product and disclosure versions required')
    if product.get('product_version', version) != product.get('version', version):
        raise ValueError('conflicting product versions')
    fees = {'purchase_fee_rate': rules.get('purchase_fee_rate'),
        'sales_service_fee_rate': rules.get('sales_service_fee_rate'),
        'redemption_fee_rules': rules.get('redeem_fee_bands')}
    fees['missing_fields'] = [key for key, value in fees.items() if value is None]
    metric = {'metric': 'nav_change', 'value_percent': None, 'status': 'unavailable',
        'reason': 'one-year history not supplied by catalog; unit NAV is not total return', 'source': source}
    return ShareClass.model_validate({'product_id': product['product_id'], 'product_version': version,
        'disclosure_version': disclosure_version, 'share_class': product.get('share_class', rules.get('share_class')),
        'product_risk_level': product.get('product_risk_level', product.get('risk_level')), 'status': product.get('status', 'unknown'),
        # 缺少已验证资格时保守不可选；这不是A2正式资格算法。
        'tradable': product.get('trade_eligible', False), 'min_amount_fen': rules.get('min_amount_fen'),
        'fees': fees, 'performance': {'one_year': metric, 'latest_day': {**metric,
            'metric': 'daily_nav_change', 'nav_date': product.get('nav_date'), 'reason': 'previous published NAV not supplied'}},
        'detail_ref': '/api/v1/wealth/products/'+product['product_id']+'/detail', 'data_source': source,
        'data_as_of': observed_at})


def adapt_legacy_asset_details(summary: dict, assets: dict, products: list[dict],
        assessment: dict | None, *, basis_status: str = 'pending_decision') -> dict:
    """仅附有B全量汇总时适配旧字段；全范围一致性由builder再校验。

    调用方必须提供同次读取、覆盖全scope的明细；旧单context结果不能自行升级。
    产品版本从精确version_id映射，正式评估仅保留事实，不读取用户risk_level替代。
    """
    from copy import deepcopy
    if datetime.fromisoformat(assets['as_of']) != datetime.fromisoformat(summary['as_of']):
        raise ValueError('summary and detail observation times differ; require one coherent read')
    facts = None if assessment is None else {k: assessment[k] for k in
        ('assessment_id','capacity_level','purchase_allowed','expires_at','confirmed')}
    return {'summary': deepcopy(summary), 'accounts': [{k:r[k] for k in
        ('account_id','account_type','balance_fen','held_fen','available_fen')} for r in assets['cash']],
        'lots': deepcopy(assets['lots']), 'orders': deepcopy(assets['orders']), 'products':deepcopy(products),
        'assessment':facts, 'context_id':assets.get('context_id'), 'context_epoch':assets.get('context_epoch'),
        'basis_status':basis_status, 'data_versions':{'asset_summary_snapshot_id':summary['snapshot_id'],
            'asset_summary_source':summary['data_source'], 'legacy_details_as_of':assets['as_of']}}
