"""应用任务状态机与模拟银行操作的持久化模型。"""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class TransferTaskORM(TimestampMixin, Base):
    __tablename__ = "transfer_tasks"

    task_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.user_id"), index=True)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="NONE")
    control_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="AUTO")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    control_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TransferPlanORM(TimestampMixin, Base):
    __tablename__ = "transfer_plans"

    plan_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("transfer_tasks.task_id"), index=True
    )
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TransferStepORM(TimestampMixin, Base):
    __tablename__ = "transfer_steps"

    step_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("transfer_tasks.task_id"), index=True
    )
    plan_id: Mapped[str] = mapped_column(String(40), ForeignKey("transfer_plans.plan_id"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="TRANSFER")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    depends_on: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class TransferOperationORM(TimestampMixin, Base):
    """5号维护的操作观察对象，不是银行账本。"""

    __tablename__ = "transfer_operations"

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("transfer_tasks.task_id"), index=True
    )
    step_id: Mapped[str] = mapped_column(String(40), ForeignKey("transfer_steps.step_id"))
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    from_account: Mapped[str] = mapped_column(String(32), nullable=False)
    to_account: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    param_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    bank_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    bank_tx_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AuthorizationORM(TimestampMixin, Base):
    __tablename__ = "transfer_authorizations"

    authorization_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    operation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("transfer_operations.operation_id"), unique=True
    )
    user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    verification_level: Mapped[str] = mapped_column(String(16), nullable=False, default="CONSENT")
    param_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RiskReservationORM(TimestampMixin, Base):
    __tablename__ = "risk_reservations"

    reservation_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    operation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("transfer_operations.operation_id"), unique=True
    )
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    amount_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class OutboxEntryORM(TimestampMixin, Base):
    __tablename__ = "transfer_outbox"

    outbox_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    operation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("transfer_operations.operation_id"), unique=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    control_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StateEventORM(Base):
    __tablename__ = "transfer_state_events"

    event_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("transfer_tasks.task_id"), index=True
    )
    operation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    before_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    after_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HumanTicketORM(TimestampMixin, Base):
    __tablename__ = "human_tickets"

    ticket_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("transfer_tasks.task_id"), unique=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="OPEN")
    assignee_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(String(256), nullable=False)


class BankOperationORM(TimestampMixin, Base):
    """4号维护的银行受理事实。"""

    __tablename__ = "bank_operations"

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    param_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    from_account: Mapped[str] = mapped_column(String(32), nullable=False)
    to_account: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    bank_tx_id: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
