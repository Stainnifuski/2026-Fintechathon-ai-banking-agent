"""4号模拟银行内部协议；仅可信应用执行器可调用。"""

import os
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..schemas.bank import (
    BankOperationResultView, TransferDirectoryView, TotpVerificationView, BANK_ERROR_RESPONSES,
)
from ..services.bank import (
    BankError,
    BankResponseLost,
    BankTransferCommand,
    TrustedExecutionContext,
    query_operation,
    release_payment_request_payment,
    resolve_transfer_parties,
    submit_transfer,
    validate_transfer_parties,
    verify_sms_for_operation,
    verify_totp_for_operation,
)

router = APIRouter(prefix="/api/v1/internal/bank", tags=["internal-bank"], responses=BANK_ERROR_RESPONSES)
INTERNAL_BANK_TOKEN = os.getenv("INTERNAL_BANK_TOKEN")


class InternalBankSubmit(BaseModel):
    operation_id: str = Field(min_length=8, max_length=64)
    from_account: str
    to_account: str
    amount_fen: int = Field(gt=0, strict=True)
    currency: str = "CNY"
    param_digest: str = Field(min_length=64, max_length=64)
    note: str | None = None
    recipient_phone: str | None = None
    subject_user_id: str
    authorization_id: str
    control_epoch: int = Field(ge=1)
    authorization_issued_at: str | None = None
    simulate_response_loss: bool = False


InternalBankResult = BankOperationResultView


class TransferValidationRequest(BaseModel):
    subject_user_id: str
    from_account: str
    to_account: str
    recipient_phone: str | None = None


class TransferResolutionRequest(BaseModel):
    subject_user_id: str
    recipient_name: str = Field(min_length=1, max_length=100)


class TransferDirectoryRequest(BaseModel):
    subject_user_id: str
    recipient_name: str | None = Field(default=None, max_length=100)
    recipient_phone: str | None = Field(default=None, max_length=32)


class FundingAccountsRequest(BaseModel):
    subject_user_id: str


class TransferValidationResponse(BaseModel):
    from_account: str
    to_account: str
    recipient_name: str
    daily_transfer_used_fen: int
    payer_card_masked: str | None = None
    recipient_card_masked: str | None = None


class TransferResolutionResponse(TransferValidationResponse):
    recipient_user_id: str


class PaymentRequestRelease(BaseModel):
    subject_user_id: str


class InternalTotpVerification(BaseModel):
    subject_user_id: str
    proof: str = Field(min_length=6, max_length=6)


class InternalSmsVerification(BaseModel):
    subject_user_id: str
    proof: str = Field(pattern=r"^\d{6}$")
    challenge_id: str = Field(min_length=1, max_length=64)
    reference_id: str = Field(min_length=1, max_length=64)


def require_internal_token(request: Request, x_internal_token: str = Header(default="")) -> None:
    if not INTERNAL_BANK_TOKEN or INTERNAL_BANK_TOKEN in {
        "dev-internal-token", "replace-with-a-separate-internal-secret"
    }:
        raise HTTPException(status_code=503, detail={"code": "INTERNAL_BANK_TOKEN_MISSING", "message": "内部银行凭据未安全配置"})
    if not hmac.compare_digest(x_internal_token, INTERNAL_BANK_TOKEN):
        raise HTTPException(status_code=403, detail={"code": "INTERNAL_BANK_TOKEN_INVALID", "message": "无效的内部执行凭据"})
    from ..services.audit.context import trust_internal
    trust_internal(request.headers)


def _raise_bank_error(error: BankError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message, **getattr(error, "security_detail", {})},
    ) from error


