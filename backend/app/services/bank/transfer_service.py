"""4号模拟银行：按 operation_id 幂等受理、记账和查询。"""
from ..audit.spans import audited
from .account_scope import isolated_account_ids


from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
import os
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError, OperationalError

from ...domain import BankOperationStatus, fen_to_yuan
from ...models.account import AccountORM
from ...models.wealth_research import WealthContextORM
from ...models.bank_operation import BankOperationORM
from ...models.payment_request import PaymentRequestORM
from ...models.payment_request_payment import PaymentRequestPaymentORM
from ...models.transaction import TransactionORM
from ...models.user import UserORM
from .phone import normalize_phone, mask_phone
from .transfer_cards import transfer_card_number
from ...domain.money import ledger_fen
from .audit_service import record_bank_event


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
    authorization_issued_at: str | None = None


@dataclass(frozen=True)
class BankTransferCommand:
    operation_id: str
    from_account: str
    to_account: str
    amount_fen: int
    currency: str
    param_digest: str
    note: str | None = None
    recipient_phone: str | None = None


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
    payer_card_masked: str | None = field(default=None, kw_only=True)
    recipient_card_masked: str | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class ResolvedTransferPartiesResult(TransferValidationResult):
    recipient_user_id: str


def _digest(command: BankTransferCommand) -> str:
    payload = {
        "from_account": command.from_account,
        "to_account": command.to_account,
        "amount_fen": command.amount_fen,
        "currency": command.currency,
        "note": command.note or "",
    }
    if command.recipient_phone is not None:
        payload["recipient_phone"] = normalize_phone(command.recipient_phone)
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _business_day_start(now: datetime) -> datetime:
    try:
        zone = ZoneInfo(os.getenv("BANK_BUSINESS_TIMEZONE", "Asia/Shanghai"))
    except (KeyError, ValueError) as error:
        raise BankError("银行业务时区配置无效", "BANK_TIMEZONE_INVALID", 503) from error
    local = now.astimezone(zone)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


async def _daily_used_fen(
    db: AsyncSession, *, account_ids: list[str], now: datetime
) -> int:
    if not account_ids:
        return 0
    start = _business_day_start(now)
    rows = (await db.execute(
        select(TransactionORM.amount_fen).where(
            TransactionORM.account_id.in_(account_ids),
            TransactionORM.type.in_(("transfer_out", "purchase")),
            TransactionORM.status == "completed",
            TransactionORM.timestamp >= start,
        )
    )).scalars().all()
    try:
        return sum(ledger_fen(value) for value in rows)
    except ValueError as error:
        raise BankError("历史流水金额异常，已阻止转账", "BANK_LEDGER_INVALID", 503) from error


def _validate_command(command: BankTransferCommand, context: TrustedExecutionContext) -> None:
    if type(command.amount_fen) is not int or not 0 < command.amount_fen <= 2_000_000_000:
        raise BankError("银行不支持该金额", "INVALID_AMOUNT", 422)
    if command.currency != "CNY":
        raise BankError("银行不支持该币种", "INVALID_CURRENCY", 422)
    if command.from_account == command.to_account:
        raise BankError("付款账户和收款账户不能相同", "SAME_ACCOUNT", 400)
    if (not context.subject_user_id or not context.authorization_id
            or type(context.control_epoch) is not int or context.control_epoch < 1):
        raise BankError("可信执行上下文无效", "EXECUTION_CONTEXT_INVALID", 403)
    if command.note is not None:
        if not command.note.strip() or len(command.note) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in command.note):
            raise BankError("备注超过256字或含控制字符", "INVALID_NOTE", 422)
    try:
        actual_digest = _digest(command)
    except ValueError as error:
        raise BankError(str(error), "INVALID_PHONE", 422) from error
    if command.param_digest != actual_digest:
        raise BankError("银行参数摘要与实际命令不一致", "PARAM_DIGEST_MISMATCH", 409)


def _result(operation: BankOperationORM) -> BankOperationResult:
    return BankOperationResult(
        operation_id=operation.operation_id,
        status=operation.status,
        bank_tx_id=operation.bank_tx_id,
        error_code=operation.error_code,
    )


async def require_ordinary_accounts(db: AsyncSession,account_ids: list[str]) -> None:
    """Investment sandboxes cannot export issued fixture cash into ordinary transfers."""
    from ...models.wealth_research import WealthContextORM
    if await db.scalar(isolated_account_ids().where(WealthContextORM.account_id.in_(account_ids))) is not None:
        raise BankError('理财沙箱资金不可用于普通转账或AA','ACCOUNT_CONTEXT_UNSUPPORTED',409)


