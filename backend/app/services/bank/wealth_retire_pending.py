"""One-way retirement of legacy deferred execution; no future NAV is invented."""
from sqlalchemy import select,update
from ...models import AccountORM,TransactionORM
from ...models.wealth import WealthOrderORM,WealthCashHoldORM,WealthUnitHoldORM,WealthSettlementORM,WealthProductVersionORM
from ...domain.time import utc_now
from .audit_service import record_bank_event
from .transfer_service import BankError

POLICY='remove-deferred-execution-20261017-v1'
WAITING={'ACCEPTED','PENDING_PRICING','PENDING_CASH'}


async def retire(db,operation_id=None):
    from .wealth_service import lock_accounts,stable_id
    query=select(WealthOrderORM).where(WealthOrderORM.status.in_(WAITING))
    if operation_id:query=query.where(WealthOrderORM.operation_id==operation_id)
    rows=list(await db.scalars(query.order_by(WealthOrderORM.account_id,WealthOrderORM.operation_id)))
    await lock_accounts(db,[r.account_id for r in rows])
    result={'cancelled':0,'released_fen':0,'receivables_paid_fen':0}
    for original in rows:
        row=await db.get(WealthOrderORM,original.operation_id,populate_existing=True)
        if row.status not in WAITING:continue
        if row.status=='PENDING_CASH':
            # Pricing has already reduced units. Pay the recorded receivable
            # once; do not cancel it or price the redeemed units a second time.
            cash=row.details.get('actual',{}).get('cash_fen')
            if type(cash) is not int or cash<0:raise BankError('历史赎回应收事实不完整','BANK_LEDGER_INVALID',503)
            event_id=row.operation_id+':cash'
            if await db.get(WealthSettlementORM,event_id):raise BankError('历史到账状态需要核对','BANK_LEDGER_INVALID',503)
            account=await db.get(AccountORM,row.account_id,populate_existing=True)
            version=await db.get(WealthProductVersionORM,row.version_id)
            account.balance_fen+=cash
            if cash:db.add(TransactionORM(tx_id=stable_id('WRT',row.operation_id),operation_id=row.operation_id,
                account_id=row.account_id,type='investment_redemption',amount_fen=cash,counterparty=version.name,
                category='investment',note='取消等待流程：历史已计价赎回应收即时到账',timestamp=utc_now(),status='completed',channel='app'))
            db.add(WealthSettlementORM(event_id=event_id,operation_id=row.operation_id,kind='cash',
                data={'cash_fen':cash,'policy':POLICY},created_at=utc_now()))
            row.status='SETTLED';result['receivables_paid_fen']+=cash
        else:
            if row.details.get('actual'):raise BankError('历史已计价记录需要先核对','BANK_LEDGER_INVALID',503)
            holds=list(await db.scalars(select(WealthCashHoldORM).where(WealthCashHoldORM.operation_id==row.operation_id,WealthCashHoldORM.status=='HELD')))
            result['released_fen']+=sum(h.amount_fen for h in holds)
            # Held money was never debited: releasing it must not add本金 twice.
            await db.execute(update(WealthCashHoldORM).where(WealthCashHoldORM.operation_id==row.operation_id,WealthCashHoldORM.status=='HELD').values(status='RELEASED'))
            await db.execute(update(WealthUnitHoldORM).where(WealthUnitHoldORM.operation_id==row.operation_id,WealthUnitHoldORM.status=='HELD').values(status='RELEASED'))
            row.status='CANCELLED';result['cancelled']+=1
        row.details={**row.details,'waiting_reason':None,'retirement_policy':POLICY,'retired_at':utc_now().isoformat()}
        record_bank_event(db,event_key=row.operation_id,subject_user_id=row.user_id,action='investment_retire_deferred',
            result=row.status.lower(),facts={'operation_id':row.operation_id,'policy':POLICY},
            decision_chain=['account_locked','legacy_facts_checked','no_future_nav','holds_released_or_receivable_paid_once'])
    await db.flush()
    return result
