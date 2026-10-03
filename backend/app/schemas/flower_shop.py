"""花店商品与订单的公开 HTTP 契约。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class FlowerProductView(BaseModel):
    product_id: str
    name: str
    description: str
    price_fen: int
    price: float
    currency: Literal["CNY"]
    image_key: str


class FlowerOrderCreate(BaseModel):
    product_id: str = Field(min_length=1, max_length=32)
    quantity: int = Field(ge=1, le=20)
    idempotency_key: str = Field(min_length=8, max_length=64)


class FlowerPaymentRequestSummary(BaseModel):
    payment_request_id: str
    requester_name: str
    payer_name: str
    amount: float
    amount_fen: int
    currency: Literal["CNY"]
    note: str | None
    status: str
    state_version: int


class FlowerOrderView(BaseModel):
    order_id: str
    customer_user_id: str
    product_id: str
    product_name: str
    product_description: str
    quantity: int
    unit_price_fen: int
    unit_price: float
    total_fen: int
    total: float
    currency: Literal["CNY"]
    payment_request_id: str | None
    payment_request: FlowerPaymentRequestSummary | None
    status: Literal["PENDING_PAYMENT", "COMPLETED", "PAYMENT_CLOSED", "REFUNDED"]
    state_version: int
    created_at: datetime
    updated_at: datetime


class FlowerRefundAction(BaseModel):
    state_version: int = Field(ge=1)