@audited("transfer")
async def validate_transfer_parties(
    db: AsyncSession,
    *,
    subject_user_id: str,
    from_account: str,
    to_account: str,
    recipient_phone: str | None = None,
) -> TransferValidationResult:
    """校验付款/收款账户并返回编排所需的最小银行事实。"""
    await require_ordinary_accounts(db,[from_account,to_account])
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
    if sender_user.status != "active" or receiver_user.status != "active":
        raise BankError("客户当前不可用", "CUSTOMER_INACTIVE", 403)
    if recipient_phone is not None:
        try:
            mapped = normalize_phone(recipient_phone) == normalize_phone(receiver_user.phone)
        except ValueError as error:
            raise BankError(str(error), "INVALID_PHONE", 422) from error
        if not mapped:
            raise BankError("手机号映射已变化，请重新确认", "RECIPIENT_MAPPING_CHANGED", 409)
    if sender.status != "active" or receiver.status != "active":
        raise BankError("账户当前不可用", "ACCOUNT_INACTIVE", 403)
    if from_account == to_account:
        raise BankError("付款账户和收款账户不能相同", "SAME_ACCOUNT", 400)
    return TransferValidationResult(
        from_account=sender.account_id,
        to_account=receiver.account_id,
        recipient_name=receiver_user.name,
        daily_transfer_used_fen=await _daily_used_fen(
            db,
            account_ids=(await db.execute(select(AccountORM.account_id).where(
                AccountORM.user_id == subject_user_id
            ))).scalars().all(),
            now=datetime.now(timezone.utc),
        ),
        payer_card_masked=f"**** **** **** {transfer_card_number(sender.account_id)[-4:]}",
        recipient_card_masked=f"**** **** **** {transfer_card_number(receiver.account_id)[-4:]}",
    )


@audited("transfer")
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
                AccountORM.account_id.not_in(isolated_account_ids()),
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
                AccountORM.account_id.not_in(isolated_account_ids()),
            )
            .order_by(AccountORM.account_id)
        )
    ).all()
    unique_users = {user.user_id for user, _ in recipients}
    if len(unique_users) != 1 or len(recipients) != 1:
        raise BankError("未找到唯一收款账户", "RECIPIENT_AMBIGUOUS", 409)
    recipient_user_id = next(iter(unique_users))
    recipient_user, receiver = next(
        (user, account) for user, account in recipients if user.user_id == recipient_user_id
    )
    return ResolvedTransferPartiesResult(
        from_account=sender.account_id,
        to_account=receiver.account_id,
        recipient_name=recipient_user.name,
        recipient_user_id=recipient_user.user_id,
        daily_transfer_used_fen=await _daily_used_fen(
            db,
            account_ids=(await db.execute(select(AccountORM.account_id).where(
                AccountORM.user_id == subject_user_id
            ))).scalars().all(),
            now=datetime.now(timezone.utc),
        ),
        payer_card_masked=f"**** **** **** {transfer_card_number(sender.account_id)[-4:]}",
        recipient_card_masked=f"**** **** **** {transfer_card_number(receiver.account_id)[-4:]}",
    )


@audited("transfer")
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
        record_bank_event(db, event_key=operation_id, subject_user_id=subject_user_id,
            action="payment_request_release", result="cancelled",
            facts={"operation_id": operation_id, "payment_request_id": request.payment_request_id},
            decision_chain=["payer_ownership_checked", "unsubmitted_payment_released"])
        await db.commit()


