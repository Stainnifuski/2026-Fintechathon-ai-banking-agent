"""收款申请的公开 HTTP 契约。"""

from datetime import datetime

from pydantic import BaseModel, Field


class PaymentRequestCreate(BaseModel):
    payer_user_id: str = Field(min_length=1, max_length=32)
    receive_account_id: str = Field(min_length=1, max_length=32)
    amount: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    note: str | None = Field(default=None, max_length=256)
    idempotency_key: str = Field(min_length=8, max_length=64)


class PaymentRequestBatchCreate(BaseModel):
    payer_user_ids: list[str] = Field(min_length=1, max_length=20)
    receive_account_id: str = Field(min_length=1, max_length=32)
    amount: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    split_equally: bool = False
    note: str | None = Field(default=None, max_length=256)
    idempotency_key: str = Field(min_length=8, max_length=64)


class PaymentRequestAction(BaseModel):
    state_version: int = Field(ge=1)


class PaymentRequestAcceptAction(PaymentRequestAction):
    payer_account_id: str = Field(min_length=1, max_length=32)


class PaymentRequestView(BaseModel):
    payment_request_id: str
    requester_user_id: str
    requester_name: str
    payer_user_id: str
    payer_name: str
    receive_account_id: str
    amount: float
    amount_fen: int
    currency: str
    note: str | None = None
    status: str
    state_version: int
    expires_at: datetime
    created_at: datetime
    responded_at: datetime | None = None
