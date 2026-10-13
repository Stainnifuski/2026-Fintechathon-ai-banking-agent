"""A1只读快照构建：不访问ORM、不调用模型、不提交订单。"""
from copy import deepcopy
from decimal import Decimal
from typing import Protocol
from ...schemas.wealth_portfolio import AssetSummary, AssetSnapshot, AccountFact, PositionFact


class SnapshotError(ValueError):
    def __init__(self, message: str, code: str='SNAPSHOT_INCONSISTENT', status_code: int=409) -> None:
        super().__init__(message)
        self.message, self.code, self.status_code = message, code, status_code


class AssetSnapshotSource(Protocol):
    async def read(self, user_id: str) -> dict:
        """提供B授权的全量AssetSummary及同次读取、同版本的完整明细。"""
        ...


class FixtureSnapshotSource:
    """明确注入的独立替身；不会自动进入正式应用。"""
    def __init__(self, bundle: dict, user_id: str='fixture-user') -> None:
        self.bundle, self.user_id = deepcopy(bundle), user_id

    async def read(self, user_id: str) -> dict:
        if user_id != self.user_id:
            raise SnapshotError('asset scope not authorized', 'ASSET_SCOPE_FORBIDDEN', 403)
        value = deepcopy(self.bundle)
        value['basis_status'] = 'fixture_only'
        value['summary']['data_source'] = 'fixture_only'
        return value


def product_version(lot: dict, products: list[dict]) -> int:
    if lot.get('product_version') is not None:
        return lot['product_version']
    # version_id为不透明标识，不通过拆分字符串猜版本。
    matches = [p for p in products if p.get('version_id') == lot.get('version_id')
               and p.get('product_id') == lot.get('product_id')]
    if len(matches) != 1 or matches[0].get('version') is None:
        raise SnapshotError('lot product version unavailable', 'PRODUCT_VERSION_UNAVAILABLE')
    return matches[0]['version']


def build_snapshot(bundle: dict) -> AssetSnapshot:
    """使用可信来源而非用户传入的资金或scope；逐项核对汇总和明细。

    旧单context接口不能独自构建全量快照。未知估值保留null。
    """
    if 'summary' not in bundle:
        raise SnapshotError('full AssetSummary required; context assets are not total assets', 'ASSET_SUMMARY_UNAVAILABLE', 503)
    summary = AssetSummary.model_validate(bundle['summary'])
    accounts = [AccountFact.model_validate(row) for row in bundle['accounts']]
    if {a.account_id for a in accounts} != set(summary.account_ids) or len(accounts) != len(summary.account_ids):
        raise SnapshotError('all authorized debit accounts required')
    for key, total in [('cash_balance_fen', sum(a.balance_fen for a in accounts)),
            ('held_fen', sum(a.held_fen for a in accounts)), ('available_cash_fen', sum(a.available_fen for a in accounts))]:
        if total != getattr(summary, key):
            raise SnapshotError('summary and account details mismatch: '+key)
    missing = list(summary.missing_fields)
    products = bundle.get('products', [])
    groups = {}
    seen_lots = set()
    for lot in bundle['lots']:
        if lot['account_id'] not in summary.account_ids or lot['lot_id'] in seen_lots:
            raise SnapshotError('out-of-scope or repeated lot')
        seen_lots.add(lot['lot_id'])
        version = product_version(lot, products)
        key = (lot['account_id'], lot['product_id'], version)
        bucket = {'cash': 'cash_management', 'money': 'cash_management', 'bond': 'stable',
            'mixed': 'growth', 'equity': 'growth'}.get(lot.get('category'))
        if bucket is None:
            missing.append('positions.'+lot['lot_id']+'.bucket')
        # 不使用成本或零替代缺少净值的市值。
        if lot.get('market_value_fen') is None:
            missing.append('positions.'+lot['lot_id']+'.market_value_fen')
        row = PositionFact.model_validate({'position_key': f'{key[0]}:{key[1]}:{key[2]}',
            'account_id': key[0], 'product_id': key[1], 'product_version': version,
            'lot_ids': [lot['lot_id']], 'units': lot['units'], 'redeemable_units': lot['redeemable_units'],
            'market_value_fen': lot.get('market_value_fen'), 'bucket': bucket,
            'nav': lot.get('nav'), 'nav_date': lot.get('nav_date')})
        if key not in groups:
            groups[key] = row
        else:
            old = groups[key]
            if (old.nav, old.nav_date, old.bucket) != (row.nav, row.nav_date, row.bucket):
                raise SnapshotError('conflicting observations within same product version')
            groups[key] = old.model_copy(update={'lot_ids': old.lot_ids+row.lot_ids,
                'units': format(Decimal(old.units)+Decimal(row.units), '.4f'),
                'redeemable_units': format(Decimal(old.redeemable_units)+Decimal(row.redeemable_units), '.4f'),
                'market_value_fen': None if old.market_value_fen is None or row.market_value_fen is None
                    else old.market_value_fen+row.market_value_fen})
    positions = sorted(groups.values(), key=lambda p: p.position_key)
    position_total = None if any(p.market_value_fen is None for p in positions) else sum(p.market_value_fen for p in positions)
    if summary.positions_market_value_fen != position_total:
        raise SnapshotError('summary and position valuation mismatch')
    orders = deepcopy(bundle['orders'])
    if len({o['operation_id'] for o in orders}) != len(orders) or any(o['account_id'] not in summary.account_ids for o in orders):
        raise SnapshotError('out-of-scope or duplicate order')
    pending = [o for o in orders if o.get('status') == 'PENDING_CASH' and o.get('direction') == 'redeem']
    if any(o.get('details', {}).get('actual', {}).get('cash_fen') is None for o in pending):
        receivable = None
        missing.append('orders.redemption_receivable_fen')
    else:
        amounts = [o['details']['actual']['cash_fen'] for o in pending]
        if any(type(v) is not int or v < 0 for v in amounts):
            raise SnapshotError('invalid receivable amount')
        receivable = sum(amounts)
    if summary.redemption_receivable_fen != receivable:
        raise SnapshotError('summary and pending redemption mismatch')
    if bundle.get('assessment') is None:
        missing.append('assessment')
    return AssetSnapshot.model_validate({'schema_version': 'wealth-asset-snapshot-v1',
        'snapshot_id': summary.snapshot_id, 'context_id': bundle.get('context_id'),
        'context_epoch': bundle.get('context_epoch'), 'asset_scope_id': summary.asset_scope_id,
        'account_ids': summary.account_ids, 'total_assets_fen': summary.total_assets_fen,
        'as_of': summary.as_of, 'business_at': summary.business_at, 'basis_kind': 'linked_bank_assets',
        'basis_status': bundle['basis_status'], 'basis_fen': summary.total_assets_fen,
        'valuation_complete': summary.valuation_complete, 'accounts': accounts, 'positions': positions,
        'receivable_fen': receivable, 'orders': orders, 'assessment': bundle.get('assessment'),
        'data_versions': bundle['data_versions'], 'missing_fields': sorted(set(missing))})


async def read_snapshot(source: AssetSnapshotSource, user_id: str) -> AssetSnapshot:
    return build_snapshot(await source.read(user_id))
