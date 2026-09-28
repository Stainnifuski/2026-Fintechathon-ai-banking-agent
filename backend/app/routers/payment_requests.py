"""受认证保护的收款申请接口；路由层不访问数据库。"""

from ..schemas.bank import BANK_ERROR_RESPONSES

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..integrations.bank_api import BankApi, get_bank_api
from ..middleware.auth import get_current_user
from ..schemas.payment_requests import (
    PaymentRequestAcceptAction,
    PaymentRequestAction,
    PaymentRequestBatchCreate,
    PaymentRequestCreate,
    PaymentRequestView,
)
from ..schemas.tasks import TaskSnapshot
from ..services.bank import (
    BankError,
    cancel_payment_request,
    create_payment_request,
    create_payment_request_batch,
    list_payment_requests,
    prepare_payment_request_payment,
    respond_to_payment_request,
)
from ..services.orchestration import TaskError, create_transfer_task

router = APIRouter(prefix="/api/v1/payment-requests", tags=["payment-requests"], responses=BANK_ERROR_RESPONSES)


def _raise_bank_error(error: BankError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


@router.post("", response_model=PaymentRequestView, status_code=status.HTTP_201_CREATED)
async def create(
    request: PaymentRequestCreate,
    current_user: dict = Depends(get_current_user),
) -> PaymentRequestView:
    try:
        result = await create_payment_request(
            requester_user_id=current_user["user_id"], **request.model_dump()
        )
        return PaymentRequestView(**result)
    except BankError as error:
        _raise_bank_error(error)


@router.post(
    "/batch",
    response_model=list[PaymentRequestView],
    status_code=status.HTTP_201_CREATED,
)
async def create_batch(
    request: PaymentRequestBatchCreate,
    current_user: dict = Depends(get_current_user),
) -> list[PaymentRequestView]:
    try:
        return [
            PaymentRequestView(**item)
            for item in await create_payment_request_batch(
                requester_user_id=current_user["user_id"], **request.model_dump()
            )
        ]
    except BankError as error:
        _raise_bank_error(error)


@router.get("", response_model=list[PaymentRequestView])
async def list_requests(
    role: str = Query(default="all", pattern="^(all|incoming|outgoing)$"),
    current_user: dict = Depends(get_current_user),
) -> list[PaymentRequestView]:
    try:
        return [
            PaymentRequestView(**item)
            for item in await list_payment_requests(
                user_id=current_user["user_id"], role=role
            )
        ]
    except BankError as error:
        _raise_bank_error(error)


@router.post("/{payment_request_id}/accept", response_model=TaskSnapshot)
async def accept(
    payment_request_id: str,
    request: PaymentRequestAcceptAction,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    bank: BankApi = Depends(get_bank_api),
) -> TaskSnapshot:
    try:
        payment = await prepare_payment_request_payment(
            payment_request_id=payment_request_id,
            payer_user_id=current_user["user_id"],
            payer_account_id=request.payer_account_id,
            state_version=request.state_version,
        )
        result = await create_transfer_task(
            db,
            bank,
            user_id=current_user["user_id"],
            request_id=f"PRTASK-{payment['operation_id']}",
            operation_id=payment["operation_id"],
            from_account=payment["payer_account_id"],
            to_account=payment["receive_account_id"],
            amount=payment["amount"],
            note=payment["note"],
        )
        return TaskSnapshot(**result)
    except BankError as error:
        _raise_bank_error(error)
    except TaskError as error:
        await db.rollback()
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": error.message},
        ) from error


@router.post("/{payment_request_id}/reject", response_model=PaymentRequestView)
async def reject(
    payment_request_id: str,
    request: PaymentRequestAction,
    current_user: dict = Depends(get_current_user),
) -> PaymentRequestView:
    try:
        return PaymentRequestView(
            **await respond_to_payment_request(
                payment_request_id=payment_request_id,
                payer_user_id=current_user["user_id"],
                action="reject",
                state_version=request.state_version,
            )
        )
    except BankError as error:
        _raise_bank_error(error)


@router.post("/{payment_request_id}/cancel", response_model=PaymentRequestView)
async def cancel(
    payment_request_id: str,
    request: PaymentRequestAction,
    current_user: dict = Depends(get_current_user),
) -> PaymentRequestView:
    try:
        return PaymentRequestView(
            **await cancel_payment_request(
                payment_request_id=payment_request_id,
                requester_user_id=current_user["user_id"],
                state_version=request.state_version,
            )
        )
    except BankError as error:
        _raise_bank_error(error)
