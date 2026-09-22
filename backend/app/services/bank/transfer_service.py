"""4号模拟银行：按 operation_id 幂等受理、记账和查询。"""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain import BankOperationStatus, fen_to_yuan
from ...models.account import AccountORM
from ...models.bank_operation import BankOperationORM
from ...models.payment_request import PaymentRequestORM
from ...models.payment_request_payment import PaymentRequestPaymentORM
from ...models.transaction import TransactionORM
from ...models.user import UserORM


class BankError(Exception):
    def __init__(self, message: str, code: str = "BANK_REJECTED", status_code: int = 400) -> None:
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class BankResponseLost(Exception):
    """仅用于故障注入：银行已提交，但调用方没有收到响应。"""


@dataclass(frozen=True)
class TrustedExecutionContext:
    subject_user_id: str
    authorization_id: str
    control_epoch: int


@dataclass(frozen=True)
class BankTransferCommand:
    operation_id: str
    from_account: str
    to_account: str
    amount_fen: int
    currency: str
    param_digest: str
    note: str | None = None


@dataclass(frozen=True)
class BankOperationResult:
    operation_id: str
    status: str
    bank_tx_id: str | None
    error_code: str | None = None


@dataclass(frozen=True)
class TransferValidationResult:
    from_account: str
    to_account: str
    recipient_name: str
    daily_transfer_used_fen: int


@dataclass(frozen=True)
class ResolvedTransferPartiesResult(TransferValidationResult):
    recipient_user_id: str


def _result(operation: BankOperationORM) -> BankOperationResult:
    return BankOperationResult(
        operation_id=operation.operation_id,
        status=operation.status,
        bank_tx_id=operation.bank_tx_id,
        error_code=operation.error_code,
    )


async def validate_transfer_parties(
    db: AsyncSession,
    *,
    subject_user_id: str,
    from_account: str,
    to_account: str,
) -> TransferValidationResult:
    """校验付款/收款账户并返回编排所需的最小银行事实。"""
    rows = (
        await db.execute(
            select(AccountORM, UserORM)
            .join(UserORM, UserORM.user_id == AccountORM.user_id)
            .where(AccountORM.account_id.in_([from_account, to_account]))
        )
    ).all()
    accounts = {account.account_id: (account, user) for account, user in rows}
    sender_row = accounts.get(from_account)
    receiver_row = accounts.get(to_account)
    if sender_row is None or receiver_row is None:
        raise BankError("付款或收款账户不存在", "ACCOUNT_NOT_FOUND", 404)
    sender, sender_user = sender_row
    receiver, receiver_user = receiver_row
    if sender_user.user_id != subject_user_id:
        raise BankError("付款账户不属于当前用户", "PAYER_FORBIDDEN", 403)
    if sender.status != "active" or receiver.status != "active":
        raise BankError("账户当前不可用", "ACCOUNT_INACTIVE", 403)
    if from_account == to_account:
        raise BankError("付款账户和收款账户不能相同", "SAME_ACCOUNT", 400)
    return TransferValidationResult(
        from_account=sender.account_id,
        to_account=receiver.account_id,
        recipient_name=receiver_user.name,
        daily_transfer_used_fen=round(sender.daily_transfer_used * 100),
    )


async def resolve_transfer_parties(
    db: AsyncSession, *, subject_user_id: str, recipient_name: str
) -> ResolvedTransferPartiesResult:
    """按用户和收款人姓名解析默认账户；歧义由银行目录明确拒绝。"""
    sender = (
        await db.execute(
            select(AccountORM)
            .where(
                AccountORM.user_id == subject_user_id,
                AccountORM.status == "active",
            )
            .order_by(AccountORM.account_id)
        )
    ).scalars().first()
    if sender is None:
        raise BankError("未找到可用付款账户", "PAYER_ACCOUNT_NOT_FOUND", 404)

    recipients = (
        await db.execute(
            select(UserORM, AccountORM)
            .join(AccountORM, AccountORM.user_id == UserORM.user_id)
            .where(
                UserORM.name == recipient_name,
                UserORM.user_id != subject_user_id,
                UserORM.status == "active",
                AccountORM.status == "active",
            )
            .order_by(AccountORM.account_id)
        )
    ).all()
    unique_users = {user.user_id for user, _ in recipients}
    if len(unique_users) != 1:
        raise BankError("未找到唯一收款人", "RECIPIENT_AMBIGUOUS", 409)
    recipient_user_id = next(iter(unique_users))
    recipient_user, receiver = next(
        (user, account) for user, account in recipients if user.user_id == recipient_user_id
    )
    return ResolvedTransferPartiesResult(
        from_account=sender.account_id,
        to_account=receiver.account_id,
        recipient_name=recipient_user.name,
        recipient_user_id=recipient_user.user_id,
        daily_transfer_used_fen=round(sender.daily_transfer_used * 100),
    )


