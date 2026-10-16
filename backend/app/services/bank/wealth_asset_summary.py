"""One bank-fact total; no estimates, transfers or LLM calls."""
import hashlib
import json
from datetime import datetime

from sqlalchemy import text

from ...domain.time import BEIJING
from ...schemas.wealth_assets import AssetSummary


def digest(prefix, value):
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return prefix + hashlib.sha256(canonical.encode()).hexdigest()


def build(user_id: str, facts: dict) -> dict:
    """Consume the full current bank scope, never a paginated/client account list."""
    cash = sorted((a for a in facts['cash'] if a['account_type'] in {'savings', 'checking'}), key=lambda a: a['account_id'])
    accounts = [a['account_id'] for a in cash]
    authorized = set(accounts)
    lots = sorted((lot for lot in facts['lots'] if lot['account_id'] in authorized), key=lambda lot: lot['lot_id'])
    orders = sorted((order for order in facts['orders'] if order['account_id'] in authorized), key=lambda order: order['operation_id'])
    receivable = sum(order['details']['actual']['cash_fen'] for order in orders if order['status'] == 'PENDING_CASH')
    missing = [f"lots.{lot['lot_id']}.market_value_fen" for lot in lots if lot['market_value_fen'] is None]
    value = None if missing else sum(lot['market_value_fen'] for lot in lots)
    balance = sum(a['balance_fen'] for a in cash)
    scope = digest('WAS-', {'user_id': user_id, 'context_id': facts['context_id'], 'account_ids': accounts})
    business_day = datetime.fromisoformat(facts['business_at']).astimezone(BEIJING).date().isoformat()
    snapshot = digest('WASNAP-', {'asset_scope_id': scope, 'business_day': business_day, 'cash': cash, 'lots': lots, 'orders': orders})
    return AssetSummary(
        asset_scope_id=scope, account_ids=accounts, snapshot_id=snapshot,
        as_of=facts['as_of'], business_at=facts['business_at'], cash_balance_fen=balance,
        held_fen=sum(a['held_fen'] for a in cash), available_cash_fen=sum(a['available_fen'] for a in cash),
        positions_market_value_fen=value, redemption_receivable_fen=receivable,
        total_assets_fen=None if value is None else balance + value + receivable,
        valuation_complete=not missing, missing_fields=missing, data_source='simulated_bank',
    ).model_dump(mode='json')


async def begin_read(db) -> None:
    """Dedicated read transaction: cash/holds/lots/orders share one DB snapshot."""
    if db.in_transaction():
        raise RuntimeError('AssetSummary reader requires a fresh database session')
    if db.get_bind().dialect.name == 'sqlite':
        # Python's legacy SQLite transaction mode does not begin on SELECT.
        await db.execute(text('BEGIN'))
    else:
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))


async def read(db, user_id: str) -> dict:
    await begin_read(db)
    from .wealth_service import assets
    return (await assets(db, user_id))['asset_summary']
