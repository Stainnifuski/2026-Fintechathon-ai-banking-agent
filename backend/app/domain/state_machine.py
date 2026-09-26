"""转账任务状态机的领域枚举、守卫与阶段投影。"""

from enum import StrEnum


class TaskPhase(StrEnum):
    PLANNING = "PLANNING"
    WAITING_INPUT = "WAITING_INPUT"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    WAITING_VERIFICATION = "WAITING_VERIFICATION"
    WAITING_EXECUTION = "WAITING_EXECUTION"
    READY = "READY"
    EXECUTING = "EXECUTING"
    RECONCILING = "RECONCILING"
    WAITING_HUMAN = "WAITING_HUMAN"
    FINISHED = "FINISHED"


class TaskOutcome(StrEnum):
    NONE = "NONE"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PARTIAL = "PARTIAL"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    WAITING_INPUT = "WAITING_INPUT"
    WAITING_AUTHORIZATION = "WAITING_AUTHORIZATION"
    READY = "READY"
    RUNNING = "RUNNING"
    RECONCILING = "RECONCILING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class OperationStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    ACCEPTED = "ACCEPTED"
    SUBMITTING = "SUBMITTING"
    PROCESSING = "PROCESSING"
    UNKNOWN = "UNKNOWN"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class BankOperationStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AuthorizationStatus(StrEnum):
    PENDING_CONSENT = "PENDING_CONSENT"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VALID = "VALID"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class ControlMode(StrEnum):
    AUTO = "AUTO"
    HANDOFF_PENDING = "HANDOFF_PENDING"
    HUMAN = "HUMAN"


class RiskReservationStatus(StrEnum):
    HELD = "HELD"
    SETTLED = "SETTLED"
    RELEASED = "RELEASED"


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    DISPATCHING = "DISPATCHING"
    SENT = "SENT"
    RETRY = "RETRY"


TERMINAL_OPERATIONS = {
    OperationStatus.SUCCEEDED,
    OperationStatus.FAILED,
    OperationStatus.CANCELLED,
    OperationStatus.SUPERSEDED,
}
IN_FLIGHT_OPERATIONS = {
    OperationStatus.ACCEPTED,
    OperationStatus.SUBMITTING,
    OperationStatus.PROCESSING,
}
TERMINAL_STEPS = {
    StepStatus.SUCCEEDED,
    StepStatus.FAILED,
    StepStatus.CANCELLED,
    StepStatus.SKIPPED,
}

OPERATION_TRANSITIONS: dict[OperationStatus, set[OperationStatus]] = {
    OperationStatus.DRAFT: {
        OperationStatus.READY,
        OperationStatus.CANCELLED,
        OperationStatus.SUPERSEDED,
    },
    OperationStatus.READY: {
        OperationStatus.DRAFT,
        OperationStatus.ACCEPTED,
        OperationStatus.CANCELLED,
        OperationStatus.SUPERSEDED,
    },
    OperationStatus.ACCEPTED: {OperationStatus.SUBMITTING},
    OperationStatus.SUBMITTING: {
        OperationStatus.PROCESSING,
        OperationStatus.UNKNOWN,
        OperationStatus.SUCCEEDED,
        OperationStatus.FAILED,
    },
    OperationStatus.PROCESSING: {
        OperationStatus.UNKNOWN,
        OperationStatus.SUCCEEDED,
        OperationStatus.FAILED,
    },
    OperationStatus.UNKNOWN: {
        OperationStatus.SUBMITTING,
        OperationStatus.PROCESSING,
        OperationStatus.SUCCEEDED,
        OperationStatus.FAILED,
    },
}


class InvalidTransition(ValueError):
    """领域状态转移不合法。"""


def ensure_operation_transition(current: str, target: OperationStatus) -> None:
    current_status = OperationStatus(current)
    if target not in OPERATION_TRANSITIONS.get(current_status, set()):
        raise InvalidTransition(f"操作不能从 {current_status} 转为 {target}")


def project_task_phase(
    operation_statuses: list[str],
    *,
    control_mode: str,
    waiting_verification: bool = False,
    waiting_confirmation: bool = False,
    ready: bool = False,
    settled: bool = False,
) -> TaskPhase:
    """按设计文档 D.3 的优先级计算任务展示阶段。"""
    statuses = {OperationStatus(value) for value in operation_statuses}
    if settled:
        return TaskPhase.FINISHED
    if ControlMode(control_mode) != ControlMode.AUTO:
        return TaskPhase.WAITING_HUMAN
    if OperationStatus.UNKNOWN in statuses:
        return TaskPhase.RECONCILING
    if statuses & IN_FLIGHT_OPERATIONS:
        return TaskPhase.EXECUTING
    if waiting_verification:
        return TaskPhase.WAITING_VERIFICATION
    if waiting_confirmation:
        return TaskPhase.WAITING_CONFIRMATION
    if ready:
        return TaskPhase.READY
    return TaskPhase.PLANNING


def yuan_to_fen(amount) -> int:
    """兼容旧数字输入；字符串/Decimal为精确输入，拒绝超精度与非有限值。"""
    from decimal import Decimal, InvalidOperation
    try:
        value = Decimal(str(amount))
        if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -2:
            raise ValueError("金额必须大于 0 且最多两位小数")
        fen = value * 100
        if fen > 2_000_000_000:
            raise ValueError("金额超出支持范围")
        return int(fen)
    except (InvalidOperation, TypeError) as error:
        raise ValueError("无效金额") from error


def fen_to_yuan(amount_fen: int) -> float:
    return amount_fen / 100
