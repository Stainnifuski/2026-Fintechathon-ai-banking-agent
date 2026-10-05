"""5号应用编排：任务、授权、受理、执行、核对与人工控制。"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.phone import normalize_phone, mask_phone
from ...domain import (
    AuthorizationStatus,
    BankOperationStatus,
    ControlMode,
    OperationStatus,
    OutboxStatus,
    RiskReservationStatus,
    StepStatus,
    TaskOutcome,
    TaskPhase,
    fen_to_yuan,
    yuan_to_fen,
)
from ...integrations.authorization import (
    AuthorizationError,
    CapabilityNotImplemented,
    consume,
    create_risk_hold,
    new_authorization,
    precheck_risk,
    record_consent,
    serialize_risk_subject,
    validate_for_acceptance,
)
from ...integrations.bank_api import (
    BankApi,
    BankApiError,
    BankOperationResult,
    BankResponseUnknown,
    BankTransferCommand,
    TrustedExecutionContext,
)
from ...models.workflow import (
    AuthorizationORM,
    HumanTicketORM,
    OutboxEntryORM,
    RiskReservationORM,
    StateEventORM,
    TransferOperationORM,
    TransferPlanORM,
    TransferStepORM,
    TransferTaskORM,
)


class TaskError(Exception):
    def __init__(self, message: str, status_code: int = 400, code: str = "TASK_ERROR") -> None:
        self.message = message
        self.status_code = status_code
        self.code = code
        super().__init__(message)


def _validated_note(note: str | None, source: str | None) -> tuple[str | None, str | None]:
    if source not in {None, "manual", "automatic", "cleared"}:
        raise TaskError("备注来源无效", 422, "INVALID_NOTE_SOURCE")
    if source == "cleared":
        if note is not None:
            raise TaskError("清空备注时不能同时提供内容", 422, "INVALID_NOTE")
        return None, source
    if source == "automatic" and note is None:
        note = "转账"
    if source == "manual" and (note is None or not note.strip()):
        raise TaskError("手动备注不能为空", 422, "INVALID_NOTE")
    if note is not None and (
        len(note) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in note)
        or (note and not note.strip())
    ):
        raise TaskError("备注为空白、过长或含控制字符", 422, "INVALID_NOTE")
    return note, source


async def _checked_risk_level(
    db: AsyncSession, *, user_id: str, amount_fen: int,
    daily_transfer_used_fen: int,
) -> str:
    try:
        level = await precheck_risk(
            db, user_id=user_id, amount_fen=amount_fen,
            daily_transfer_used_fen=daily_transfer_used_fen,
        )
    except Exception as error:
        raise TaskError(
            "风险服务暂不可用，请稍后重试", 503, "RISK_UNAVAILABLE"
        ) from error
    if level not in {"YELLOW", "RED"}:
        raise TaskError(
            "风险服务返回无效等级，已阻止转账", 503, "RISK_DECISION_INVALID"
        )
    return level


def _id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:16].upper()}"


def parameter_digest(
    from_account: str,
    to_account: str,
    amount_fen: int,
    currency: str,
    note: str | None,
    recipient_phone: str | None = None,
) -> str:
    payload = {
        "from_account": from_account,
        "to_account": to_account,
        "amount_fen": amount_fen,
        "currency": currency,
        "note": note or "",
    }
    if recipient_phone is not None:
        payload["recipient_phone"] = normalize_phone(recipient_phone)
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


async def _event(
    db: AsyncSession,
    *,
    task_id: str,
    operation_id: str | None,
    request_id: str | None,
    event_type: str,
    actor: str,
    before: dict,
    after: dict,
) -> None:
    db.add(
        StateEventORM(
            event_id=_id("EVT"),
            task_id=task_id,
            operation_id=operation_id,
            request_id=request_id,
            event_type=event_type,
            actor=actor,
            before_state=before,
            after_state=after,
        )
    )


async def _deduplicated_task_id(db: AsyncSession, request_id: str) -> str | None:
    event = (
        await db.execute(select(StateEventORM).where(StateEventORM.request_id == request_id))
    ).scalar_one_or_none()
    return event.task_id if event else None


async def _owned_task(db: AsyncSession, task_id: str, user_id: str) -> TransferTaskORM:
    task = await db.get(TransferTaskORM, task_id)
    if task is None:
        raise TaskError("任务不存在", 404, "TASK_NOT_FOUND")
    if task.user_id != user_id:
        raise TaskError("无权访问该任务", 403, "TASK_FORBIDDEN")
    return task


async def _current_operation(
    db: AsyncSession, task: TransferTaskORM
) -> TransferOperationORM:
    operation = (
        await db.execute(
            select(TransferOperationORM)
            .where(
                TransferOperationORM.task_id == task.task_id,
                TransferOperationORM.plan_version == task.current_plan_version,
            )
            .order_by(TransferOperationORM.created_at.desc())
        )
    ).scalars().first()
    if operation is None:
        raise TaskError("任务没有当前操作", 409, "OPERATION_MISSING")
    return operation


async def _current_plan(db: AsyncSession, task: TransferTaskORM) -> TransferPlanORM:
    plan = (
        await db.execute(
            select(TransferPlanORM).where(
                TransferPlanORM.task_id == task.task_id,
                TransferPlanORM.plan_version == task.current_plan_version,
            )
        )
    ).scalar_one_or_none()
    if plan is None:
        raise TaskError("任务没有当前计划", 409, "PLAN_MISSING")
    return plan


async def _authorization(
    db: AsyncSession, operation_id: str
) -> AuthorizationORM:
    authorization = (
        await db.execute(
            select(AuthorizationORM)
            .where(AuthorizationORM.operation_id == operation_id)
            .order_by(AuthorizationORM.created_at.desc())
        )
    ).scalars().first()
    if authorization is None:
        raise TaskError("操作没有授权记录", 409, "AUTHORIZATION_MISSING")
    return authorization


async def _step(db: AsyncSession, step_id: str) -> TransferStepORM:
    step = await db.get(TransferStepORM, step_id)
    if step is None:
        raise TaskError("任务步骤不存在", 409, "STEP_MISSING")
    return step


def _check_version(task: TransferTaskORM, expected: int) -> None:
    if task.state_version != expected:
        raise TaskError(
            f"任务版本已变化，当前版本为 {task.state_version}",
            409,
            "STALE_STATE_VERSION",
        )

async def _reserve_version(db: AsyncSession, task: TransferTaskORM, expected: int) -> None:
    """锁定版本到本事务结束，防止并发修改与确认/受理跨过旧版本检查。"""
    _check_version(task, expected)
    result = await db.execute(update(TransferTaskORM).where(
        TransferTaskORM.task_id == task.task_id,
        TransferTaskORM.state_version == expected,
    ).values(state_version=expected))
    if result.rowcount != 1:
        raise TaskError("任务版本已变化，请刷新后重试", 409, "STALE_STATE_VERSION")


async def _require_complete_proposal(db: AsyncSession, task: TransferTaskORM) -> None:
    """存在已保存但待消歧的修改时，旧卡片不得确认/受理。"""
    from ...models.conversation import ConversationORM
    states = (await db.execute(select(ConversationORM.state).where(
        ConversationORM.user_id == task.user_id
    ))).scalars().all()
    if any(state.get("task_id") == task.task_id and state.get("editing") for state in states):
        raise TaskError("修改仍需补充或选择，请完成修改后使用新卡片", 409, "PROPOSAL_INCOMPLETE")


# 持久化一次转账申请
async def create_transfer_task(
    db: AsyncSession,
    bank: BankApi,
    *,
    user_id: str,
    request_id: str,
    operation_id: str,
    from_account: str,
    to_account: str,
    amount: float,
    note: str | None = None,
    recipient_phone: str | None = None,
    note_source: str | None = None,
    scheduled_at: datetime | None = None,
    commit: bool = True,
) -> dict:
    duplicate = await _deduplicated_task_id(db, request_id)
    if duplicate:
        return await task_snapshot(db, duplicate, user_id)
    note, note_source = _validated_note(note, note_source)
    if await db.get(TransferOperationORM, operation_id):
        raise TaskError("operation_id 已被其它任务使用", 409, "OPERATION_ID_CONFLICT")
    if from_account == to_account:
        raise TaskError("付款账户和收款账户不能相同")
    try:
        amount_fen = yuan_to_fen(amount)
    except ValueError as error:
        raise TaskError(str(error)) from error
    scheduled_at_utc: datetime | None = None
    if scheduled_at is not None:
        if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None:
            raise TaskError("定时执行时间必须包含时区", 422, "SCHEDULE_TIMEZONE_REQUIRED")
        scheduled_at_utc = scheduled_at.astimezone(timezone.utc)
        if scheduled_at_utc <= datetime.now(timezone.utc):
            raise TaskError("定时执行时间必须晚于当前时间", 422, "SCHEDULE_TIME_IN_PAST")

    try:
        bank_validation = await bank.validate_transfer(
            subject_user_id=user_id,
            from_account=from_account,
            to_account=to_account,
            **({"recipient_phone": recipient_phone} if recipient_phone else {}),
        )
    except BankApiError as error:
        raise TaskError(error.message, error.status_code, error.code) from error

    try:
        canonical_phone = normalize_phone(recipient_phone) if recipient_phone else None
    except ValueError as error:
        raise TaskError(str(error), 422, "INVALID_PHONE") from error
    digest = parameter_digest(from_account, to_account, amount_fen, "CNY", note, canonical_phone)
    # 3号风险检查
    risk_level = await _checked_risk_level(
        db,
        user_id=user_id,
        amount_fen=amount_fen,
        daily_transfer_used_fen=bank_validation.daily_transfer_used_fen,
    )
    task_id, plan_id, step_id = _id("TASK"), _id("PLAN"), _id("STEP")
    task = TransferTaskORM(
        task_id=task_id,
        user_id=user_id,
        phase=TaskPhase.WAITING_CONFIRMATION,
        outcome=TaskOutcome.NONE,
        control_mode=ControlMode.AUTO,
        state_version=1,
        current_plan_version=1,
        control_epoch=1,
    )
    plan = TransferPlanORM(
        plan_id=plan_id,
        task_id=task_id,
        plan_version=1,
        params={
            "from_account": from_account,
            "to_account": to_account,
            "amount_fen": amount_fen,
            "currency": "CNY",
            "note": note,
            "note_source": note_source,
            "recipient_phone": canonical_phone,
            "recipient_name": bank_validation.recipient_name,
            "scheduled_at": scheduled_at_utc.isoformat() if scheduled_at_utc else None,
        },
    )
    step = TransferStepORM(
        step_id=step_id,
        task_id=task_id,
        plan_id=plan_id,
        status=StepStatus.WAITING_AUTHORIZATION,
    )
    operation = TransferOperationORM(
        operation_id=operation_id,
        task_id=task_id,
        step_id=step_id,
        plan_version=1,
        status=OperationStatus.DRAFT,
        from_account=from_account,
        to_account=to_account,
        amount_fen=amount_fen,
        currency="CNY",
        note=note,
        param_digest=digest,
    )
    authorization = new_authorization(
        operation_id=operation_id,
        user_id=user_id,
        plan_version=1,
        param_digest=digest,
        risk_level=risk_level,
    )
    # 显式按外键顺序 flush；这些模型刻意不建立 ORM 级联关系。
    db.add(task)
    await db.flush()
    db.add(plan)
    await db.flush()
    db.add(step)
    await db.flush()
    db.add(operation)
    await db.flush()
    db.add(authorization)
    await _event(
        db,
        task_id=task_id,
        operation_id=operation_id,
        request_id=request_id,
        event_type="TASK_CREATED",
        actor=user_id,
        before={},
        after={"phase": TaskPhase.WAITING_CONFIRMATION, "operation": OperationStatus.DRAFT},
    )
    if commit:
        await db.commit()
    else:
        await db.flush()
    return await task_snapshot(db, task_id, user_id)

# 点击同意后，进行状态更新和落盘
async def confirm_task(
    db: AsyncSession,
    *,
    task_id: str,
    user_id: str,
    request_id: str,
    state_version: int,
    plan_version: int,
) -> dict:
    task = await _owned_task(db, task_id, user_id)
    duplicate = await _deduplicated_task_id(db, request_id)
    if duplicate == task_id:
        return await task_snapshot(db, task_id, user_id)
    await _reserve_version(db, task, state_version)
    await _require_complete_proposal(db, task)
    if task.current_plan_version != plan_version:
        raise TaskError("确认卡版本已经失效", 409, "STALE_PLAN_VERSION")
    operation = await _current_operation(db, task)
    authorization = await _authorization(db, operation.operation_id)
    plan = await _current_plan(db, task)
    if operation.status != OperationStatus.DRAFT:
        raise TaskError("当前操作不能确认", 409, "INVALID_OPERATION_STATE")

    # 调整授权状态（黄->同意即可生效，红->同意后还要验证）
    record_consent(authorization)
    before = {"phase": task.phase, "operation": operation.status}
    if authorization.status == AuthorizationStatus.VALID:
        operation.status = OperationStatus.READY
        (await _step(db, operation.step_id)).status = StepStatus.READY
        task.phase = (
            TaskPhase.WAITING_EXECUTION
            if plan.params.get("scheduled_at")
            else TaskPhase.READY
        )
    else:
        task.phase = TaskPhase.WAITING_VERIFICATION
    operation.state_version += 1
    task.state_version += 1
    await _event(
        db,
        task_id=task_id,
        operation_id=operation.operation_id,
        request_id=request_id,
        event_type="CONSENT_RECORDED",
        actor=user_id,
        before=before,
        after={"phase": task.phase, "operation": operation.status, "authorization": authorization.status},
    )
    await db.commit()
    return await task_snapshot(db, task_id, user_id)

# 真正处理业务
async def accept_task(
    db: AsyncSession,
    bank: BankApi,
    *,
    task_id: str,
    user_id: str,
    request_id: str,
    state_version: int,
    simulate_response_loss: bool = False,
    execution_time: datetime | None = None,
) -> dict:
    task = await _owned_task(db, task_id, user_id)
    duplicate = await _deduplicated_task_id(db, request_id)
    if duplicate == task_id:
        return await task_snapshot(db, task_id, user_id)
    await _reserve_version(db, task, state_version)
    await _require_complete_proposal(db, task)
    operation = await _current_operation(db, task)
    authorization = await _authorization(db, operation.operation_id)
    if task.cancel_requested or task.control_mode != ControlMode.AUTO:
        raise TaskError("任务已取消或执行权不在自动执行器", 409, "EXECUTION_NOT_ALLOWED")
    if operation.status != OperationStatus.READY:
        raise TaskError("操作尚未准备就绪", 409, "OPERATION_NOT_READY")

    plan = await _current_plan(db, task)
    scheduled_at = plan.params.get("scheduled_at")
    if scheduled_at:
        try:
            execute_at = datetime.fromisoformat(scheduled_at)
            if execute_at.tzinfo is None or execute_at.utcoffset() is None:
                raise ValueError("missing schedule timezone")
        except (TypeError, ValueError) as error:
            raise TaskError("定时执行时间无效，请重新规划", 409, "SCHEDULE_TIME_INVALID") from error
        # execution_time is a trusted scheduler/test clock, never an HTTP field.
        current_time = execution_time or datetime.now(timezone.utc)
        if execute_at.astimezone(timezone.utc) > current_time.astimezone(timezone.utc):
            raise TaskError("尚未到定时执行时间", 409, "SCHEDULE_NOT_DUE")

    await serialize_risk_subject(db, user_id)
    phone_binding = plan.params.get("recipient_phone")
    try:
        bank_validation = await bank.validate_transfer(
            subject_user_id=user_id,
            from_account=operation.from_account,
            to_account=operation.to_account,
            **({"recipient_phone": phone_binding} if phone_binding else {}),
        )
    except BankApiError as error:
        raise TaskError(error.message, error.status_code, error.code) from error
    latest_risk = await _checked_risk_level(
        db,
        user_id=user_id,
        amount_fen=operation.amount_fen,
        daily_transfer_used_fen=bank_validation.daily_transfer_used_fen,
    )
    if latest_risk != authorization.risk_level:
        authorization.risk_level = latest_risk
        authorization.status = AuthorizationStatus.PENDING_VERIFICATION
        operation.status = OperationStatus.DRAFT
        task.phase = TaskPhase.WAITING_VERIFICATION
        task.state_version += 1
        operation.state_version += 1
        await _event(
            db,
            task_id=task_id,
            operation_id=operation.operation_id,
            request_id=request_id,
            event_type="RISK_UPGRADED",
            actor="authorization-adapter",
            before={"risk": "YELLOW"},
            after={"risk": latest_risk, "phase": task.phase},
        )
        await db.commit()
        raise TaskError("风险等级已升级，请完成强验证", 409, "VERIFICATION_REQUIRED")

    try:
        validate_for_acceptance(
            authorization,
            operation_digest=operation.param_digest,
            plan_version=operation.plan_version,
        )
        consume(authorization)
    except AuthorizationError as error:
        raise TaskError(error.message, 409, error.code) from error

    risk_hold = create_risk_hold(
        operation_id=operation.operation_id,
        user_id=user_id,
        amount_fen=operation.amount_fen,
    )
    outbox = OutboxEntryORM(
        outbox_id=_id("OUT"),
        operation_id=operation.operation_id,
        status=OutboxStatus.PENDING,
        control_epoch=task.control_epoch,
    )
    operation.status = OperationStatus.ACCEPTED
    operation.state_version += 1
    (await _step(db, operation.step_id)).status = StepStatus.RUNNING
    task.phase = TaskPhase.EXECUTING
    task.state_version += 1
    db.add_all([risk_hold, outbox])
    await _event(
        db,
        task_id=task_id,
        operation_id=operation.operation_id,
        request_id=request_id,
        event_type="APPLICATION_ACCEPTED",
        actor=user_id,
        before={"operation": OperationStatus.READY},
        after={
            "operation": OperationStatus.ACCEPTED,
            "authorization": AuthorizationStatus.CONSUMED,
            "risk": RiskReservationStatus.HELD,
        },
    )
    # T06：授权消费、风险占用、操作受理和 outbox 必须先原子提交。
    await db.commit()
    # 调用银行的业务
    await dispatch_task(
        db,
        bank,
        task_id=task_id,
        user_id=user_id,
        simulate_response_loss=simulate_response_loss,
    )
    return await task_snapshot(db, task_id, user_id)

# 会真正调用银行的转账API
async def dispatch_task(
    db: AsyncSession,
    bank: BankApi,
    *,
    task_id: str,
    user_id: str,
    simulate_response_loss: bool = False,
) -> None:
    task = await _owned_task(db, task_id, user_id)
    operation = await _current_operation(db, task)
    authorization = await _authorization(db, operation.operation_id)
    outbox = (
        await db.execute(
            select(OutboxEntryORM).where(OutboxEntryORM.operation_id == operation.operation_id)
        )
    ).scalar_one()
    if operation.status not in {OperationStatus.ACCEPTED, OperationStatus.UNKNOWN}:
        return
    if outbox.control_epoch != task.control_epoch or task.control_mode != ControlMode.AUTO:
        raise TaskError("执行权版本已经失效", 409, "STALE_CONTROL_EPOCH")

    operation.status = OperationStatus.SUBMITTING
    operation.state_version += 1
    outbox.status = OutboxStatus.DISPATCHING
    outbox.attempt_count += 1
    task.attempt_count += 1
    task.state_version += 1
    await _event(
        db,
        task_id=task_id,
        operation_id=operation.operation_id,
        request_id=None,
        event_type="BANK_SUBMITTING",
        actor="outbox-dispatcher",
        before={"operation": OperationStatus.ACCEPTED},
        after={"operation": OperationStatus.SUBMITTING, "attempt": outbox.attempt_count},
    )
    await db.commit()

    plan = await _current_plan(db, task)
    command = BankTransferCommand(
        operation_id=operation.operation_id,
        from_account=operation.from_account,
        to_account=operation.to_account,
        amount_fen=operation.amount_fen,
        currency=operation.currency,
        note=operation.note,
        recipient_phone=plan.params.get("recipient_phone"),
        param_digest=operation.param_digest,
    )
    context = TrustedExecutionContext(
        subject_user_id=user_id,
        authorization_id=authorization.authorization_id,
        control_epoch=task.control_epoch,
    )
    try:
        # submit_transfer是真正把本地请求向银行服务提交
        result = await bank.submit_transfer(
            command,
            context,
            simulate_response_loss=simulate_response_loss,
        )
    except BankResponseUnknown as error:
        operation.status = OperationStatus.UNKNOWN
        operation.state_version += 1
        (await _step(db, operation.step_id)).status = StepStatus.RECONCILING
        task.phase = TaskPhase.RECONCILING
        task.state_version += 1
        outbox.status = OutboxStatus.RETRY
        outbox.last_error = str(error)
        outbox.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=5)
        await _event(
            db,
            task_id=task_id,
            operation_id=operation.operation_id,
            request_id=None,
            event_type="BANK_RESPONSE_UNKNOWN",
            actor="outbox-dispatcher",
            before={"operation": OperationStatus.SUBMITTING},
            after={"operation": OperationStatus.UNKNOWN},
        )
        await db.commit()
        return
    except BankApiError as error:
        try:
            result = await bank.query_operation(operation.operation_id)
        except BankResponseUnknown:
            result = None
        if result is None:
            operation.status = OperationStatus.UNKNOWN
            operation.state_version += 1
            (await _step(db, operation.step_id)).status = StepStatus.RECONCILING
            task.phase = TaskPhase.RECONCILING
            task.error_code = error.code
            task.state_version += 1
            outbox.status = OutboxStatus.RETRY
            outbox.last_error = error.message
            outbox.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=5)
            await _event(
                db,
                task_id=task_id,
                operation_id=operation.operation_id,
                request_id=None,
                event_type="BANK_SUBMIT_UNKNOWN",
                actor="outbox-dispatcher",
                before={"operation": OperationStatus.SUBMITTING},
                after={"operation": OperationStatus.UNKNOWN, "error_code": error.code},
            )
            await db.commit()
            return

    await _merge_bank_result(db, task, operation, outbox, result)


async def _merge_bank_result(
    db: AsyncSession,
    task: TransferTaskORM,
    operation: TransferOperationORM,
    outbox: OutboxEntryORM,
    result: BankOperationResult,
) -> None:
    risk = (
        await db.execute(
            select(RiskReservationORM).where(
                RiskReservationORM.operation_id == operation.operation_id
            )
        )
    ).scalar_one()
    step = await _step(db, operation.step_id)
    before = operation.status
    operation.bank_status = result.status
    operation.bank_tx_id = result.bank_tx_id
    if result.status == BankOperationStatus.SUCCEEDED:
        operation.status = OperationStatus.SUCCEEDED
        step.status = StepStatus.SUCCEEDED
        risk.status = RiskReservationStatus.SETTLED
        task.phase = TaskPhase.FINISHED
        task.outcome = TaskOutcome.SUCCEEDED
        outbox.status = OutboxStatus.SENT
    elif result.status == BankOperationStatus.FAILED:
        operation.status = OperationStatus.FAILED
        operation.failure_code = result.error_code
        step.status = StepStatus.FAILED
        risk.status = RiskReservationStatus.RELEASED
        task.phase = TaskPhase.FINISHED
        task.outcome = TaskOutcome.FAILED
        outbox.status = OutboxStatus.SENT
    else:
        operation.status = OperationStatus.PROCESSING
        task.phase = TaskPhase.EXECUTING
        outbox.status = OutboxStatus.SENT
    operation.state_version += 1
    task.state_version += 1
    await _event(
        db,
        task_id=task.task_id,
        operation_id=operation.operation_id,
        request_id=None,
        event_type="BANK_RESULT_MERGED",
        actor="bank-adapter",
        before={"operation": before},
        after={
            "operation": operation.status,
            "bank_status": result.status,
            "bank_tx_id": result.bank_tx_id,
        },
    )
    await db.commit()


async def reconcile_task(
    db: AsyncSession,
    bank: BankApi,
    *,
    task_id: str,
    user_id: str,
    request_id: str,
    state_version: int,
) -> dict:
    task = await _owned_task(db, task_id, user_id)
    duplicate = await _deduplicated_task_id(db, request_id)
    if duplicate == task_id:
        return await task_snapshot(db, task_id, user_id)
    await _reserve_version(db, task, state_version)
    operation = await _current_operation(db, task)
    if operation.status not in {OperationStatus.UNKNOWN, OperationStatus.PROCESSING}:
        raise TaskError("当前操作不需要核对", 409, "RECONCILIATION_NOT_ALLOWED")
    try:
        result = await bank.query_operation(operation.operation_id)
    except BankApiError as error:
        raise TaskError(error.message, 502, error.code) from error
    outbox = (
        await db.execute(
            select(OutboxEntryORM).where(OutboxEntryORM.operation_id == operation.operation_id)
        )
    ).scalar_one()
    if result is not None:
        await _merge_bank_result(db, task, operation, outbox, result)
        await _event(
            db,
            task_id=task_id,
            operation_id=operation.operation_id,
            request_id=request_id,
            event_type="RECONCILIATION_COMPLETED",
            actor=user_id,
            before={},
            after={"operation": operation.status},
        )
        await db.commit()
        return await task_snapshot(db, task_id, user_id)

    task.attempt_count += 1
    task.state_version += 1
    if task.attempt_count >= 3:
        task.control_mode = ControlMode.HANDOFF_PENDING
        task.phase = TaskPhase.WAITING_HUMAN
        if await db.get(HumanTicketORM, task_id) is None:
            db.add(
                HumanTicketORM(
                    ticket_id=task_id,
                    task_id=task_id,
                    reason="银行连续查询 NOT_FOUND，需要人工核对",
                )
            )
    await _event(
        db,
        task_id=task_id,
        operation_id=operation.operation_id,
        request_id=request_id,
        event_type="RECONCILIATION_NOT_FOUND",
        actor=user_id,
        before={"operation": operation.status},
        after={"operation": OperationStatus.UNKNOWN, "attempt": task.attempt_count},
    )
    await db.commit()
    return await task_snapshot(db, task_id, user_id)


async def cancel_task(
    db: AsyncSession,
    bank: BankApi,
    *,
    task_id: str,
    user_id: str,
    request_id: str,
    state_version: int,
) -> dict:
    task = await _owned_task(db, task_id, user_id)
    duplicate = await _deduplicated_task_id(db, request_id)
    if duplicate == task_id:
        operation = await _current_operation(db, task)
        await bank.release_payment_request(
            operation_id=operation.operation_id, subject_user_id=user_id
        )
        return await task_snapshot(db, task_id, user_id)
    await _reserve_version(db, task, state_version)
    operation = await _current_operation(db, task)
    task.cancel_requested = True
    before = operation.status
    if operation.status in {OperationStatus.DRAFT, OperationStatus.READY}:
        operation.status = OperationStatus.CANCELLED
        operation.state_version += 1
        (await _step(db, operation.step_id)).status = StepStatus.CANCELLED
        authorization = await _authorization(db, operation.operation_id)
        if authorization.status not in {
            AuthorizationStatus.CONSUMED,
            AuthorizationStatus.EXPIRED,
            AuthorizationStatus.REVOKED,
        }:
            authorization.status = AuthorizationStatus.REVOKED
        task.phase = TaskPhase.FINISHED
        task.outcome = TaskOutcome.CANCELLED
    task.state_version += 1
    await _event(
        db,
        task_id=task_id,
        operation_id=operation.operation_id,
        request_id=request_id,
        event_type="CANCEL_REQUESTED",
        actor=user_id,
        before={"operation": before},
        after={"operation": operation.status, "cancel_requested": True},
    )
    await db.commit()
    if operation.status == OperationStatus.CANCELLED:
        try:
            await bank.release_payment_request(
                operation_id=operation.operation_id, subject_user_id=user_id
            )
        except BankApiError as error:
            raise TaskError(
                "任务已取消，但收款申请释放待重试", 502, error.code
            ) from error
    return await task_snapshot(db, task_id, user_id)


async def modify_task(
    db: AsyncSession,
    bank: BankApi,
    *,
    task_id: str,
    user_id: str,
    request_id: str,
    state_version: int,
    operation_id: str,
    from_account: str,
    to_account: str,
    amount: float,
    note: str | None,
    recipient_phone: str | None = None,
    note_source: str | None = None,
    commit: bool = True,
) -> dict:
    task = await _owned_task(db, task_id, user_id)
    duplicate = await _deduplicated_task_id(db, request_id)
    if duplicate == task_id:
        return await task_snapshot(db, task_id, user_id)
    await _reserve_version(db, task, state_version)
    old_operation = await _current_operation(db, task)
    if old_operation.status not in {OperationStatus.DRAFT, OperationStatus.READY}:
        raise TaskError("应用受理后参数不可修改", 409, "PARAMETERS_IMMUTABLE")
    if await db.get(TransferOperationORM, operation_id):
        raise TaskError("新的 operation_id 已存在", 409, "OPERATION_ID_CONFLICT")
    note, note_source = _validated_note(note, note_source)
    try:
        amount_fen = yuan_to_fen(amount)
    except ValueError as error:
        raise TaskError(str(error)) from error
    old_plan = await _current_plan(db, task)
    if recipient_phone is None and to_account == old_operation.to_account:
        recipient_phone = old_plan.params.get("recipient_phone")
    try:
        bank_validation = await bank.validate_transfer(
            subject_user_id=user_id,
            from_account=from_account,
            to_account=to_account,
            **({"recipient_phone": recipient_phone} if recipient_phone else {}),
        )
    except BankApiError as error:
        raise TaskError(error.message, error.status_code, error.code) from error

    old_operation.status = OperationStatus.SUPERSEDED
    old_operation.state_version += 1
    old_authorization = await _authorization(db, old_operation.operation_id)
    old_authorization.status = AuthorizationStatus.REVOKED
    (await _step(db, old_operation.step_id)).status = StepStatus.CANCELLED
    old_plan.is_current = False

    new_version = task.current_plan_version + 1
    plan_id, step_id = _id("PLAN"), _id("STEP")
    try:
        canonical_phone = normalize_phone(recipient_phone) if recipient_phone else None
    except ValueError as error:
        raise TaskError(str(error), 422, "INVALID_PHONE") from error
    digest = parameter_digest(from_account, to_account, amount_fen, "CNY", note, canonical_phone)
    risk_level = await _checked_risk_level(
        db,
        user_id=user_id,
        amount_fen=amount_fen,
        daily_transfer_used_fen=bank_validation.daily_transfer_used_fen,
    )
    new_plan = TransferPlanORM(
        plan_id=plan_id,
        task_id=task_id,
        plan_version=new_version,
        params={
            "from_account": from_account,
            "to_account": to_account,
            "amount_fen": amount_fen,
            "currency": "CNY",
            "note": note,
            "note_source": note_source,
            "recipient_phone": canonical_phone,
            "recipient_name": bank_validation.recipient_name,
            "scheduled_at": old_plan.params.get("scheduled_at"),
        },
    )
    db.add(new_plan)
    await db.flush()
    new_step = TransferStepORM(
        step_id=step_id,
        task_id=task_id,
        plan_id=plan_id,
        status=StepStatus.WAITING_AUTHORIZATION,
    )
    db.add(new_step)
    await db.flush()
    new_operation = TransferOperationORM(
        operation_id=operation_id,
        task_id=task_id,
        step_id=step_id,
        plan_version=new_version,
        status=OperationStatus.DRAFT,
        from_account=from_account,
        to_account=to_account,
        amount_fen=amount_fen,
        currency="CNY",
        note=note,
        param_digest=digest,
    )
    db.add(new_operation)
    await db.flush()
    db.add(
        new_authorization(
            operation_id=operation_id,
            user_id=user_id,
            plan_version=new_version,
            param_digest=digest,
            risk_level=risk_level,
        )
    )
    task.current_plan_version = new_version
    task.phase = TaskPhase.WAITING_CONFIRMATION
    task.state_version += 1
    await _event(
        db,
        task_id=task_id,
        operation_id=operation_id,
        request_id=request_id,
        event_type="PARAMETERS_MODIFIED",
        actor=user_id,
        before={"operation_id": old_operation.operation_id},
        after={"operation_id": operation_id, "plan_version": new_version},
    )
    if commit:
        await db.commit()
    else:
        await db.flush()
    return await task_snapshot(db, task_id, user_id)


async def verify_task(
    db: AsyncSession,
    bank: BankApi,
    *,
    task_id: str,
    user_id: str,
    request_id: str,
    state_version: int,
    challenge_id: str,
    proof: str,
    method: str = "totp",
) -> dict:
    """红色路径 MFA 核验：TOTP 或短信验证码通过后将授权推进到 VALID，任务推进到 READY。

    Args:
        challenge_id: TOTP 无状态，固定传占位值即可；短信方式须传 ``/auth/sms/send``
            返回的挑战 ID，该挑战必须以本任务 ID 作为 reference_id 获取。
        proof: 验证器 App 的 6 位动态码，或短信收到的 6 位验证码。
        method: ``totp``（默认）或 ``sms``。
    """
    from ...integrations.authorization import verify_consent
    task = await _owned_task(db, task_id, user_id)
    duplicate = await _deduplicated_task_id(db, request_id)
    if duplicate == task_id:
        return await task_snapshot(db, task_id, user_id)
    await _reserve_version(db, task, state_version)

    if task.phase != TaskPhase.WAITING_VERIFICATION:
        raise TaskError("任务当前不需要验证", 409, "VERIFICATION_NOT_REQUIRED")

    operation = await _current_operation(db, task)
    authorization = await _authorization(db, operation.operation_id)
    if authorization.status != AuthorizationStatus.PENDING_VERIFICATION:
        raise TaskError("授权不在待验证状态", 409, "AUTHORIZATION_WRONG_STATE")

    # 由模拟银行内部 API 校验第二因素；编排不直接读取银行客户表。
    try:
        if method == "sms":
            await bank.verify_sms(subject_user_id=user_id, proof=proof,
                                  challenge_id=challenge_id, reference_id=task_id)
        else:
            await bank.verify_totp(subject_user_id=user_id, proof=proof)
    except BankResponseUnknown as error:
        raise TaskError("验证结果未知，请等待新验证码后重试", 503, error.code) from error
    except BankApiError as error:
        raise TaskError(error.message, error.status_code, error.code) from error

    try:
        verify_consent(authorization)
    except AuthorizationError as error:
        raise TaskError(error.message, 409, error.code) from error

    plan = await _current_plan(db, task)
    operation.status = OperationStatus.READY
    (await _step(db, operation.step_id)).status = StepStatus.READY
    task.phase = (
        TaskPhase.WAITING_EXECUTION
        if plan.params.get("scheduled_at")
        else TaskPhase.READY
    )
    operation.state_version += 1
    task.state_version += 1
    await _event(
        db,
        task_id=task_id,
        operation_id=operation.operation_id,
        request_id=request_id,
        event_type="MFA_VERIFIED",
        actor=user_id,
        before={"phase": TaskPhase.WAITING_VERIFICATION, "authorization": AuthorizationStatus.PENDING_VERIFICATION},
        after={"phase": task.phase, "authorization": AuthorizationStatus.VALID, "mfa_method": method},
    )
    await db.commit()
    return await task_snapshot(db, task_id, user_id)


async def claim_task(
    db: AsyncSession,
    *,
    task_id: str,
    operator_id: str,
    request_id: str,
    lease_minutes: int = 15,
) -> dict:
    task = await db.get(TransferTaskORM, task_id)
    if task is None:
        raise TaskError("任务不存在", 404, "TASK_NOT_FOUND")
    if task.control_mode != ControlMode.HANDOFF_PENDING:
        raise TaskError("任务当前不能被人工领取", 409, "CLAIM_NOT_ALLOWED")
    task.control_mode = ControlMode.HUMAN
    task.owner_id = operator_id
    task.control_epoch += 1
    task.state_version += 1
    task.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=lease_minutes)
    ticket = await db.get(HumanTicketORM, task_id)
    if ticket:
        ticket.status = "CLAIMED"
        ticket.assignee_id = operator_id
    await _event(
        db,
        task_id=task_id,
        operation_id=None,
        request_id=request_id,
        event_type="HUMAN_CLAIMED",
        actor=operator_id,
        before={"control_mode": ControlMode.HANDOFF_PENDING},
        after={"control_mode": ControlMode.HUMAN, "control_epoch": task.control_epoch},
    )
    await db.commit()
    return await task_snapshot(db, task_id, task.user_id)


async def return_task(
    db: AsyncSession,
    *,
    task_id: str,
    operator_id: str,
    request_id: str,
) -> dict:
    task = await db.get(TransferTaskORM, task_id)
    if task is None:
        raise TaskError("任务不存在", 404, "TASK_NOT_FOUND")
    if task.control_mode != ControlMode.HUMAN or task.owner_id != operator_id:
        raise TaskError("只有当前处理人可以归还任务", 403, "RETURN_FORBIDDEN")
    task.control_mode = ControlMode.HANDOFF_PENDING
    task.owner_id = None
    task.lease_expires_at = None
    task.control_epoch += 1
    task.state_version += 1
    await _event(
        db,
        task_id=task_id,
        operation_id=None,
        request_id=request_id,
        event_type="HUMAN_RETURNED",
        actor=operator_id,
        before={"control_mode": ControlMode.HUMAN},
        after={"control_mode": ControlMode.HANDOFF_PENDING, "control_epoch": task.control_epoch},
    )
    await db.commit()
    return await task_snapshot(db, task_id, task.user_id)


async def task_events(
    db: AsyncSession, task_id: str, user_id: str, since_event_id: str | None = None
) -> list[dict]:
    await _owned_task(db, task_id, user_id)
    statement = (
        select(StateEventORM)
        .where(StateEventORM.task_id == task_id)
        .order_by(StateEventORM.created_at, StateEventORM.event_id)
    )
    events = (await db.execute(statement)).scalars().all()
    if since_event_id:
        try:
            index = next(i for i, event in enumerate(events) if event.event_id == since_event_id)
            events = events[index + 1 :]
        except StopIteration:
            pass
    return [
        {
            "event_id": event.event_id,
            "task_id": event.task_id,
            "operation_id": event.operation_id,
            "event_type": event.event_type,
            "before": event.before_state,
            "after": event.after_state,
            "timestamp": event.created_at,
        }
        for event in events
    ]


async def task_snapshot(db: AsyncSession, task_id: str, user_id: str) -> dict:
    task = await _owned_task(db, task_id, user_id)
    operation = await _current_operation(db, task)
    plan = await _current_plan(db, task)
    authorization = await _authorization(db, operation.operation_id)
    step = await _step(db, operation.step_id)
    risk = (
        await db.execute(
            select(RiskReservationORM).where(
                RiskReservationORM.operation_id == operation.operation_id
            )
        )
    ).scalar_one_or_none()
    allowed: list[str] = []
    if task.control_mode == ControlMode.AUTO and task.outcome == TaskOutcome.NONE:
        if task.phase == TaskPhase.WAITING_CONFIRMATION:
            allowed = ["confirm", "modify", "cancel"]
        elif task.phase == TaskPhase.WAITING_VERIFICATION:
            allowed = ["verify", "modify", "cancel"]
        elif task.phase == TaskPhase.WAITING_EXECUTION:
            allowed = ["modify", "cancel"]
        elif task.phase == TaskPhase.READY:
            allowed = ["accept", "modify", "cancel"]
        elif task.phase == TaskPhase.RECONCILING:
            allowed = ["reconcile", "cancel"]
        elif task.phase == TaskPhase.EXECUTING:
            allowed = ["cancel"]

    confirmation_card = None
    if operation.status in {OperationStatus.DRAFT, OperationStatus.READY}:
        confirmation_card = {
            "card_id": f"CARD-{operation.operation_id}",
            "plan_version": operation.plan_version,
            "from_account": operation.from_account,
            "to_account": operation.to_account,
            "recipient_name": plan.params.get("recipient_name", operation.to_account),
            "amount": fen_to_yuan(operation.amount_fen),
            "currency": operation.currency,
            "note": operation.note,
            "note_source": plan.params.get("note_source"),
            "recipient_phone_masked": (
                mask_phone(plan.params["recipient_phone"])
                if plan.params.get("recipient_phone") else None
            ),
            "risk_level": authorization.risk_level,
            "scheduled_at": plan.params.get("scheduled_at"),
        }
    message = {
        TaskPhase.WAITING_CONFIRMATION: "请核对转账信息并确认",
        TaskPhase.WAITING_VERIFICATION: "请完成身份验证",
        TaskPhase.WAITING_EXECUTION: "定时转账已确认，等待到达执行时间",
        TaskPhase.READY: "授权有效，可以提交银行",
        TaskPhase.EXECUTING: "转账正在处理中",
        TaskPhase.RECONCILING: "结果待核对，请勿重复转账",
        TaskPhase.WAITING_HUMAN: "等待人工处理",
        TaskPhase.FINISHED: (
            "转账成功"
            if task.outcome == TaskOutcome.SUCCEEDED
            else "转账已取消"
            if task.outcome == TaskOutcome.CANCELLED
            else "转账失败"
        ),
    }.get(TaskPhase(task.phase), "正在整理转账信息")
    return {
        "task_id": task.task_id,
        "phase": task.phase,
        "outcome": task.outcome,
        "state_version": task.state_version,
        "plan_version": task.current_plan_version,
        "control_mode": task.control_mode,
        "control_epoch": task.control_epoch,
        "cancel_requested": task.cancel_requested,
        "allowed_actions": allowed,
        "message": message,
        "scheduled_at": plan.params.get("scheduled_at"),
        "step": {"step_id": step.step_id, "status": step.status},
        "operation": {
            "operation_id": operation.operation_id,
            "status": operation.status,
            "amount": fen_to_yuan(operation.amount_fen),
            "amount_fen": operation.amount_fen,
            "currency": operation.currency,
            "from_account": operation.from_account,
            "to_account": operation.to_account,
            "bank_status": operation.bank_status,
            "bank_tx_id": operation.bank_tx_id,
            "failure_code": operation.failure_code,
            "note": operation.note,
        },
        "authorization": {
            "authorization_id": authorization.authorization_id,
            "status": authorization.status,
            "risk_level": authorization.risk_level,
        },
        "risk_reservation": (
            {
                "reservation_id": risk.reservation_id,
                "status": risk.status,
                "amount_fen": risk.amount_fen,
            }
            if risk
            else None
        ),
        "confirmation_card": confirmation_card,
    }
