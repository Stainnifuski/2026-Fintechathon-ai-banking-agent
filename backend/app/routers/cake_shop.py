"""登录用户可访问的蛋糕店商品与订单接口。"""

from fastapi import APIRouter, Depends, HTTPException, status

from ..middleware.auth import get_current_user
from ..schemas.cake_shop import (
    CakeOrderCreate,
    CakeOrderView,
    CakeProductView,
    CakeRefundAction,
)
from ..services.cake_shop import (
    CakeShopError,
    create_order,
    get_order,
    list_orders,
    list_merchant_orders,
    list_products,
    refund_order,
)

router = APIRouter(prefix="/api/v1/cake-shop", tags=["cake-shop"])


def _raise_cake_error(error: CakeShopError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


@router.get("/products", response_model=list[CakeProductView])
async def products(_: dict = Depends(get_current_user)) -> list[CakeProductView]:
    return [CakeProductView(**item) for item in list_products()]


@router.post(
    "/orders",
    response_model=CakeOrderView,
    status_code=status.HTTP_201_CREATED,
)
async def create(
    request: CakeOrderCreate,
    current_user: dict = Depends(get_current_user),
) -> CakeOrderView:
    try:
        return CakeOrderView(
            **await create_order(
                customer_user_id=current_user["user_id"],
                **request.model_dump(),
            )
        )
    except CakeShopError as error:
        _raise_cake_error(error)


@router.get("/orders", response_model=list[CakeOrderView])
async def orders(
    current_user: dict = Depends(get_current_user),
) -> list[CakeOrderView]:
    try:
        return [
            CakeOrderView(**item)
            for item in await list_orders(customer_user_id=current_user["user_id"])
        ]
    except CakeShopError as error:
        _raise_cake_error(error)


@router.get("/orders/{order_id}", response_model=CakeOrderView)
async def order(
    order_id: str,
    current_user: dict = Depends(get_current_user),
) -> CakeOrderView:
    try:
        return CakeOrderView(
            **await get_order(
                customer_user_id=current_user["user_id"],
                order_id=order_id,
            )
        )
    except CakeShopError as error:
        _raise_cake_error(error)


@router.get("/merchant/orders", response_model=list[CakeOrderView])
async def merchant_orders(
    current_user: dict = Depends(get_current_user),
) -> list[CakeOrderView]:
    try:
        return [
            CakeOrderView(**item)
            for item in await list_merchant_orders(
                merchant_user_id=current_user["user_id"],
            )
        ]
    except CakeShopError as error:
        _raise_cake_error(error)


@router.post(
    "/merchant/orders/{order_id}/refund",
    response_model=CakeOrderView,
)
async def refund(
    order_id: str,
    request: CakeRefundAction,
    current_user: dict = Depends(get_current_user),
) -> CakeOrderView:
    try:
        return CakeOrderView(
            **await refund_order(
                merchant_user_id=current_user["user_id"],
                order_id=order_id,
                state_version=request.state_version,
                amount_fen=request.amount_fen,
                operation_id=request.operation_id,
            )
        )
    except CakeShopError as error:
        _raise_cake_error(error)
