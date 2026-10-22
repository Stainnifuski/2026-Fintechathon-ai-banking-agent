"""Bank-owned full and partial merchant refunds with cumulative caps and replay safety."""
from ..audit.spans import audited
from datetime import datetime,timezone
from uuid import uuid4
from sqlalchemy import select,func,text
from sqlalchemy.exc import IntegrityError
from ...database.connection import AsyncSessionLocal
from ...domain.money import ledger_fen
from ...models.account import AccountORM
from ...models.merchant_refund import MerchantRefundORM
from ...models.bill_features import MerchantRefundPartORM as Part
from ...models.payment_request import PaymentRequestORM
from ...models.payment_request_payment import PaymentRequestPaymentORM
from ...models.transaction import TransactionORM
from ...models.user import UserORM
from .audit_service import record_bank_event
from .available_funds import available_cash
from .transfer_service import BankError,require_ordinary_accounts


def _view(row):
    return dict(payment_request_id=row.payment_request_id,operation_id=row.operation_id,amount_fen=row.amount_fen,amount=row.amount_fen/100,merchant_tx_id=row.merchant_tx_id,customer_tx_id=row.customer_tx_id,status=row.status,created_at=row.created_at)


async def refund_summaries(db,payment_ids):
    totals=dict((await db.execute(select(Part.payment_request_id,func.sum(Part.amount_fen)).where(Part.payment_request_id.in_(payment_ids),Part.status=='SUCCEEDED').group_by(Part.payment_request_id))).all())
    # Legacy full-refund markers and new final markers are not added twice.
    for row in (await db.scalars(select(MerchantRefundORM).where(MerchantRefundORM.payment_request_id.in_(payment_ids),MerchantRefundORM.status=='SUCCEEDED'))).all():totals.setdefault(row.payment_request_id,row.amount_fen)
    return {k:int(v) for k,v in totals.items()}