@audited("transfer")
async def submit_transfer(
    db: AsyncSession,
    command: BankTransferCommand,
    context: TrustedExecutionContext,
    *,
    simulate_response_loss: bool = False,
) -> BankOperationResult:
    """银行端重算摘要，在数据库写锁下执行幂等检查与原子双边记账。"""
    _validate_command(command, context)
    try:
        # SQLite 的 with_for_update 无效；空更新先取得跨连接写锁。
        if db.get_bind().dialect.name == "sqlite":
            await db.execute(
                update(AccountORM)
                .where(AccountORM.account_id == min(command.from_account, command.to_account))
                .values(balance_fen=AccountORM.balance_fen)
            )
        else:
            await db.execute(
                select(AccountORM.account_id)
                .where(AccountORM.account_id.in_([command.from_account, command.to_account]))
                .order_by(AccountORM.account_id)
                .with_for_update()
            )

        rows = (await db.execute(select(AccountORM, UserORM).join(
            UserORM, UserORM.user_id == AccountORM.user_id
        ).where(AccountORM.account_id.in_([command.from_account, command.to_account])))).all()
        accounts = {account.account_id: (account, user) for account, user in rows}
        sender_row = accounts.get(command.from_account)
        receiver_row = accounts.get(command.to_account)
        if sender_row is None or receiver_row is None:
            raise BankError("付款或收款账户不存在", "ACCOUNT_NOT_FOUND", 404)
        sender, sender_user = sender_row
        receiver, receiver_user = receiver_row
        await require_ordinary_accounts(db,[command.from_account,command.to_account])
        if sender.user_id != context.subject_user_id:
            raise BankError("无权使用付款账户", "PAYER_FORBIDDEN", 403)
        if sender_user.status != "active" or receiver_user.status != "active":
            raise BankError("客户当前不可用", "CUSTOMER_INACTIVE", 403)
        if command.recipient_phone is not None:
            try:
                mapped = normalize_phone(command.recipient_phone) == normalize_phone(receiver_user.phone)
            except ValueError as error:
                raise BankError(str(error), "INVALID_PHONE", 422) from error
            if not mapped:
                raise BankError("手机号映射已变化，请重新确认", "RECIPIENT_MAPPING_CHANGED", 409)
        if sender.status != "active" or receiver.status != "active":
            raise BankError("账户当前不可用", "ACCOUNT_INACTIVE", 403)

        existing = await db.get(BankOperationORM, command.operation_id)
        if existing is not None:
            if (existing.param_digest != _digest(command)
                    or existing.from_account != command.from_account
                    or existing.to_account != command.to_account
                    or existing.amount_fen != command.amount_fen
                    or existing.currency != command.currency):
                raise BankError("幂等参数冲突", "IDEMPOTENCY_CONFLICT", 409)
            result = _result(existing)
            await db.rollback()
            return result

        from ...security.circuit_breaker import require_trade
        await require_trade(db, context.subject_user_id,
            issued_at=context.authorization_issued_at, authorization=True)

        payment_link = (await db.execute(
            select(PaymentRequestPaymentORM).where(
                PaymentRequestPaymentORM.operation_id == command.operation_id
            )
        )).scalar_one_or_none()
        payment_request = None
        if payment_link is not None:
            payment_request = await db.get(PaymentRequestORM, payment_link.payment_request_id)
            if payment_request is None or payment_request.status != "ACCEPTED":
                raise BankError("收款申请当前不能付款", "PAYMENT_REQUEST_NOT_PAYABLE", 409)
            if (
                payment_link.payer_account_id != command.from_account
                or payment_request.receive_account_id != command.to_account
                or payment_request.amount_fen != command.amount_fen
                or payment_request.payer_user_id != context.subject_user_id
            ):
                raise BankError("转账参数与收款申请不一致", "PAYMENT_REQUEST_MISMATCH", 409)

        now = datetime.now(timezone.utc)
        daily_used_fen = await _daily_used_fen(
            db, account_ids=[sender.account_id], now=now
        )
        try:
            balance_fen = ledger_fen(sender.balance_fen)
            receiver_balance_fen = ledger_fen(receiver.balance_fen)
            limit_fen = ledger_fen(sender.daily_transfer_limit_fen)
            ledger_fen(receiver_balance_fen + command.amount_fen)
        except ValueError as error:
            raise BankError("银行账户金额异常", "BANK_LEDGER_INVALID", 503) from error
        error: tuple[str, str] | None = None
        from .available_funds import available_cash
        from .wealth_reconciliation import require_reconciled
        await require_reconciled(db, sender.account_id)
        if await available_cash(db, sender.account_id, balance_fen) < command.amount_fen:
            error = ("INSUFFICIENT_FUNDS", "余额不足")
        elif daily_used_fen + command.amount_fen > limit_fen:
            error = ("DAILY_LIMIT_EXCEEDED", "超过付款账户日转账限额")

        bank_operation = BankOperationORM(
            operation_id=command.operation_id,
            status=BankOperationStatus.FAILED if error else BankOperationStatus.SUCCEEDED,
            param_digest=_digest(command),
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
            record_bank_event(db, event_key=command.operation_id,
                subject_user_id=context.subject_user_id, action="bank_transfer", result="failed",
                facts={"operation_id": command.operation_id, "error_code": error[0], "amount_fen": command.amount_fen},
                decision_chain=["trusted_executor", "ownership_and_status_checked", error[0]])
            await db.commit()
            raise BankError(error[1], error[0])

        bank_tx_id = f"BTX{uuid4().hex[:18].upper()}"
        credit_tx_id = f"TX{uuid4().hex[:18].upper()}"
        sender.balance_fen = balance_fen - command.amount_fen
        sender.daily_transfer_used_fen = daily_used_fen + command.amount_fen
        receiver.balance_fen = receiver_balance_fen + command.amount_fen
        bank_operation.bank_tx_id = bank_tx_id
        if payment_request is not None:
            payment_request.status = "PAID"
            payment_request.state_version += 1
            payment_request.responded_at = now
        is_merchant_purchase = (
            payment_request is not None
            and payment_request.requester_user_id in {"U007", "U008"}
            and receiver.user_id == payment_request.requester_user_id
        )
        db.add_all([
            TransactionORM(
                tx_id=bank_tx_id, operation_id=command.operation_id,
                account_id=sender.account_id,
                type="purchase" if is_merchant_purchase else "transfer_out",
                amount_fen=command.amount_fen,
                counterparty=receiver_user.name if is_merchant_purchase else receiver.account_id,
                category="shopping" if is_merchant_purchase else "transfer",
                note=command.note, timestamp=now, status="completed", channel="app",
            ),
            TransactionORM(
                tx_id=credit_tx_id, operation_id=None,
                account_id=receiver.account_id,
                type="merchant_sale" if is_merchant_purchase else "transfer_in",
                amount_fen=command.amount_fen,
                counterparty=sender_user.name if is_merchant_purchase else sender.account_id,
                category="shopping" if is_merchant_purchase else "transfer",
                note=command.note, timestamp=now, status="completed", channel="app",
            ),
        ])
        record_bank_event(db, event_key=command.operation_id,
            subject_user_id=context.subject_user_id, action="bank_transfer", result="success",
            facts={"operation_id": command.operation_id, "bank_tx_id": bank_tx_id,
                   "amount_fen": command.amount_fen, "currency": command.currency,
                   "param_digest": command.param_digest, "authorization_id": context.authorization_id,
                   "control_epoch": context.control_epoch},
            decision_chain=["trusted_executor", "ownership_and_status_checked", "digest_checked",
                            "balance_and_daily_limit_checked", "double_entry_committed"])
        from .bill_foundation import sync_users
        await sync_users(db, [sender.user_id, receiver.user_id], now=now, exact_pair=(bank_tx_id, credit_tx_id), changed_tx_ids=[bank_tx_id,credit_tx_id])
        await db.commit()
        if simulate_response_loss:
            raise BankResponseLost("银行已记账，但响应在传输中丢失")
        return _result(bank_operation)
    except BankError:
        await db.rollback()
        raise
    except IntegrityError as error:
        await db.rollback()
        raise BankError("银行操作编号或流水冲突", "BANK_INTEGRITY_CONFLICT", 409) from error
    except OperationalError as error:
        await db.rollback()
        raise BankError("银行账本繁忙，请按原操作编号核对", "BANK_BUSY", 503) from error


async def transfer_directory(
    db: AsyncSession,
    *,
    subject_user_id: str,
    recipient_name: str | None = None,
    recipient_phone: str | None = None,
    contacts_only: bool = False,
) -> dict:
    """受控目录：真实姓名、本人保存的备注名或规范化手机号精确查询。"""
    if not recipient_name and not recipient_phone:
        raise BankError("请提供收款人姓名或手机号", "RECIPIENT_REQUIRED", 422)
    normalized_phone = None
    if recipient_phone is not None:
        try:
            normalized_phone = normalize_phone(recipient_phone)
        except ValueError as error:
            raise BankError(str(error), "INVALID_PHONE", 422) from error
    subject = await db.get(UserORM, subject_user_id)
    if subject is None or subject.status != "active":
        raise BankError("付款用户不可用", "PAYER_INACTIVE", 403)
    payers = (await db.execute(select(AccountORM).where(
        AccountORM.user_id == subject_user_id, AccountORM.status == "active",AccountORM.account_id.not_in(isolated_account_ids())
    ).order_by(AccountORM.account_id))).scalars().all()

    users = (await db.execute(select(UserORM).where(
        UserORM.user_id != subject_user_id
    ))).scalars().all()
    if normalized_phone is not None:
        matches = []
        for user in users:
            try:
                if normalize_phone(user.phone) == normalized_phone:
                    matches.append(user)
            except ValueError:
                continue
        if len(matches) > 1:
            raise BankError("手机号映射冲突，请联系管理员", "PHONE_CONFLICT", 409)
        if recipient_name and matches and matches[0].name != recipient_name:
            from ...models.contact import ContactORM
            alias = await db.scalar(select(ContactORM.contact_id).where(
                ContactORM.user_id == subject_user_id,
                ContactORM.nickname == recipient_name,
                ContactORM.name == matches[0].name,
                ContactORM.phone == normalized_phone,
            ))
            if alias is None:
                raise BankError("姓名与手机号不指向同一收款人", "RECIPIENT_MISMATCH", 409)
        users = matches
    else:
        from ...models.contact import ContactORM
        contacts = (await db.scalars(select(ContactORM).where(
            ContactORM.user_id == subject_user_id,
            (ContactORM.name == recipient_name) | (ContactORM.nickname == recipient_name),
        ))).all()
        saved_matches = {(contact.phone, contact.name) for contact in contacts}
        matching_users = []
        for user in users:
            try:
                canonical_phone = normalize_phone(user.phone)
            except ValueError:
                continue
            if ((not contacts_only and user.name == recipient_name)
                    or (canonical_phone, user.name) in saved_matches):
                matching_users.append(user)
        users = matching_users
    users = [user for user in users if user.status == "active"]
    user_ids = [user.user_id for user in users]
    accounts = (await db.execute(select(AccountORM).where(
        AccountORM.user_id.in_(user_ids), AccountORM.status == "active",AccountORM.account_id.not_in(isolated_account_ids())
    ).order_by(AccountORM.user_id, AccountORM.account_id))).scalars().all() if user_ids else []
    by_id = {user.user_id: user for user in users}
    return {
        "payers": [{"account_id": account.account_id,
                    "label": f"{account.account_type} · 尾号{account.card_number[-4:]}"}
                   for account in payers],
        "default_payer": payers[0].account_id if payers else None,
        "recipients": [
            {"account_id": account.account_id,
             "recipient_user_id": account.user_id,
             "recipient_name": by_id[account.user_id].name,
             "phone_masked": mask_phone(by_id[account.user_id].phone) if recipient_phone else None,
             "label": f"{by_id[account.user_id].name} · {account.account_type}账户"}
            for account in accounts
        ],
    }


async def transfer_funding_accounts(db: AsyncSession, *, subject_user_id: str) -> dict:
    """Bank-owned read of cash available for a relative transfer quote."""
    from .available_funds import available_cash
    subject = await db.get(UserORM, subject_user_id)
    if subject is None or subject.status != "active":
        raise BankError("付款用户不可用", "PAYER_INACTIVE", 403)
    accounts = (await db.scalars(select(AccountORM).where(
        AccountORM.user_id == subject_user_id,
        AccountORM.status == "active",
        AccountORM.account_type.in_(["savings", "checking"]),
        AccountORM.account_id.not_in(isolated_account_ids()),
    ).order_by(AccountORM.account_id))).all()
    return {"accounts": [{"account_id": account.account_id,
                          "label": f"debit · 尾号{transfer_card_number(account.account_id)[-4:]}",
                          "available_fen": max(0, await available_cash(db, account.account_id, account.balance_fen))}
                         for account in accounts],
            "as_of": datetime.now(timezone.utc).isoformat()}


# A rejected bank transaction is rolled back before its security event is saved.
# Identity comes from the authenticated internal context, never a model parameter.
def _observe_transfer_boundary(function):
    from functools import wraps
    @wraps(function)
    async def wrapped(db, *args, **kwargs):
        try:
            return await function(db, *args, **kwargs)
        except BankError as error:
            from ...security.circuit_breaker import report_violation
            context = args[1] if len(args) > 1 else kwargs.get("context")
            owner = context.subject_user_id if context else kwargs.get("subject_user_id")
            if owner:
                await report_violation(db, owner, error.code)
            raise
    return wrapped


submit_transfer = _observe_transfer_boundary(submit_transfer)
validate_transfer_parties = _observe_transfer_boundary(validate_transfer_parties)
