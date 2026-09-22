"""订阅管理公开 HTTP 契约。"""

from datetime import datetime

from pydantic import BaseModel, Field


class SubscriptionAction(BaseModel):
    state_version: int = Field(ge=1)


class SubscriptionChargeAction(SubscriptionAction):
    idempotency_key: str = Field(min_length=8, max_length=64)


class SubscriptionView(BaseModel):
    subscription_id: str
    debit_account_id: str
    debit_account_label: str
    merchant_name: str
    amount: float
    amount_fen: int
    currency: str
    billing_cycle: str
    next_charge_at: datetime
    status: str
    state_version: int
    source_transaction_id: str | None = None
    cancelled_at: datetime | None = None


class SubscriptionChargeResult(BaseModel):
    subscription: SubscriptionView
    transaction_id: str
    charged_amount: float
    duplicate: bool
    message: str
