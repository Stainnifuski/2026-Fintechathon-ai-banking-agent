"""登录用户可访问的花店商品与订单接口。"""

from fastapi import APIRouter, Depends, HTTPException, status

from ..middleware.auth import get_current_user
from ..schemas.flower_shop import (
    FlowerOrderCreate,
    FlowerOrderView,
    FlowerProductView,
    FlowerRefundAction,
)
from ..services.flower_shop import (
    FlowerShopError,
    create_order,
    get_order,
    list_orders,
    list_merchant_orders,
    list_products,
    refund_order,
)

router = APIRouter(prefix="/api/v1/flower-shop", tags=["flower-shop"])


def _raise_flower_error(error: FlowerShopError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


@router.get("/products", response_model=list[FlowerProductView])
async def products(_: dict = Depends(get_current_user)) -> list[FlowerProductView]:
    return [FlowerProductView(**item) for item in list_products()]


@router.post(
    "/orders",
    response_model=FlowerOrderView,
    status_code=status.HTTP_201_CREATED,
)
async def create(
    request: FlowerOrderCreate,
    current_user: dict = Depends(get_current_user),
) -> FlowerOrderView:
    try:
        return FlowerOrderView(
            **await create_order(
                customer_user_id=current_user["user_id"],
                **request.model_dump(),
            )
        )
    except FlowerShopError as error:
        _raise_flower_error(error)


@router.get("/orders", response_model=list[FlowerOrderView])
async def orders(
    current_user: dict = Depends(get_current_user),
) -> list[FlowerOrderView]:
    try:
        return [
            FlowerOrderView(**item)
            for item in await list_orders(customer_user_id=current_user["user_id"])
        ]
    except FlowerShopError as error:
        _raise_flower_error(error)


@router.get("/orders/{order_id}", response_model=FlowerOrderView)
async def order(
    order_id: str,
    current_user: dict = Depends(get_current_user),
) -> FlowerOrderView:
    try:
        return FlowerOrderView(
            **await get_order(
                customer_user_id=current_user["user_id"],
                order_id=order_id,
            )
        )
    except FlowerShopError as error:
        _raise_flower_error(error)


@router.get("/merchant/orders", response_model=list[FlowerOrderView])
async def merchant_orders(
    current_user: dict = Depends(get_current_user),
) -> list[FlowerOrderView]:
    try:
        return [
            FlowerOrderView(**item)
            for item in await list_merchant_orders(
                merchant_user_id=current_user["user_id"],
            )
        ]
    except FlowerShopError as error:
        _raise_flower_error(error)


@router.post(
    "/merchant/orders/{order_id}/refund",
    response_model=FlowerOrderView,
)
async def refund(
    order_id: str,
    request: FlowerRefundAction,
    current_user: dict = Depends(get_current_user),
) -> FlowerOrderView:
    try:
        return FlowerOrderView(
            **await refund_order(
                merchant_user_id=current_user["user_id"],
                order_id=order_id,
                state_version=request.state_version,
                amount_fen=request.amount_fen,
                operation_id=request.operation_id,
            )
        )
    except FlowerShopError as error:
        _raise_flower_error(error)
