"""4号模拟银行：按 operation_id 幂等受理、记账和查询。"""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain import BankOperationStatus, fen_to_yuan
from ...models.account import AccountORM
from ...models.transaction import TransactionORM
from ...models.workflow import BankOperationORM


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


def _result(operation: BankOperationORM) -> BankOperationResult:
    return BankOperationResult(
        operation_id=operation.operation_id,
        status=operation.status,
        bank_tx_id=operation.bank_tx_id,
        error_code=operation.error_code,
    )


async def query_operation(db: AsyncSession, operation_id: str) -> BankOperationResult | None:
    """按应用操作编号查询银行权威状态；None 表示 NOT_FOUND。"""
    operation = await db.get(BankOperationORM, operation_id)
    return _result(operation) if operation else None


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
