"""受认证保护的订阅查询与取消接口。"""

from ..schemas.bank import BANK_ERROR_RESPONSES

from fastapi import APIRouter, Depends, HTTPException

from ..middleware.auth import get_current_user
from ..schemas.subscriptions import (
    SubscriptionAction,
    SubscriptionCancelAction,
    SubscriptionCancellationPreview,
    SubscriptionChargeAction,
    SubscriptionChargeResult,
    SubscriptionView,
)
from ..services.bank import (
    BankError,
    cancel_subscription,
    list_subscriptions,
    simulate_subscription_charge,
)

from ..services.bank.subscription_service import preview_subscription_cancellation, restore_subscription_for_testing

router = APIRouter(prefix="/api/v1/subscriptions", tags=["subscriptions"], responses=BANK_ERROR_RESPONSES)


def _raise_bank_error(error: BankError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


@router.get("", response_model=list[SubscriptionView])
async def list_user_subscriptions(
    current_user: dict = Depends(get_current_user),
) -> list[SubscriptionView]:
    return [
        SubscriptionView(**item)
        for item in await list_subscriptions(user_id=current_user["user_id"])
    ]


@router.post("/{subscription_id}/cancel-preview", response_model=SubscriptionCancellationPreview)
async def preview_cancel(subscription_id: str, current_user: dict = Depends(get_current_user)):
    try:
        return await preview_subscription_cancellation(
            subscription_id=subscription_id, user_id=current_user["user_id"])
    except BankError as error:
        _raise_bank_error(error)


@router.post("/{subscription_id}/cancel", response_model=SubscriptionView)
async def cancel_user_subscription(
    subscription_id: str,
    request: SubscriptionCancelAction,
    current_user: dict = Depends(get_current_user),
) -> SubscriptionView:
    try:
        return SubscriptionView(
            **await cancel_subscription(
                subscription_id=subscription_id,
                user_id=current_user["user_id"],
                state_version=request.state_version,
                confirmation_token=request.confirmation_token,
            )
        )
    except BankError as error:
        _raise_bank_error(error)


@router.post("/{subscription_id}/restore-test", response_model=SubscriptionView,
             summary="测试专用：恢复已取消订阅")
async def restore_user_subscription_for_testing(
    subscription_id: str, request: SubscriptionAction,
    current_user: dict = Depends(get_current_user),
) -> SubscriptionView:
    try:
        return SubscriptionView(**await restore_subscription_for_testing(
            subscription_id=subscription_id, user_id=current_user["user_id"],
            state_version=request.state_version))
    except BankError as error:
        _raise_bank_error(error)


@router.post(
    "/{subscription_id}/simulate-charge",
    response_model=SubscriptionChargeResult,
)
async def simulate_user_subscription_charge(
    subscription_id: str,
    request: SubscriptionChargeAction,
    current_user: dict = Depends(get_current_user),
) -> SubscriptionChargeResult:
    try:
        return SubscriptionChargeResult(
            **await simulate_subscription_charge(
                subscription_id=subscription_id,
                user_id=current_user["user_id"],
                state_version=request.state_version,
                idempotency_key=request.idempotency_key,
            )
        )
    except BankError as error:
        _raise_bank_error(error)