@router.post("/totp/verify", response_model=TotpVerificationView, dependencies=[Depends(require_internal_token)])
async def verify_totp(request: InternalTotpVerification, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await verify_totp_for_operation(db, request.subject_user_id, request.proof)
        await db.commit()
        return {"verified": True}
    except BankError as error:
        await db.rollback()
        _raise_bank_error(error)


@router.post("/sms/verify", response_model=TotpVerificationView, dependencies=[Depends(require_internal_token)])
async def verify_sms(request: InternalSmsVerification, db: AsyncSession = Depends(get_db)) -> dict:
    """转账任务的短信第二因素：挑战须绑定到同一任务。"""
    try:
        await verify_sms_for_operation(
            db, request.subject_user_id, request.proof,
            challenge_id=request.challenge_id, purpose="transfer", reference_id=request.reference_id,
        )
        await db.commit()
        return {"verified": True}
    except BankError as error:
        await db.rollback()
        _raise_bank_error(error)


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
                recipient_phone=request.recipient_phone,
            ),
            TrustedExecutionContext(
                subject_user_id=request.subject_user_id,
                authorization_id=request.authorization_id,
                control_epoch=request.control_epoch,
                authorization_issued_at=request.authorization_issued_at,
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


@router.post("/transfers/directory", response_model=TransferDirectoryView, dependencies=[Depends(require_internal_token)])
async def directory(request: TransferDirectoryRequest, db: AsyncSession = Depends(get_db)) -> dict:
    from ..services.bank.transfer_service import transfer_directory
    try:
        return await transfer_directory(db, **request.model_dump())
    except BankError as error:
        _raise_bank_error(error)


@router.post("/transfers/funding-accounts", dependencies=[Depends(require_internal_token)])
async def funding_accounts(request: FundingAccountsRequest, db: AsyncSession = Depends(get_db)) -> dict:
    from ..services.bank.transfer_service import transfer_funding_accounts
    try:
        return await transfer_funding_accounts(db, subject_user_id=request.subject_user_id)
    except BankError as error:
        _raise_bank_error(error)


@router.post(
    "/payment-requests/{operation_id}/release",
    status_code=204,
    dependencies=[Depends(require_internal_token)],
)
async def release_payment_request(
    operation_id: str,
    request: PaymentRequestRelease,
    db: AsyncSession = Depends(get_db),
) -> None:
    try:
        await release_payment_request_payment(
            db,
            operation_id=operation_id,
            subject_user_id=request.subject_user_id,
        )
    except BankError as error:
        await db.rollback()
        _raise_bank_error(error)


class ScheduledHoldPlace(BaseModel):
    subject_user_id: str
    account_id: str = Field(min_length=1, max_length=32)
    amount_fen: int = Field(gt=0, strict=True)
    purpose: str = Field(min_length=1, max_length=24)
    reference_id: str = Field(min_length=1, max_length=64)
    link_keys: list[str] = Field(default_factory=list, max_length=8)


class ScheduledHoldLink(BaseModel):
    subject_user_id: str
    link_key: str = Field(min_length=1, max_length=96)


class ScheduledHoldRelease(BaseModel):
    subject_user_id: str
    purpose: str = Field(min_length=1, max_length=24)
    reference_id: str = Field(min_length=1, max_length=64)
    keep_hold_id: str | None = Field(default=None, max_length=64)


@router.put("/scheduled-holds/{hold_id}", dependencies=[Depends(require_internal_token)])
async def place_scheduled_hold(hold_id: str, request: ScheduledHoldPlace, db: AsyncSession = Depends(get_db)) -> dict:
    """为定时任务锁定活期资金；可自由支配余额不足时拒绝。"""
    from ..services.bank.scheduled_holds import place_hold
    try:
        return await place_hold(db, hold_id=hold_id, **request.model_dump())
    except BankError as error:
        await db.rollback()
        _raise_bank_error(error)


@router.post("/scheduled-holds/{hold_id}/links", status_code=204, dependencies=[Depends(require_internal_token)])
async def link_scheduled_hold(hold_id: str, request: ScheduledHoldLink, db: AsyncSession = Depends(get_db)) -> None:
    from ..services.bank.scheduled_holds import link_hold
    try:
        await link_hold(db, hold_id=hold_id, **request.model_dump())
    except BankError as error:
        await db.rollback()
        _raise_bank_error(error)


@router.post("/scheduled-holds/release", dependencies=[Depends(require_internal_token)])
async def release_scheduled_holds(request: ScheduledHoldRelease, db: AsyncSession = Depends(get_db)) -> list[dict]:
    from ..services.bank.scheduled_holds import release_holds
    try:
        return await release_holds(db, **request.model_dump())
    except BankError as error:
        await db.rollback()
        _raise_bank_error(error)
