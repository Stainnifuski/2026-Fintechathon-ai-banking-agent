"""花店订单编排：创建银行收款申请，并以其状态对账订单。"""

import hashlib
import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ...database.connection import AsyncSessionLocal
from ...models.flower_order import FlowerOrderORM
from ..bank.payment_request_service import (
    BankError,
    create_payment_request,
    list_payment_requests,
)
from ..bank.merchant_refund_service import refund_merchant_payment
from .catalog import PRODUCTS_BY_ID

FLOWER_MERCHANT_USER_ID = "U007"
FLOWER_MERCHANT_ACCOUNT_ID = "A008"
FLOWER_MERCHANT_NAME = "花语鲜花店"

PENDING_PAYMENT = "PENDING_PAYMENT"
COMPLETED = "COMPLETED"
PAYMENT_CLOSED = "PAYMENT_CLOSED"
REFUNDED = "REFUNDED"


class FlowerShopError(Exception):
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


def _view(order: FlowerOrderORM, payment: dict | None = None) -> dict:
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


async def _reconcile(order: FlowerOrderORM) -> tuple[bool, dict | None]:
    if not order.payment_request_id:
        return False, None
    payment = await _find_payment_request(order.customer_user_id, order.payment_request_id)
    if payment is None:
        return False, None
    if payment["status"] == "PAID":
        target = COMPLETED
    elif payment["status"] in {"REJECTED", "CANCELLED", "EXPIRED"}:
        target = PAYMENT_CLOSED
    else:
        target = PENDING_PAYMENT
    if target == order.status or order.status in {COMPLETED, PAYMENT_CLOSED, REFUNDED}:
        return False, payment
    order.status = target
    order.state_version += 1
    return True, payment


