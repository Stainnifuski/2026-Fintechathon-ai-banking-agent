"""Competition execution overlay; public product terms and historical fills stay immutable."""
import hashlib
import json
from dataclasses import asdict
from datetime import date, timedelta

from ...domain.time import utc_now
from ...domain.wealth import TZ, purchase, redeem
from ...models.transaction import TransactionORM
from ...models.wealth import WealthLotORM, WealthProductVersionORM, WealthSettlementORM
from .audit_service import record_bank_event
from .transfer_service import BankError

POLICY_VERSION = 'competition-instant-v1'
NOTICE = '比赛模拟即时成交：按最近已公布有效净值计价，确认及MFA后立即扣款/到账；本金可能亏损。原产品持有期、开放窗口及额度继续适用，成交后不可撤。'


def bind(command, version, context, price):
    """Bind the execution policy and visible quote through the existing v2 digest."""
    if price is None:
        raise BankError('缺少已公布有效净值，请稍后重新预览', 'NAV_UNAVAILABLE', 409)
    payload = [POLICY_VERSION, version.version_id, command.disclosure_version,
               context.context_id, context.version, price.nav_id, price.nav_date.isoformat(), price.price]
    quote = 'instant-v1-' + hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
    if command.command_version == 2 and (command.nav_quote_id != quote or
            command.context_id != context.context_id or command.context_epoch != context.version):
        raise BankError('报价或资金范围已变化，请重新生成预览并确认', 'QUOTE_CHANGED', 409)
    return command.model_copy(update={'command_version': 2, 'nav_quote_id': quote,
        'context_id': context.context_id, 'context_epoch': context.version})


async def complete(db, row, account, command, version, now, preview):
    """Caller owns user/account locks and commits the entire batch atomically."""
    from .wealth_service import scaled, quantity, stable_id, eligible_lots, redemption_rate
    nav = preview['estimated_nav']
    day = now.astimezone(TZ).date()
    if command.direction == 'purchase':
        value = purchase(command.amount_fen, nav, version.rules['purchase_fee_rate'])
        paid = value.gross_fen-value.refund_fen
        account.balance_fen -= paid
        db.add(WealthLotORM(lot_id=stable_id('LOT', row.operation_id), operation_id=row.operation_id,
            user_id=row.user_id, account_id=row.account_id, version_id=row.version_id,
            units_scaled=scaled(value.units), cost_fen=paid, acquired_date=day,
            unlock_date=date.fromisoformat(version.rules['closed_maturity_date']) if version.rules.get('closed_maturity_date')
                else day+timedelta(days=version.rules['holding_days'])))
        row.units_scaled = scaled(value.units)
        actual, cash, prefix, kind = asdict(value), paid, 'WPT', 'investment_purchase'
    else:
        remaining, values = scaled(command.units), []
        for lot, free in await eligible_lots(db, row.user_id, command, now):
            take = min(remaining, free)
            if not take:
                continue
            lot_version = await db.get(WealthProductVersionORM, lot.version_id)
            value = redeem(quantity(take), nav, redemption_rate(lot_version.rules, (day-lot.acquired_date).days),
                lot_units=quantity(lot.units_scaled), lot_cost_fen=lot.cost_fen)
            lot.units_scaled -= take
            lot.cost_fen -= value.cost_fen
            values.append({'lot_id': lot.lot_id, **asdict(value)})
            remaining -= take
        if remaining:
            raise BankError('可赎份额已变化，请重新预览', 'INSUFFICIENT_REDEEMABLE_UNITS', 409)
        actual = {'lots': values, **{key: sum(v[key] for v in values)
            for key in ('gross_fen', 'fee_fen', 'cash_fen', 'cost_fen', 'realized_pnl_fen')}}
        cash, prefix, kind = actual['cash_fen'], 'WRT', 'investment_redemption'
        account.balance_fen += cash
    row.status = 'SETTLED'
    row.pricing_date = row.confirm_date = row.cash_date = day
    row.details = {**row.details, 'actual': actual, 'actual_nav': nav, 'waiting_reason': None,
        'source_nav_date': preview['estimated_nav_date'], 'execution_policy': POLICY_VERSION,
        'execution_source_kind': 'simulated', 'price_source': 'latest_published_valid_nav',
        'business_at': now.isoformat()}
    db.add(TransactionORM(tx_id=stable_id(prefix, row.operation_id), operation_id=row.operation_id,
        account_id=row.account_id, type=kind, amount_fen=cash, counterparty=version.name,
        category='investment', note='比赛模拟即时成交', timestamp=utc_now(), status='completed', channel='app'))
    for event_kind, action in [('pricing', 'investment_pricing')] + ([('cash', 'investment_cash')] if command.direction == 'redeem' else []):
        key = row.operation_id+':'+event_kind
        db.add(WealthSettlementORM(event_id=key, operation_id=row.operation_id, kind=event_kind,
            data={**actual, 'execution_policy': POLICY_VERSION, 'business_at': now.isoformat()}, created_at=utc_now()))
        record_bank_event(db, event_key=key, subject_user_id=row.user_id, action=action, result='success',
            facts={'operation_id': row.operation_id, 'actual': actual, 'cash_fen': cash, 'policy': POLICY_VERSION},
            decision_chain=['bound_confirmation_and_mfa_checked', 'published_quote_checked', 'atomic_immediate_ledger'])
