"""4号模拟银行：商户对已付款收款申请执行全额退款。"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ...database.connection import AsyncSessionLocal
from ...domain.money import ledger_fen
from ...models.account import AccountORM
from ...models.merchant_refund import MerchantRefundORM
from ...models.payment_request import PaymentRequestORM
from ...models.payment_request_payment import PaymentRequestPaymentORM
from ...models.transaction import TransactionORM
from ...models.user import UserORM
from .audit_service import record_bank_event
from .available_funds import available_cash
from .transfer_service import BankError, require_ordinary_accounts


def _view(refund: MerchantRefundORM) -> dict:
    return {
        "payment_request_id": refund.payment_request_id,
        "operation_id": refund.operation_id,
        "amount_fen": refund.amount_fen,
        "amount": refund.amount_fen / 100,
        "merchant_tx_id": refund.merchant_tx_id,
        "customer_tx_id": refund.customer_tx_id,
        "status": refund.status,
        "created_at": refund.created_at,
    }


async def refund_merchant_payment(
    *,
    payment_request_id: str,
    merchant_user_id: str,
    operation_id: str,
) -> dict:
    """原路退回整笔商户消费；以付款申请和操作号双重幂等。"""
    async with AsyncSessionLocal() as session:
        existing = await session.get(MerchantRefundORM, payment_request_id)
        if existing is not None:
            if existing.operation_id != operation_id:
                raise BankError("该消费已经退款", "PAYMENT_ALREADY_REFUNDED", 409)
            return _view(existing)

        payment = await session.get(
            PaymentRequestORM,
            payment_request_id,
            with_for_update=True,
        )
        if payment is None:
            raise BankError("付款申请不存在", "PAYMENT_REQUEST_NOT_FOUND", 404)
        if payment.requester_user_id != merchant_user_id:
            raise BankError("只有原收款商户可以退款", "MERCHANT_REFUND_FORBIDDEN", 403)
        if payment.status != "PAID":
            raise BankError("只有已付款订单可以退款", "PAYMENT_NOT_PAID", 409)
        link = await session.get(PaymentRequestPaymentORM, payment_request_id)
        if link is None:
            raise BankError("付款账户关联不存在", "PAYMENT_LINK_NOT_FOUND", 409)

        await require_ordinary_accounts(
            session,
            [payment.receive_account_id, link.payer_account_id],
        )
        if session.get_bind().dialect.name != "sqlite":
            await session.execute(
                select(AccountORM.account_id)
                .where(
                    AccountORM.account_id.in_(
                        [payment.receive_account_id, link.payer_account_id]
                    )
                )
                .order_by(AccountORM.account_id)
                .with_for_update()
            )
        merchant_account = await session.get(AccountORM, payment.receive_account_id)
        customer_account = await session.get(AccountORM, link.payer_account_id)
        if merchant_account is None or customer_account is None:
            raise BankError("退款账户不存在", "ACCOUNT_NOT_FOUND", 404)
        if merchant_account.user_id != merchant_user_id:
            raise BankError("商户收款账户归属异常", "MERCHANT_ACCOUNT_MISMATCH", 409)
        if (
            merchant_account.status != "active"
            or customer_account.status != "active"
        ):
            raise BankError("退款账户当前不可用", "ACCOUNT_INACTIVE", 403)

        users = {
            user.user_id: user
            for user in (
                await session.execute(
                    select(UserORM).where(
                        UserORM.user_id.in_(
                            [merchant_account.user_id, customer_account.user_id]
                        )
                    )
                )
            ).scalars()
        }
        merchant = users[merchant_account.user_id]
        customer = users[customer_account.user_id]
        merchant_balance = ledger_fen(merchant_account.balance_fen)
        customer_balance = ledger_fen(customer_account.balance_fen)
        if (
            await available_cash(
                session,
                merchant_account.account_id,
                merchant_balance,
            )
            < payment.amount_fen
        ):
            raise BankError("商户可用余额不足，无法退款", "INSUFFICIENT_FUNDS", 409)

        merchant_tx_id = f"MRO{uuid4().hex[:18].upper()}"
        customer_tx_id = f"RFD{uuid4().hex[:18].upper()}"
        now = datetime.now(timezone.utc)
        merchant_account.balance_fen = merchant_balance - payment.amount_fen
        customer_account.balance_fen = customer_balance + payment.amount_fen
        refund = MerchantRefundORM(
            payment_request_id=payment_request_id,
            operation_id=operation_id,
            merchant_account_id=merchant_account.account_id,
            customer_account_id=customer_account.account_id,
            amount_fen=payment.amount_fen,
            merchant_tx_id=merchant_tx_id,
            customer_tx_id=customer_tx_id,
            status="SUCCEEDED",
        )
        session.add(refund)
        session.add_all(
            [
                TransactionORM(
                    tx_id=merchant_tx_id,
                    operation_id=operation_id,
                    account_id=merchant_account.account_id,
                    type="refund_out",
                    amount_fen=payment.amount_fen,
                    counterparty=customer.name,
                    category="shopping",
                    note=f"商户订单退款 · {payment.note or payment_request_id}",
                    timestamp=now,
                    status="completed",
                    channel="app",
                ),
                TransactionORM(
                    tx_id=customer_tx_id,
                    operation_id=None,
                    account_id=customer_account.account_id,
                    type="refund",
                    amount_fen=payment.amount_fen,
                    counterparty=merchant.name,
                    category="shopping",
                    note=f"商户订单退款 · {payment.note or payment_request_id}",
                    timestamp=now,
                    status="completed",
                    channel="app",
                ),
            ]
        )
        record_bank_event(
            session,
            event_key=operation_id,
            subject_user_id=merchant_user_id,
            action="merchant_refund",
            result="success",
            facts={
                "payment_request_id": payment_request_id,
                "amount_fen": payment.amount_fen,
                "merchant_tx_id": merchant_tx_id,
                "customer_tx_id": customer_tx_id,
            },
            decision_chain=[
                "merchant_ownership_checked",
                "paid_payment_checked",
                "available_balance_checked",
                "double_entry_refund_committed",
            ],
        )
        try:
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            existing = await session.get(MerchantRefundORM, payment_request_id)
            if existing is not None and existing.operation_id == operation_id:
                return _view(existing)
            raise BankError("退款操作冲突", "REFUND_CONFLICT", 409) from error
        await session.refresh(refund)
        return _view(refund)
