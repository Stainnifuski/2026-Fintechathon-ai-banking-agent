"""4号模拟银行内部协议；仅可信应用执行器可调用。"""

import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..services.bank import (
    BankError,
    BankTransferCommand,
    TrustedExecutionContext,
    query_operation,
    submit_transfer,
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


class InternalBankResult(BaseModel):
    operation_id: str
    status: str
    bank_tx_id: str | None = None
    error_code: str | None = None


def require_internal_token(x_internal_token: str = Header(default="")) -> None:
    if x_internal_token != INTERNAL_BANK_TOKEN:
        raise HTTPException(status_code=403, detail="无效的内部执行凭据")


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
        )
        return InternalBankResult(**result.__dict__)
    except BankError as error:
        await db.rollback()
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": error.message},
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