async def query_operation(db: AsyncSession, operation_id: str) -> BankOperationResult | None:
    """按应用操作编号查询银行权威状态；None 表示 NOT_FOUND。"""
    operation = await db.get(BankOperationORM, operation_id)
    return _result(operation) if operation else None


async def release_payment_request_payment(
    db: AsyncSession, *, operation_id: str, subject_user_id: str
) -> None:
    """转账任务在提交银行前取消时，释放其收款申请占用。"""
    link = (
        await db.execute(
            select(PaymentRequestPaymentORM)
            .where(PaymentRequestPaymentORM.operation_id == operation_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if link is None:
        return
    request = await db.get(
        PaymentRequestORM, link.payment_request_id, with_for_update=True
    )
    if request is None or request.payer_user_id != subject_user_id:
        raise BankError("无权释放该收款申请", "PAYMENT_REQUEST_FORBIDDEN", 403)
    if request.status == "ACCEPTED":
        request.status = "PENDING"
        request.state_version += 1
        request.responded_at = None
        await db.delete(link)
        await db.commit()


async def submit_transfer(
    db: AsyncSession,
    command: BankTransferCommand,
    context: TrustedExecutionContext,
    *,
    simulate_response_loss: bool = False,
) -> BankOperationResult:
    """幂等执行内部转账；银行账务更新与双边流水在一个事务中提交。"""
    existing = await db.get(BankOperationORM, command.operation_id)
    if existing:
        if existing.param_digest != command.param_digest:
            raise BankError(
                "同一操作编号不能用于不同的银行参数",
                "IDEMPOTENCY_CONFLICT",
                409,
            )
        return _result(existing)

    payment_link = (
        await db.execute(
            select(PaymentRequestPaymentORM).where(
                PaymentRequestPaymentORM.operation_id == command.operation_id
            )
        )
    ).scalar_one_or_none()
    payment_request = None
    if payment_link is not None:
        payment_request = await db.get(
            PaymentRequestORM, payment_link.payment_request_id, with_for_update=True
        )
        if payment_request is None or payment_request.status != "ACCEPTED":
            raise BankError("收款申请当前不能付款", "PAYMENT_REQUEST_NOT_PAYABLE", 409)
        if (
            payment_link.payer_account_id != command.from_account
            or payment_request.receive_account_id != command.to_account
            or payment_request.amount_fen != command.amount_fen
            or payment_request.payer_user_id != context.subject_user_id
        ):
            raise BankError("转账参数与收款申请不一致", "PAYMENT_REQUEST_MISMATCH", 409)

    accounts = {
        account.account_id: account
        for account in (
            await db.execute(
                select(AccountORM)
                .where(AccountORM.account_id.in_([command.from_account, command.to_account]))
                .with_for_update()
            )
        )
        .scalars()
        .all()
    }
    sender = accounts.get(command.from_account)
    receiver = accounts.get(command.to_account)
    if sender is None or receiver is None:
        raise BankError("付款或收款账户不存在", "ACCOUNT_NOT_FOUND", 404)
    if sender.user_id != context.subject_user_id:
        raise BankError("可信执行上下文无权使用该付款账户", "PAYER_FORBIDDEN", 403)
    if sender.status != "active" or receiver.status != "active":
        raise BankError("账户当前不可用", "ACCOUNT_INACTIVE", 403)

    amount = fen_to_yuan(command.amount_fen)
    error: tuple[str, str] | None = None
    if command.currency != "CNY" or command.amount_fen <= 0:
        error = ("INVALID_AMOUNT", "银行不支持该币种或金额")
    elif round(sender.balance * 100) < command.amount_fen:
        error = ("INSUFFICIENT_FUNDS", "余额不足")
    elif round((sender.daily_transfer_used + amount) * 100) > round(
        sender.daily_transfer_limit * 100
    ):
        error = ("DAILY_LIMIT_EXCEEDED", "超过付款账户日转账限额")

    # 锁账户后再次检查，防止并发请求都在加锁前看不到银行操作。
    concurrent = await db.get(BankOperationORM, command.operation_id)
    if concurrent is not None:
        if concurrent.param_digest != command.param_digest:
            await db.rollback()
            raise BankError("幂等参数冲突", "IDEMPOTENCY_CONFLICT", 409)
        await db.rollback()
        return _result(concurrent)

    bank_operation = BankOperationORM(
        operation_id=command.operation_id,
        status=BankOperationStatus.FAILED if error else BankOperationStatus.ACCEPTED,
        param_digest=command.param_digest,
        from_account=command.from_account,
        to_account=command.to_account,
        amount_fen=command.amount_fen,
        currency=command.currency,
        error_code=error[0] if error else None,
    )
    db.add(bank_operation)
    if error:
        if payment_request is not None and payment_link is not None:
            payment_request.status = "PENDING"
            payment_request.state_version += 1
            payment_request.responded_at = None
            await db.delete(payment_link)
        await db.commit()
        raise BankError(error[1], error[0])

    now = datetime.now(timezone.utc)
    bank_tx_id = f"BTX{uuid4().hex[:18].upper()}"
    credit_tx_id = f"TX{uuid4().hex[:18].upper()}"
    sender.balance -= amount
    sender.daily_transfer_used += amount
    receiver.balance += amount
    bank_operation.status = BankOperationStatus.SUCCEEDED
    bank_operation.bank_tx_id = bank_tx_id
    if payment_request is not None:
        payment_request.status = "PAID"
        payment_request.state_version += 1
        payment_request.responded_at = now
    db.add_all(
        [
            TransactionORM(
                tx_id=bank_tx_id,
                operation_id=command.operation_id,
                account_id=sender.account_id,
                type="transfer_out",
                amount=amount,
                counterparty=receiver.account_id,
                category="transfer",
                note=command.note,
                timestamp=now,
                status="completed",
                channel="app",
            ),
            TransactionORM(
                tx_id=credit_tx_id,
                operation_id=None,
                account_id=receiver.account_id,
                type="transfer_in",
                amount=amount,
                counterparty=sender.account_id,
                category="transfer",
                note=command.note,
                timestamp=now,
                status="completed",
                channel="app",
            ),
        ]
    )
    await db.commit()
    await db.refresh(bank_operation)
    if simulate_response_loss:
        raise BankResponseLost("银行已记账，但响应在传输中丢失")
    return _result(bank_operation)


async def transfer_directory(db: AsyncSession, *, subject_user_id: str, recipient_name: str) -> dict:
    """4号受控目录。沿用账户ID排序的默认付款规则；不返回余额/完整卡号。"""
    payers = (await db.execute(select(AccountORM).where(
        AccountORM.user_id == subject_user_id, AccountORM.status == "active"
    ).order_by(AccountORM.account_id))).scalars().all()
    rows = (await db.execute(select(UserORM, AccountORM).join(
        AccountORM, AccountORM.user_id == UserORM.user_id
    ).where(UserORM.name == recipient_name, UserORM.user_id != subject_user_id,
            UserORM.status == "active", AccountORM.status == "active")
        .order_by(UserORM.user_id, AccountORM.account_id))).all()
    return {
        "payers": [{"account_id": a.account_id, "label": f"{'活期 savings' if a.account_type == 'savings' else a.account_type} · 尾号{a.card_number[-4:]}"} for a in payers],
        "default_payer": payers[0].account_id if payers else None,
        "recipients": [{"account_id": a.account_id, "label": f"{u.name} · 手机尾号{u.phone[-4:]} · 账户尾号{a.card_number[-4:]}"} for u, a in rows],
    }
