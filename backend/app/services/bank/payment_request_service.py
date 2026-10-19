"""4号模拟银行：收款申请的数据访问与权限校验。"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from ...database.connection import AsyncSessionLocal
from ...domain import fen_to_yuan, yuan_to_fen
from ...models.account import AccountORM
from ...models.payment_request import PaymentRequestORM
from ...models.payment_request_batch import PaymentRequestBatchORM
from ...models.payment_request_payment import PaymentRequestPaymentORM
from ...models.user import UserORM
from .transfer_service import BankError
from .audit_service import record_bank_event

PENDING = "PENDING"
REJECTED = "REJECTED"
CANCELLED = "CANCELLED"
EXPIRED = "EXPIRED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _view(record: PaymentRequestORM, requester_name: str, payer_name: str) -> dict:
    return {
        "payment_request_id": record.payment_request_id,
        "requester_user_id": record.requester_user_id,
        "requester_name": requester_name,
        "payer_user_id": record.payer_user_id,
        "payer_name": payer_name,
        "receive_account_id": record.receive_account_id,
        "amount": float(fen_to_yuan(record.amount_fen)),
        "amount_fen": record.amount_fen,
        "currency": record.currency,
        "note": record.note,
        "status": record.status,
        "state_version": record.state_version,
        "expires_at": record.expires_at,
        "created_at": record.created_at,
        "responded_at": record.responded_at,
    }


def _expire_if_needed(record: PaymentRequestORM, now: datetime) -> bool:
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if record.status == PENDING and expires_at <= now:
        record.status = EXPIRED
        record.state_version += 1
        record.responded_at = now
        return True
    return False


async def create_payment_request(
    *,
    requester_user_id: str,
    payer_user_id: str,
    receive_account_id: str,
    amount: float,
    note: str | None,
    idempotency_key: str,
) -> dict:
    if requester_user_id == payer_user_id:
        raise BankError("不能向自己发起收款申请", "SELF_PAYMENT_REQUEST", 400)
    try:
        amount_fen = yuan_to_fen(amount)
    except ValueError as error:
        raise BankError(str(error), "INVALID_AMOUNT", 400) from error

    async with AsyncSessionLocal() as session:
        duplicate = (
            await session.execute(
                select(PaymentRequestORM).where(
                    PaymentRequestORM.requester_user_id == requester_user_id,
                    PaymentRequestORM.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            if (
                duplicate.payer_user_id != payer_user_id
                or duplicate.receive_account_id != receive_account_id
                or duplicate.amount_fen != amount_fen
                or (duplicate.note or "") != (note or "")
            ):
                raise BankError("幂等键已用于不同的收款申请", "IDEMPOTENCY_CONFLICT", 409)
            users = {
                user.user_id: user.name
                for user in (
                    await session.execute(
                        select(UserORM).where(
                            UserORM.user_id.in_([requester_user_id, payer_user_id])
                        )
                    )
                ).scalars()
            }
            return _view(duplicate, users[requester_user_id], users[payer_user_id])

        users = {
            user.user_id: user
            for user in (
                await session.execute(
                    select(UserORM).where(
                        UserORM.user_id.in_([requester_user_id, payer_user_id])
                    )
                )
            ).scalars()
        }
        requester = users.get(requester_user_id)
        payer = users.get(payer_user_id)
        if requester is None or payer is None:
            raise BankError("申请人或付款人不存在", "CUSTOMER_NOT_FOUND", 404)
        if requester.status != "active" or payer.status != "active":
            raise BankError("申请人或付款人当前不可用", "CUSTOMER_INACTIVE", 403)
        from .transfer_service import require_ordinary_accounts
        await require_ordinary_accounts(session,[receive_account_id])
        receive_account = await session.get(AccountORM, receive_account_id)
        if receive_account is None:
            raise BankError("收款账户不存在", "ACCOUNT_NOT_FOUND", 404)
        if receive_account.user_id != requester_user_id:
            raise BankError("收款账户不属于当前用户", "RECEIVER_FORBIDDEN", 403)
        if receive_account.status != "active":
            raise BankError("收款账户当前不可用", "ACCOUNT_INACTIVE", 403)

        record = PaymentRequestORM(
            payment_request_id=f"PR{uuid4().hex[:18].upper()}",
            requester_user_id=requester_user_id,
            payer_user_id=payer_user_id,
            receive_account_id=receive_account_id,
            amount_fen=amount_fen,
            currency="CNY",
            note=note,
            status=PENDING,
            state_version=1,
            idempotency_key=idempotency_key,
            expires_at=_now() + timedelta(days=7),
        )
        session.add(record)
        record_bank_event(session, event_key=record.payment_request_id,
            subject_user_id=requester_user_id, action="payment_request_create", result="success",
            facts={"payment_request_id": record.payment_request_id, "amount_fen": amount_fen},
            decision_chain=["participants_checked", "ownership_checked", "request_created"])
        try:
            await session.commit()
        except IntegrityError as error:
            # 并发重试可能在首次查询后命中唯一约束；回读权威记录并按幂等语义处理。
            await session.rollback()
            duplicate = (
                await session.execute(
                    select(PaymentRequestORM).where(
                        PaymentRequestORM.requester_user_id == requester_user_id,
                        PaymentRequestORM.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if duplicate is None:
                raise BankError("收款申请保存失败", "PAYMENT_REQUEST_CONFLICT", 409) from error
            if (
                duplicate.payer_user_id != payer_user_id
                or duplicate.receive_account_id != receive_account_id
                or duplicate.amount_fen != amount_fen
                or (duplicate.note or "") != (note or "")
            ):
                raise BankError("幂等键已用于不同的收款申请", "IDEMPOTENCY_CONFLICT", 409) from error
            return _view(duplicate, requester.name, payer.name)
        await session.refresh(record)
        return _view(record, requester.name, payer.name)


async def create_payment_request_batch(
    *,
    requester_user_id: str,
    payer_user_ids: list[str],
    receive_account_id: str,
    amount: float,
    split_equally: bool,
    note: str | None,
    idempotency_key: str,
) -> list[dict]:
    """整批创建多人收款申请；任一校验失败时不创建任何一条。"""
    if not payer_user_ids:
        raise BankError("至少选择一位收款对象", "PAYERS_REQUIRED", 400)
    payer_ids = list(dict.fromkeys(payer_user_ids))
    if len(payer_ids) != len(payer_user_ids):
        raise BankError("收款对象不能重复", "DUPLICATE_PAYERS", 400)
    if requester_user_id in payer_ids:
        raise BankError("不能向自己发起收款申请", "SELF_PAYMENT_REQUEST", 400)
    try:
        total_fen = yuan_to_fen(amount)
    except ValueError as error:
        raise BankError(str(error), "INVALID_AMOUNT", 400) from error
    if split_equally and total_fen % len(payer_ids) != 0:
        raise BankError(
            "总金额无法精确均分到分，请调整金额",
            "AMOUNT_NOT_EVENLY_DIVISIBLE",
            400,
        )
    amount_fen = total_fen // len(payer_ids) if split_equally else total_fen
    child_keys = {
        payer_id: hashlib.sha256(
            f"{idempotency_key}:{payer_id}".encode()
        ).hexdigest()
        for payer_id in payer_ids
    }
    batch_id = hashlib.sha256(f"{requester_user_id}:{idempotency_key}".encode()).hexdigest()
    request_digest = hashlib.sha256(json.dumps({
        "payers": sorted(payer_ids), "receive_account_id": receive_account_id,
        "amount_fen": total_fen, "split_equally": split_equally, "note": note or "",
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    async with AsyncSessionLocal() as session:
        # 同一申请人的批次串行化；SQLite使用数据库写锁，PG使用客户行锁。
        if session.get_bind().dialect.name == "sqlite":
            await session.execute(update(UserORM).where(UserORM.user_id == requester_user_id)
                                  .values(status=UserORM.status))
        else:
            await session.execute(select(UserORM.user_id).where(
                UserORM.user_id == requester_user_id).with_for_update())
        batch = await session.get(PaymentRequestBatchORM, batch_id)
        if batch is not None and batch.request_digest != request_digest:
            raise BankError("幂等键已用于不同的批量收款申请", "IDEMPOTENCY_CONFLICT", 409)
        users = {
            user.user_id: user
            for user in (
                await session.execute(
                    select(UserORM).where(
                        UserORM.user_id.in_([requester_user_id, *payer_ids])
                    )
                )
            ).scalars()
        }
        requester = users.get(requester_user_id)
        if requester is None or requester.status != "active":
            raise BankError("申请人当前不可用", "CUSTOMER_INACTIVE", 403)
        if any(users.get(payer_id) is None for payer_id in payer_ids):
            raise BankError("部分收款对象不存在", "CUSTOMER_NOT_FOUND", 404)
        if any(users[payer_id].status != "active" for payer_id in payer_ids):
            raise BankError("部分收款对象当前不可用", "CUSTOMER_INACTIVE", 403)
        receive_account = await session.get(AccountORM, receive_account_id)
        if receive_account is None:
            raise BankError("收款账户不存在", "ACCOUNT_NOT_FOUND", 404)
        if receive_account.user_id != requester_user_id:
            raise BankError("收款账户不属于当前用户", "RECEIVER_FORBIDDEN", 403)
        if receive_account.status != "active":
            raise BankError("收款账户当前不可用", "ACCOUNT_INACTIVE", 403)

        existing_records = (
            await session.execute(
                select(PaymentRequestORM).where(
                    PaymentRequestORM.requester_user_id == requester_user_id,
                    PaymentRequestORM.idempotency_key.in_(child_keys.values()),
                )
            )
        ).scalars().all()
        if existing_records and len(existing_records) != len(payer_ids):
            raise BankError("历史批次参与人不一致", "IDEMPOTENCY_CONFLICT", 409)
        if batch is None:
            session.add(PaymentRequestBatchORM(
                batch_id=batch_id, requester_user_id=requester_user_id,
                idempotency_key=idempotency_key, request_digest=request_digest))
        existing_by_payer = {item.payer_user_id: item for item in existing_records}
        records: list[PaymentRequestORM] = []
        for payer_id in payer_ids:
            existing = existing_by_payer.get(payer_id)
            if existing is not None:
                if (
                    existing.receive_account_id != receive_account_id
                    or existing.amount_fen != amount_fen
                    or (existing.note or "") != (note or "")
                ):
                    raise BankError(
                        "幂等键已用于不同的批量收款申请",
                        "IDEMPOTENCY_CONFLICT",
                        409,
                    )
                records.append(existing)
                continue
            record = PaymentRequestORM(
                payment_request_id=f"PR{uuid4().hex[:18].upper()}",
                requester_user_id=requester_user_id,
                payer_user_id=payer_id,
                receive_account_id=receive_account_id,
                amount_fen=amount_fen,
                currency="CNY",
                note=note,
                status=PENDING,
                state_version=1,
                idempotency_key=child_keys[payer_id],
                expires_at=_now() + timedelta(days=7),
            )
            session.add(record)
            record_bank_event(session, event_key=record.payment_request_id,
                subject_user_id=requester_user_id, action="payment_request_create", result="success",
                facts={"batch_id": batch_id, "payment_request_id": record.payment_request_id,
                       "amount_fen": amount_fen},
                decision_chain=["participants_checked", "ownership_checked", "batch_request_created"])
            records.append(record)
        try:
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise BankError("批量收款申请冲突，请按原编号重试", "PAYMENT_REQUEST_CONFLICT", 409) from error
        for record in records:
            await session.refresh(record)
        return [
            _view(record, requester.name, users[record.payer_user_id].name)
            for record in records
        ]


async def list_payment_requests(*, user_id: str, role: str = "all") -> list[dict]:
    if role not in {"all", "incoming", "outgoing"}:
        raise BankError("role 参数无效", "INVALID_ROLE", 400)
    statement = select(PaymentRequestORM)
    if role == "incoming":
        statement = statement.where(PaymentRequestORM.payer_user_id == user_id)
    elif role == "outgoing":
        statement = statement.where(PaymentRequestORM.requester_user_id == user_id)
    else:
        statement = statement.where(
            or_(
                PaymentRequestORM.payer_user_id == user_id,
                PaymentRequestORM.requester_user_id == user_id,
            )
        )
    async with AsyncSessionLocal() as session:
        records = (
            await session.execute(statement.order_by(PaymentRequestORM.created_at.desc()))
        ).scalars().all()
        changed = False
        now = _now()
        for record in records:
            changed = _expire_if_needed(record, now) or changed
        user_ids = {value for record in records for value in (record.requester_user_id, record.payer_user_id)}
        names = {
            user.user_id: user.name
            for user in (
                await session.execute(select(UserORM).where(UserORM.user_id.in_(user_ids)))
            ).scalars()
        } if user_ids else {}
        if changed:
            await session.commit()
        from .merchant_refund_service import refund_summaries
        refunds=await refund_summaries(session,[r.payment_request_id for r in records])
        return [{**_view(record,names[record.requester_user_id],names[record.payer_user_id]),'refunded_fen':refunds.get(record.payment_request_id,0),'remaining_refundable_fen':max(0,record.amount_fen-refunds.get(record.payment_request_id,0))} for record in records]


async def respond_to_payment_request(
    *,
    payment_request_id: str,
    payer_user_id: str,
    action: str,
    state_version: int,
) -> dict:
    if action != "reject":
        raise BankError("不支持的处理动作", "INVALID_ACTION", 400)
    async with AsyncSessionLocal() as session:
        record = (
            await session.execute(
                select(PaymentRequestORM)
                .where(PaymentRequestORM.payment_request_id == payment_request_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if record is None:
            raise BankError("收款申请不存在", "PAYMENT_REQUEST_NOT_FOUND", 404)
        if record.payer_user_id != payer_user_id:
            raise BankError("只有指定付款人可以处理该申请", "PAYMENT_REQUEST_FORBIDDEN", 403)
        if _expire_if_needed(record, _now()):
            await session.commit()
        if record.state_version != state_version:
            raise BankError("收款申请状态已变化，请刷新后重试", "STALE_STATE_VERSION", 409)
        if record.status != PENDING:
            raise BankError("该收款申请已处理", "PAYMENT_REQUEST_NOT_PENDING", 409)
        responded_at = _now()
        updated = await session.execute(
            update(PaymentRequestORM)
            .where(
                PaymentRequestORM.payment_request_id == payment_request_id,
                PaymentRequestORM.status == PENDING,
                PaymentRequestORM.state_version == state_version,
            )
            .values(
                status=REJECTED,
                state_version=state_version + 1,
                responded_at=responded_at,
            )
            .execution_options(synchronize_session=False)
        )
        if updated.rowcount != 1:
            await session.rollback()
            raise BankError(
                "收款申请状态已变化，请刷新后重试",
                "STALE_STATE_VERSION",
                409,
            )
        users = {
            user.user_id: user.name
            for user in (
                await session.execute(
                    select(UserORM).where(
                        UserORM.user_id.in_([record.requester_user_id, record.payer_user_id])
                    )
                )
            ).scalars()
        }
        record_bank_event(session, event_key=f"{payment_request_id}:{state_version + 1}",
            subject_user_id=payer_user_id, action="payment_request_reject", result="cancelled",
            facts={"payment_request_id": payment_request_id, "state_version": state_version + 1},
            decision_chain=["payer_ownership_checked", "version_checked", "request_rejected"])
        await session.commit()
        await session.refresh(record)
        return _view(record, users[record.requester_user_id], users[record.payer_user_id])


async def prepare_payment_request_payment(
    *,
    payment_request_id: str,
    payer_user_id: str,
    payer_account_id: str,
    state_version: int,
) -> dict:
    """冻结申请参数并返回创建安全转账任务所需的最小事实，不执行扣款。"""
    async with AsyncSessionLocal() as session:
        record = (
            await session.execute(
                select(PaymentRequestORM)
                .where(PaymentRequestORM.payment_request_id == payment_request_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if record is None:
            raise BankError("收款申请不存在", "PAYMENT_REQUEST_NOT_FOUND", 404)
        if record.payer_user_id != payer_user_id:
            raise BankError("只有指定付款人可以接受该申请", "PAYMENT_REQUEST_FORBIDDEN", 403)
        if _expire_if_needed(record, _now()):
            await session.commit()
        if record.status not in {PENDING, "ACCEPTED"}:
            raise BankError("该收款申请已处理", "PAYMENT_REQUEST_NOT_PENDING", 409)

        existing = await session.get(PaymentRequestPaymentORM, payment_request_id)
        if existing is not None:
            return {
                "operation_id": existing.operation_id,
                "payer_account_id": existing.payer_account_id,
                "receive_account_id": record.receive_account_id,
                "amount": fen_to_yuan(record.amount_fen),
                "note": record.note,
            }
        if record.state_version != state_version:
            raise BankError("收款申请状态已变化，请刷新后重试", "STALE_STATE_VERSION", 409)

        accounts = {
            account.account_id: account
            for account in (
                await session.execute(
                    select(AccountORM).where(
                        AccountORM.account_id.in_(
                            [payer_account_id, record.receive_account_id]
                        )
                    )
                )
            ).scalars()
        }
        payer = accounts.get(payer_account_id)
        receiver = accounts.get(record.receive_account_id)
        if payer is None or receiver is None:
            raise BankError("付款或收款账户不存在", "ACCOUNT_NOT_FOUND", 404)
        if payer.user_id != payer_user_id:
            raise BankError("付款账户不属于当前付款人", "PAYER_FORBIDDEN", 403)
        if receiver.user_id != record.requester_user_id:
            raise BankError("收款账户归属异常", "RECEIVER_FORBIDDEN", 403)
        if payer.status != "active" or receiver.status != "active":
            raise BankError("付款或收款账户当前不可用", "ACCOUNT_INACTIVE", 403)

        operation_id = f"PRPAY{uuid4().hex[:18].upper()}"
        session.add(
            PaymentRequestPaymentORM(
                payment_request_id=payment_request_id,
                operation_id=operation_id,
                payer_account_id=payer_account_id,
            )
        )
        if record.status == PENDING:
            record.status = "ACCEPTED"
            record.state_version += 1
        record_bank_event(session, event_key=operation_id, subject_user_id=payer_user_id,
            action="payment_request_accept", result="success",
            facts={"operation_id": operation_id, "payment_request_id": payment_request_id},
            decision_chain=["payer_ownership_checked", "version_checked", "payment_reserved"])
        await session.commit()
        return {
            "operation_id": operation_id,
            "payer_account_id": payer_account_id,
            "receive_account_id": record.receive_account_id,
            "amount": fen_to_yuan(record.amount_fen),
            "note": record.note,
        }


async def cancel_payment_request(
    *, payment_request_id: str, requester_user_id: str, state_version: int
) -> dict:
    async with AsyncSessionLocal() as session:
        record = (
            await session.execute(
                select(PaymentRequestORM)
                .where(PaymentRequestORM.payment_request_id == payment_request_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if record is None:
            raise BankError("收款申请不存在", "PAYMENT_REQUEST_NOT_FOUND", 404)
        if record.requester_user_id != requester_user_id:
            raise BankError("只有申请人可以取消该申请", "PAYMENT_REQUEST_FORBIDDEN", 403)
        if _expire_if_needed(record, _now()):
            await session.commit()
        if record.state_version != state_version:
            raise BankError("收款申请状态已变化，请刷新后重试", "STALE_STATE_VERSION", 409)
        if record.status != PENDING:
            raise BankError("只有待处理的收款申请可以取消", "PAYMENT_REQUEST_NOT_PENDING", 409)
        responded_at = _now()
        updated = await session.execute(
            update(PaymentRequestORM)
            .where(
                PaymentRequestORM.payment_request_id == payment_request_id,
                PaymentRequestORM.status == PENDING,
                PaymentRequestORM.state_version == state_version,
            )
            .values(
                status=CANCELLED,
                state_version=state_version + 1,
                responded_at=responded_at,
            )
            .execution_options(synchronize_session=False)
        )
        if updated.rowcount != 1:
            await session.rollback()
            raise BankError("收款申请状态已变化，请刷新后重试", "STALE_STATE_VERSION", 409)
        users = {
            user.user_id: user.name
            for user in (
                await session.execute(
                    select(UserORM).where(
                        UserORM.user_id.in_([record.requester_user_id, record.payer_user_id])
                    )
                )
            ).scalars()
        }
        record_bank_event(session, event_key=f"{payment_request_id}:{state_version + 1}",
            subject_user_id=requester_user_id, action="payment_request_cancel", result="cancelled",
            facts={"payment_request_id": payment_request_id, "state_version": state_version + 1},
            decision_chain=["requester_ownership_checked", "version_checked", "request_cancelled"])
        await session.commit()
        await session.refresh(record)
        return _view(record, users[record.requester_user_id], users[record.payer_user_id])


async def read_payment_snapshots(db, *, user_id: str, requester_user_id: str,
                                 payment_request_ids: list[str]) -> dict[str, dict]:
    """本人店铺收款申请的只读快照；过期只投影，不更新状态或版本。"""
    from ...domain.time import as_utc
    if not payment_request_ids:
        return {}
    records = (await db.scalars(select(PaymentRequestORM).where(
        PaymentRequestORM.payment_request_id.in_(payment_request_ids),
        PaymentRequestORM.payer_user_id == user_id,
        PaymentRequestORM.requester_user_id == requester_user_id))).all()
    now = _now()
    from .merchant_refund_service import refund_summaries
    refunds=await refund_summaries(db,[r.payment_request_id for r in records])
    return {record.payment_request_id: {
        'refunded_fen':refunds.get(record.payment_request_id,0),
        "payment_request_id": record.payment_request_id,
        "status": EXPIRED if record.status == PENDING and as_utc(record.expires_at, legacy_utc=True) <= now else record.status,
        "stored_status": record.status, "state_version": record.state_version,
        "amount_fen": record.amount_fen, "currency": record.currency,
        "expires_at": as_utc(record.expires_at, legacy_utc=True),
    } for record in records}
