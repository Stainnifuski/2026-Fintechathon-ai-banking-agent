"""蛋糕店商品与订单的公开 HTTP 契约。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CakeProductView(BaseModel):
    product_id: str
    name: str
    description: str
    price_fen: int
    price: float
    currency: Literal["CNY"]
    image_key: str


class CakeOrderCreate(BaseModel):
    product_id: str = Field(min_length=1, max_length=32)
    quantity: int = Field(ge=1, le=20)
    idempotency_key: str = Field(min_length=8, max_length=64)


class CakePaymentRequestSummary(BaseModel):
    payment_request_id: str
    requester_name: str
    payer_name: str
    amount: float
    amount_fen: int
    currency: Literal["CNY"]
    note: str | None
    status: str
    state_version: int


class CakeOrderView(BaseModel):
    order_id: str
    customer_user_id: str
    product_id: str
    product_name: str
    product_description: str
    quantity: int
    unit_price_fen: int
    unit_price: float
    refunded_fen: int = 0
    remaining_refundable_fen: int = 0
    total_fen: int
    total: float
    currency: Literal["CNY"]
    payment_request_id: str | None
    payment_request: CakePaymentRequestSummary | None
    status: Literal["PENDING_PAYMENT", "COMPLETED", "PAYMENT_CLOSED", "REFUNDED", "PARTIALLY_REFUNDED"]
    state_version: int
    created_at: datetime
    updated_at: datetime


class CakeRefundAction(BaseModel):
    state_version: int = Field(ge=1)
    amount_fen: int | None = Field(default=None,gt=0,strict=True)
    operation_id: str | None = Field(default=None,min_length=1,max_length=64)