@audited("payments")
async def refund_merchant_payment(*,payment_request_id,merchant_user_id,operation_id,amount_fen=None,expected_refunded_fen=None):
    if not operation_id or len(operation_id)>64:raise BankError('退款操作号无效','INVALID_OPERATION_ID',422)
    if amount_fen is not None and (type(amount_fen) is not int or amount_fen<=0):raise BankError('退款金额必须为正整数分','INVALID_AMOUNT',422)
    async with AsyncSessionLocal() as session:
        if session.get_bind().dialect.name=='sqlite':await session.execute(text('BEGIN IMMEDIATE'))
        payment=await session.get(PaymentRequestORM,payment_request_id,with_for_update=True)
        if payment is None:raise BankError('付款申请不存在','PAYMENT_REQUEST_NOT_FOUND',404)
        if payment.requester_user_id!=merchant_user_id:raise BankError('只有原收款商户可以退款','MERCHANT_REFUND_FORBIDDEN',403)
        existing=await session.get(Part,operation_id)
        if existing:
            if existing.payment_request_id!=payment_request_id or (amount_fen is not None and existing.amount_fen!=amount_fen):raise BankError('操作号已用于其他退款参数','IDEMPOTENCY_CONFLICT',409)
            cumulative=(await refund_summaries(session,[payment_request_id])).get(payment_request_id,0)
            return {**_view(existing),"cumulative_refunded_fen":cumulative,"remaining_fen":max(0,payment.amount_fen-cumulative)}
        legacy=await session.get(MerchantRefundORM,payment_request_id)
        if legacy:
            if legacy.operation_id!=operation_id:raise BankError('该消费已经全额退款','PAYMENT_ALREADY_REFUNDED',409)
            if amount_fen is not None and legacy.amount_fen!=amount_fen:raise BankError('操作号参数不一致','IDEMPOTENCY_CONFLICT',409)
            return {**_view(legacy),"cumulative_refunded_fen":payment.amount_fen,"remaining_fen":0}
        if payment.status!='PAID':raise BankError('只有已付款订单可以退款','PAYMENT_NOT_PAID',409)
        link=await session.get(PaymentRequestPaymentORM,payment_request_id)
        if link is None:raise BankError('付款账户关联不存在','PAYMENT_LINK_NOT_FOUND',409)
        refunded=(await refund_summaries(session,[payment_request_id])).get(payment_request_id,0)
        if expected_refunded_fen is not None and expected_refunded_fen!=refunded:raise BankError('累计退款额已变化，请刷新后确认','REFUND_CHANGED',409)
        amount=payment.amount_fen-refunded if amount_fen is None else amount_fen
        if not 0<amount<=payment.amount_fen-refunded:raise BankError('退款金额超过剩余可退金额','REFUND_EXCEEDS_REMAINING',409)
        await require_ordinary_accounts(session,[payment.receive_account_id,link.payer_account_id])
        accounts=(await session.scalars(select(AccountORM).where(AccountORM.account_id.in_([payment.receive_account_id,link.payer_account_id])).order_by(AccountORM.account_id).with_for_update().execution_options(populate_existing=True))).all()
        by_id={a.account_id:a for a in accounts};merchant=by_id.get(payment.receive_account_id);customer=by_id.get(link.payer_account_id)
        if not merchant or not customer:raise BankError('退款账户不存在','ACCOUNT_NOT_FOUND',404)
        if merchant.user_id!=merchant_user_id:raise BankError('商户账户归属异常','MERCHANT_ACCOUNT_MISMATCH',409)
        if merchant.status!='active' or customer.status!='active':raise BankError('退款账户不可用','ACCOUNT_INACTIVE',403)
        balance=ledger_fen(merchant.balance_fen)
        if await available_cash(session,merchant.account_id,balance)<amount:raise BankError('商户可用余额不足，无法退款','INSUFFICIENT_FUNDS',409)
        users={u.user_id:u.name for u in (await session.scalars(select(UserORM).where(UserORM.user_id.in_([merchant.user_id,customer.user_id])))).all()}
        debit_id='MRO'+uuid4().hex[:18].upper();credit_id='RFD'+uuid4().hex[:18].upper();now=datetime.now(timezone.utc)
        merchant.balance_fen=balance-amount;customer.balance_fen=ledger_fen(customer.balance_fen)+amount
        for tx_id,account,kind,party,op_id in [(debit_id,merchant,'refund_out',users[customer.user_id],operation_id),(credit_id,customer,'refund',users[merchant.user_id],None)]:
            session.add(TransactionORM(tx_id=tx_id,operation_id=op_id,account_id=account.account_id,type=kind,amount_fen=amount,counterparty=party,category='shopping',note=f'商户退款 · {payment.note or payment_request_id}'[:256],timestamp=now,status='completed',channel='app'))
        await session.flush()
        row=Part(operation_id=operation_id,payment_request_id=payment_request_id,merchant_account_id=merchant.account_id,customer_account_id=customer.account_id,amount_fen=amount,merchant_tx_id=debit_id,customer_tx_id=credit_id,status='SUCCEEDED');session.add(row)
        if refunded+amount==payment.amount_fen:
            session.add(MerchantRefundORM(payment_request_id=payment_request_id,operation_id=operation_id,merchant_account_id=merchant.account_id,customer_account_id=customer.account_id,amount_fen=payment.amount_fen,merchant_tx_id=debit_id,customer_tx_id=credit_id,status='SUCCEEDED'))
        record_bank_event(session,event_key=operation_id,subject_user_id=merchant_user_id,action='merchant_refund',result='success',facts={'payment_request_id':payment_request_id,'amount_fen':amount,'cumulative_refunded_fen':refunded+amount,'merchant_tx_id':debit_id,'customer_tx_id':credit_id},decision_chain=['merchant_ownership_checked','payment_locked','remaining_refund_checked','available_balance_checked','double_entry_refund_committed'])
        try:
            from .bill_foundation import sync_users
            await sync_users(session,[customer.user_id,merchant.user_id],now=now,changed_tx_ids=[debit_id,credit_id])
            await session.commit()
        except IntegrityError as error:
            await session.rollback();raise BankError('退款操作号冲突，请按原操作号核对','REFUND_CONFLICT',409) from error
        await session.refresh(row)
        return {**_view(row),'cumulative_refunded_fen':refunded+amount,'remaining_fen':payment.amount_fen-refunded-amount}