async def create_order(
    *,
    customer_user_id: str,
    product_id: str,
    quantity: int,
    idempotency_key: str,
) -> dict:
    product = PRODUCTS_BY_ID.get(product_id)
    if product is None:
        raise FlowerShopError("商品不存在或已下架", "PRODUCT_NOT_FOUND", 404)
    if not 1 <= quantity <= 20:
        raise FlowerShopError("购买数量必须在 1 到 20 之间", "INVALID_QUANTITY")
    request_digest = _digest(product_id, quantity)

    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(
                select(FlowerOrderORM).where(
                    FlowerOrderORM.customer_user_id == customer_user_id,
                    FlowerOrderORM.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if order is not None:
            if order.request_digest != request_digest:
                raise FlowerShopError(
                    "幂等键已用于不同的花店订单",
                    "IDEMPOTENCY_CONFLICT",
                    409,
                )
        else:
            order = FlowerOrderORM(
                order_id=f"FO{uuid4().hex[:18].upper()}",
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
                        select(FlowerOrderORM).where(
                            FlowerOrderORM.customer_user_id == customer_user_id,
                            FlowerOrderORM.idempotency_key == idempotency_key,
                        )
                    )
                ).scalar_one_or_none()
                if duplicate is None or duplicate.request_digest != request_digest:
                    raise FlowerShopError(
                        "订单创建冲突，请使用原幂等键重试",
                        "ORDER_CONFLICT",
                        409,
                    ) from error
                order = duplicate
            await session.refresh(order)

        order_id = order.order_id
        total_fen = order.total_fen
        note = f"鲜花订单 {order.order_id} · {order.product_name} × {order.quantity}"
        existing_payment_request_id = order.payment_request_id

    if existing_payment_request_id is None:
        try:
            payment = await create_payment_request(
                requester_user_id=FLOWER_MERCHANT_USER_ID,
                payer_user_id=customer_user_id,
                receive_account_id=FLOWER_MERCHANT_ACCOUNT_ID,
                amount=total_fen / 100,
                note=note,
                idempotency_key=f"flower-{order_id}",
            )
        except BankError as error:
            raise FlowerShopError(error.message, error.code, error.status_code) from error

        async with AsyncSessionLocal() as session:
            order = (
                await session.execute(
                    select(FlowerOrderORM)
                    .where(FlowerOrderORM.order_id == order_id)
                    .with_for_update()
                )
            ).scalar_one()
            if order.payment_request_id is None:
                order.payment_request_id = payment["payment_request_id"]
                order.state_version += 1
                await session.commit()
            elif order.payment_request_id != payment["payment_request_id"]:
                raise FlowerShopError("订单付款申请关联冲突", "PAYMENT_LINK_CONFLICT", 409)
            await session.refresh(order)
            return _view(order, payment)

    return await get_order(customer_user_id=customer_user_id, order_id=order_id)


async def get_order(*, customer_user_id: str, order_id: str) -> dict:
    async with AsyncSessionLocal() as session:
        order = await session.get(FlowerOrderORM, order_id)
        if order is None:
            raise FlowerShopError("花店订单不存在", "ORDER_NOT_FOUND", 404)
        if order.customer_user_id != customer_user_id:
            raise FlowerShopError("无权查看该花店订单", "ORDER_FORBIDDEN", 403)
        changed, payment = await _reconcile(order)
        if changed:
            await session.commit()
            await session.refresh(order)
        return _view(order, payment)


async def list_orders(*, customer_user_id: str) -> list[dict]:
    async with AsyncSessionLocal() as session:
        orders = (
            await session.execute(
                select(FlowerOrderORM)
                .where(FlowerOrderORM.customer_user_id == customer_user_id)
                .order_by(FlowerOrderORM.created_at.desc())
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


async def list_merchant_orders(*, merchant_user_id: str) -> list[dict]:
    if merchant_user_id != FLOWER_MERCHANT_USER_ID:
        raise FlowerShopError("只有花店商户可以查看商户订单", "MERCHANT_FORBIDDEN", 403)
    async with AsyncSessionLocal() as session:
        orders = (
            await session.execute(
                select(FlowerOrderORM).order_by(FlowerOrderORM.created_at.desc())
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


async def refund_order(
    *,
    merchant_user_id: str,
    order_id: str,
    state_version: int,
) -> dict:
    if merchant_user_id != FLOWER_MERCHANT_USER_ID:
        raise FlowerShopError("只有花店商户可以发起退款", "MERCHANT_FORBIDDEN", 403)
    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(
                select(FlowerOrderORM)
                .where(FlowerOrderORM.order_id == order_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if order is None:
            raise FlowerShopError("花店订单不存在", "ORDER_NOT_FOUND", 404)
        changed, payment = await _reconcile(order)
        if changed:
            await session.commit()
            await session.refresh(order)
        if order.status == REFUNDED:
            return _view(order, payment)
        if order.state_version != state_version:
            raise FlowerShopError("订单状态已变化，请刷新后重试", "STALE_STATE_VERSION", 409)
        if order.status != COMPLETED or not order.payment_request_id:
            raise FlowerShopError("只有已完成付款的订单可以退款", "ORDER_NOT_REFUNDABLE", 409)
        payment_request_id = order.payment_request_id

    try:
        await refund_merchant_payment(
            payment_request_id=payment_request_id,
            merchant_user_id=merchant_user_id,
            operation_id=f"FLOWER-REFUND-{order_id}",
        )
    except BankError as error:
        raise FlowerShopError(error.message, error.code, error.status_code) from error

    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(
                select(FlowerOrderORM)
                .where(FlowerOrderORM.order_id == order_id)
                .with_for_update()
            )
        ).scalar_one()
        if order.status != REFUNDED:
            if order.status != COMPLETED:
                raise FlowerShopError("订单退款状态冲突", "REFUND_STATE_CONFLICT", 409)
            order.status = REFUNDED
            order.state_version += 1
            await session.commit()
            await session.refresh(order)
        payment = await _find_payment_request(
            order.customer_user_id,
            payment_request_id,
        )
        return _view(order, payment)
