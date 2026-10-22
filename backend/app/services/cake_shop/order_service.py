"""蛋糕店订单编排：创建银行收款申请，并以其状态对账订单。"""
from ..audit.spans import audited

import hashlib
import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ...database.connection import AsyncSessionLocal
from ...models.cake_order import CakeOrderORM
from ..bank.payment_request_service import (
    BankError,
    create_payment_request,
    list_payment_requests,
)
from ..bank.merchant_refund_service import refund_merchant_payment
from .catalog import PRODUCTS_BY_ID

CAKE_MERCHANT_USER_ID = "U008"
CAKE_MERCHANT_ACCOUNT_ID = "A009"
CAKE_MERCHANT_NAME = "甜时光蛋糕店"

PENDING_PAYMENT = "PENDING_PAYMENT"
COMPLETED = "COMPLETED"
PAYMENT_CLOSED = "PAYMENT_CLOSED"
REFUNDED = "REFUNDED"


class CakeShopError(Exception):
    def __init__(self, message: str, code: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


def _digest(product_id: str, quantity: int) -> str:
    payload = json.dumps(
        {"product_id": product_id, "quantity": quantity},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _payment_summary(payment: dict | None) -> dict | None:
    if payment is None:
        return None
    return {
        "payment_request_id": payment["payment_request_id"],
        "requester_name": payment["requester_name"],
        "payer_name": payment["payer_name"],
        "amount": payment["amount"],
        "amount_fen": payment["amount_fen"],
        "currency": payment["currency"],
        "note": payment["note"],
        "status": payment["status"],
        "state_version": payment["state_version"],
    }


def _view(order: CakeOrderORM, payment: dict | None = None) -> dict:
    return {
        "order_id": order.order_id,
        "customer_user_id": order.customer_user_id,
        "product_id": order.product_id,
        "product_name": order.product_name,
        "product_description": order.product_description,
        "quantity": order.quantity,
        "unit_price_fen": order.unit_price_fen,
        "unit_price": order.unit_price_fen / 100,
        "total_fen": order.total_fen,
        "total": order.total_fen / 100,
        "currency": order.currency,
        "payment_request_id": order.payment_request_id,
        "payment_request": _payment_summary(payment),
        "status": order.status,
        "refunded_fen": (payment or {}).get("refunded_fen",order.total_fen if order.status==REFUNDED else 0),
        "remaining_refundable_fen": max(0,order.total_fen-(payment or {}).get("refunded_fen",order.total_fen if order.status==REFUNDED else 0)),
        "state_version": order.state_version,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }


async def _find_payment_request(customer_user_id: str, payment_request_id: str) -> dict | None:
    requests = await list_payment_requests(user_id=customer_user_id, role="incoming")
    return next(
        (item for item in requests if item["payment_request_id"] == payment_request_id),
        None,
    )


def effective_order_status(stored_status: str, payment: dict | None) -> str:
    """页面对账与NOVA投影共用状态规则；函数不修改订单。"""
    if payment and payment.get('refunded_fen',0)>0:
        return REFUNDED if payment['refunded_fen']>=payment['amount_fen'] else 'PARTIALLY_REFUNDED'
    if payment is None or stored_status in {COMPLETED, PAYMENT_CLOSED, REFUNDED, 'PARTIALLY_REFUNDED'}:
        return stored_status
    if payment["status"] == "PAID":
        return COMPLETED
    if payment["status"] in {"REJECTED", "CANCELLED", "EXPIRED"}:
        return PAYMENT_CLOSED
    return PENDING_PAYMENT


async def _reconcile(order: CakeOrderORM) -> tuple[bool, dict | None]:
    if not order.payment_request_id:
        return False, None
    payment = await _find_payment_request(order.customer_user_id, order.payment_request_id)
    if payment is None:
        return False, None
    target = effective_order_status(order.status, payment)
    if target == order.status:
        return False, payment
    order.status = target
    order.state_version += 1
    return True, payment


@audited("shops")
async def create_order(
    *,
    customer_user_id: str,
    product_id: str,
    quantity: int,
    idempotency_key: str,
) -> dict:
    product = PRODUCTS_BY_ID.get(product_id)
    if product is None:
        raise CakeShopError("商品不存在或已下架", "PRODUCT_NOT_FOUND", 404)
    if not 1 <= quantity <= 20:
        raise CakeShopError("购买数量必须在 1 到 20 之间", "INVALID_QUANTITY")
    request_digest = _digest(product_id, quantity)

    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(
                select(CakeOrderORM).where(
                    CakeOrderORM.customer_user_id == customer_user_id,
                    CakeOrderORM.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if order is not None:
            if order.request_digest != request_digest:
                raise CakeShopError(
                    "幂等键已用于不同的蛋糕店订单",
                    "IDEMPOTENCY_CONFLICT",
                    409,
                )
        else:
            order = CakeOrderORM(
                order_id=f"CO{uuid4().hex[:18].upper()}",
                customer_user_id=customer_user_id,
                product_id=product.product_id,
                product_name=product.name,
                product_description=product.description,
                quantity=quantity,
                unit_price_fen=product.price_fen,
                total_fen=product.price_fen * quantity,
                currency="CNY",
                status=PENDING_PAYMENT,
                state_version=1,
                request_digest=request_digest,
                idempotency_key=idempotency_key,
            )
            session.add(order)
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                duplicate = (
                    await session.execute(
                        select(CakeOrderORM).where(
                            CakeOrderORM.customer_user_id == customer_user_id,
                            CakeOrderORM.idempotency_key == idempotency_key,
                        )
                    )
                ).scalar_one_or_none()
                if duplicate is None or duplicate.request_digest != request_digest:
                    raise CakeShopError(
                        "订单创建冲突，请使用原幂等键重试",
                        "ORDER_CONFLICT",
                        409,
                    ) from error
                order = duplicate
            await session.refresh(order)

        order_id = order.order_id
        total_fen = order.total_fen
        note = f"蛋糕订单 {order.order_id} · {order.product_name} × {order.quantity}"
        existing_payment_request_id = order.payment_request_id

    if existing_payment_request_id is None:
        try:
            payment = await create_payment_request(
                requester_user_id=CAKE_MERCHANT_USER_ID,
                payer_user_id=customer_user_id,
                receive_account_id=CAKE_MERCHANT_ACCOUNT_ID,
                amount=total_fen / 100,
                note=note,
                idempotency_key=f"cake-{order_id}",
            )
        except BankError as error:
            raise CakeShopError(error.message, error.code, error.status_code) from error

        async with AsyncSessionLocal() as session:
            order = (
                await session.execute(
                    select(CakeOrderORM)
                    .where(CakeOrderORM.order_id == order_id)
                    .with_for_update()
                )
            ).scalar_one()
            if order.payment_request_id is None:
                order.payment_request_id = payment["payment_request_id"]
                order.state_version += 1
                await session.commit()
            elif order.payment_request_id != payment["payment_request_id"]:
                raise CakeShopError("订单付款申请关联冲突", "PAYMENT_LINK_CONFLICT", 409)
            await session.refresh(order)
            return _view(order, payment)

    return await get_order(customer_user_id=customer_user_id, order_id=order_id)


@audited("shops")
async def get_order(*, customer_user_id: str, order_id: str) -> dict:
    async with AsyncSessionLocal() as session:
        order = await session.get(CakeOrderORM, order_id)
        if order is None:
            raise CakeShopError("蛋糕店订单不存在", "ORDER_NOT_FOUND", 404)
        if order.customer_user_id != customer_user_id:
            raise CakeShopError("无权查看该蛋糕店订单", "ORDER_FORBIDDEN", 403)
        changed, payment = await _reconcile(order)
        if changed:
            await session.commit()
            await session.refresh(order)
        return _view(order, payment)


@audited("shops")
async def list_orders(*, customer_user_id: str) -> list[dict]:
    async with AsyncSessionLocal() as session:
        orders = (
            await session.execute(
                select(CakeOrderORM)
                .where(CakeOrderORM.customer_user_id == customer_user_id)
                .order_by(CakeOrderORM.created_at.desc())
            )
        ).scalars().all()
        changed = False
        payments: dict[str, dict | None] = {}
        for order in orders:
            order_changed, payment = await _reconcile(order)
            payments[order.order_id] = payment
            changed = order_changed or changed
        if changed:
            await session.commit()
            for order in orders:
                await session.refresh(order)
        return [_view(order, payments[order.order_id]) for order in orders]


@audited("shops")
async def list_merchant_orders(*, merchant_user_id: str) -> list[dict]:
    if merchant_user_id != CAKE_MERCHANT_USER_ID:
        raise CakeShopError("只有蛋糕店商户可以查看商户订单", "MERCHANT_FORBIDDEN", 403)
    async with AsyncSessionLocal() as session:
        orders = (
            await session.execute(
                select(CakeOrderORM).order_by(CakeOrderORM.created_at.desc())
            )
        ).scalars().all()
        changed = False
        payments: dict[str, dict | None] = {}
        for order in orders:
            order_changed, payment = await _reconcile(order)
            payments[order.order_id] = payment
            changed = order_changed or changed
        if changed:
            await session.commit()
            for order in orders:
                await session.refresh(order)
        return [_view(order, payments[order.order_id]) for order in orders]


@audited("shops")
async def refund_order(
    *,
    merchant_user_id: str,
    order_id: str,
    state_version: int,
    amount_fen: int | None = None,
    operation_id: str | None = None,
) -> dict:
    if merchant_user_id != CAKE_MERCHANT_USER_ID:
        raise CakeShopError("只有蛋糕店商户可以发起退款", "MERCHANT_FORBIDDEN", 403)
    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(
                select(CakeOrderORM)
                .where(CakeOrderORM.order_id == order_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if order is None:
            raise CakeShopError("蛋糕店订单不存在", "ORDER_NOT_FOUND", 404)
        changed, payment = await _reconcile(order)
        if changed:
            await session.commit()
            await session.refresh(order)
        if order.status == REFUNDED:
            return _view(order, payment)
        if order.state_version != state_version:
            raise CakeShopError("订单状态已变化，请刷新后重试", "STALE_STATE_VERSION", 409)
        if order.status not in (COMPLETED,'PARTIALLY_REFUNDED') or not order.payment_request_id:
            raise CakeShopError("只有已完成付款的订单可以退款", "ORDER_NOT_REFUNDABLE", 409)
        payment_request_id = order.payment_request_id
        expected_refunded=(payment or {}).get("refunded_fen",0)

    try:
        refunded = await refund_merchant_payment(
            payment_request_id=payment_request_id,
            merchant_user_id=merchant_user_id,
            operation_id=operation_id or f"CAKE-REFUND-{order_id}",
            amount_fen=amount_fen,
            expected_refunded_fen=expected_refunded,
        )
    except BankError as error:
        raise CakeShopError(error.message, error.code, error.status_code) from error

    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(
                select(CakeOrderORM)
                .where(CakeOrderORM.order_id == order_id)
                .with_for_update()
            )
        ).scalar_one()
        if order.status != REFUNDED:
            if order.status not in (COMPLETED,'PARTIALLY_REFUNDED'):
                raise CakeShopError("订单退款状态冲突", "REFUND_STATE_CONFLICT", 409)
            order.status = REFUNDED if refunded.get('remaining_fen',0)==0 else 'PARTIALLY_REFUNDED'
            order.state_version += 1
            await session.commit()
            await session.refresh(order)
        payment = await _find_payment_request(
            order.customer_user_id,
            payment_request_id,
        )
        return _view(order, payment)
