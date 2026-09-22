"""任务状态机 HTTP 契约。"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class TransferTaskCreate(BaseModel):
    request_id: str = Field(min_length=8, max_length=64)
    operation_id: str = Field(min_length=8, max_length=64)
    from_account: str
    to_account: str
    amount: Decimal = Field(gt=0, decimal_places=2)
    note: str | None = Field(default=None, max_length=256)


class TaskActionRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=64)
    state_version: int = Field(ge=1)


class ConfirmTaskRequest(TaskActionRequest):
    plan_version: int = Field(ge=1)


class ModifyTaskRequest(TaskActionRequest):
    operation_id: str = Field(min_length=8, max_length=64)
    from_account: str
    to_account: str
    amount: Decimal = Field(gt=0, decimal_places=2)
    note: str | None = Field(default=None, max_length=256)


class VerificationRequest(TaskActionRequest):
    challenge_id: str
    proof: str


class HumanActionRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=64)
    operator_id: str
    lease_minutes: int = Field(default=15, ge=1, le=120)


class StepView(BaseModel):
    step_id: str
    status: str


class OperationView(BaseModel):
    operation_id: str
    status: str
    amount: float
    amount_fen: int
    currency: str
    from_account: str
    to_account: str
    bank_status: str | None = None
    bank_tx_id: str | None = None
    failure_code: str | None = None


class AuthorizationView(BaseModel):
    authorization_id: str
    status: str
    risk_level: str


class RiskReservationView(BaseModel):
    reservation_id: str
    status: str
    amount_fen: int


class ConfirmationCard(BaseModel):
    card_id: str
    plan_version: int
    from_account: str
    to_account: str
    recipient_name: str
    amount: float
    currency: str
    note: str | None = None
    risk_level: str


class TaskSnapshot(BaseModel):
    task_id: str
    phase: str
    outcome: str
    state_version: int
    plan_version: int
    control_mode: str
    control_epoch: int
    cancel_requested: bool
    allowed_actions: list[str]
    message: str
    step: StepView
    operation: OperationView
    authorization: AuthorizationView
    risk_reservation: RiskReservationView | None = None
    confirmation_card: ConfirmationCard | None = None


class StateEventView(BaseModel):
    event_id: str
    task_id: str
    operation_id: str | None = None
    event_type: str
    before: dict
    after: dict
    timestamp: datetime


class ChatTaskCreate(BaseModel):
    request_id: str = Field(min_length=8, max_length=64)
    operation_id: str = Field(min_length=8, max_length=64)
    message: str = Field(min_length=1, max_length=500)

    session_id: str | None = Field(default=None, min_length=8, max_length=64)
    session_version: int = Field(default=0, ge=0)
    candidate_id: str | None = Field(default=None, max_length=64)
    task_id: str | None = Field(default=None, max_length=40)
    state_version: int | None = Field(default=None, ge=1)


class CandidateView(BaseModel):
    candidate_id: str
    kind: Literal["recipient", "payer"]
    label: str


class ChatReply(BaseModel):
    session_id: str
    session_version: int
    kind: Literal["clarification", "plan", "modification", "status", "unsupported", "error"]
    message: str
    mode: str
    candidates: list[CandidateView] = Field(default_factory=list)
    task: TaskSnapshot | None = None
