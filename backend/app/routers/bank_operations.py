"""4号模拟银行内部协议；仅可信应用执行器可调用。"""

import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..services.bank import (
    BankError,
    BankResponseLost,
    BankTransferCommand,
    TrustedExecutionContext,
    query_operation,
    resolve_transfer_parties,
    submit_transfer,
    validate_transfer_parties,
)

router = APIRouter(prefix="/api/v1/internal/bank", tags=["internal-bank"])
INTERNAL_BANK_TOKEN = os.getenv("INTERNAL_BANK_TOKEN", "dev-internal-token")


class InternalBankSubmit(BaseModel):
    operation_id: str = Field(min_length=8, max_length=64)
    from_account: str
    to_account: str
    amount_fen: int = Field(gt=0)
    currency: str = "CNY"
    param_digest: str = Field(min_length=64, max_length=64)
    note: str | None = None
    subject_user_id: str
    authorization_id: str
    control_epoch: int = Field(ge=1)
    simulate_response_loss: bool = False


class InternalBankResult(BaseModel):
    operation_id: str
    status: str
    bank_tx_id: str | None = None
    error_code: str | None = None


class TransferValidationRequest(BaseModel):
    subject_user_id: str
    from_account: str
    to_account: str


class TransferResolutionRequest(BaseModel):
    subject_user_id: str
    recipient_name: str = Field(min_length=1, max_length=100)


class TransferValidationResponse(BaseModel):
    from_account: str
    to_account: str
    recipient_name: str
    daily_transfer_used_fen: int


class TransferResolutionResponse(TransferValidationResponse):
    recipient_user_id: str


def require_internal_token(x_internal_token: str = Header(default="")) -> None:
    if x_internal_token != INTERNAL_BANK_TOKEN:
        raise HTTPException(status_code=403, detail="无效的内部执行凭据")


def _raise_bank_error(error: BankError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


@router.post(
    "/transfers/validate",
    response_model=TransferValidationResponse,
    dependencies=[Depends(require_internal_token)],
)
async def validate_transfer(
    request: TransferValidationRequest, db: AsyncSession = Depends(get_db)
) -> TransferValidationResponse:
    try:
        result = await validate_transfer_parties(db, **request.model_dump())
        return TransferValidationResponse(**result.__dict__)
    except BankError as error:
        _raise_bank_error(error)


@router.post(
    "/transfers/resolve",
    response_model=TransferResolutionResponse,
    dependencies=[Depends(require_internal_token)],
)
async def resolve_transfer(
    request: TransferResolutionRequest, db: AsyncSession = Depends(get_db)
) -> TransferResolutionResponse:
    try:
        result = await resolve_transfer_parties(db, **request.model_dump())
        return TransferResolutionResponse(**result.__dict__)
    except BankError as error:
        _raise_bank_error(error)


@router.post(
    "/operations",
    response_model=InternalBankResult,
    dependencies=[Depends(require_internal_token)],
)
async def submit(
    request: InternalBankSubmit, db: AsyncSession = Depends(get_db)
) -> InternalBankResult:
    try:
        result = await submit_transfer(
            db,
            BankTransferCommand(
                operation_id=request.operation_id,
                from_account=request.from_account,
                to_account=request.to_account,
                amount_fen=request.amount_fen,
                currency=request.currency,
                param_digest=request.param_digest,
                note=request.note,
            ),
            TrustedExecutionContext(
                subject_user_id=request.subject_user_id,
                authorization_id=request.authorization_id,
                control_epoch=request.control_epoch,
            ),
            simulate_response_loss=request.simulate_response_loss,
        )
        return InternalBankResult(**result.__dict__)
    except BankError as error:
        await db.rollback()
        _raise_bank_error(error)
    except BankResponseLost as error:
        raise HTTPException(
            status_code=504,
            detail={"code": "BANK_RESPONSE_LOST", "message": str(error)},
        ) from error


@router.get(
    "/operations/{operation_id}",
    response_model=InternalBankResult,
    dependencies=[Depends(require_internal_token)],
)
async def query(
    operation_id: str, db: AsyncSession = Depends(get_db)
) -> InternalBankResult:
    result = await query_operation(db, operation_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "银行尚未受理该操作"},
        )
    return InternalBankResult(**result.__dict__)
