"""受认证保护的卡片管理接口：预览 → 本人确认（黄）/确认+TOTP（红）→ 银行受理；另含模拟用卡交易。"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..domain import yuan_to_fen
from ..integrations import card_authorization as auth
from ..integrations.card_authorization import CardAuthorizationError
from ..middleware.auth import get_current_user
from ..models.card import CardOperationORM
from ..schemas.bank import BANK_ERROR_RESPONSES
from ..schemas.cards import (
    CardAuthorizationView, CardConfirmRequest, CardOperationCreate, CardOperationView,
    CardPurchaseRequest, CardPurchaseResult, CardVerifyRequest, CardView,
)
from ..services.bank import BankError, card_service as cards, verify_second_factor
from ..services.card_operations import open_preview, operation_view as _view

router = APIRouter(prefix="/api/v1/cards", tags=["cards"], responses=BANK_ERROR_RESPONSES)


async def _fail(db: AsyncSession, error: BankError | CardAuthorizationError) -> None:
    if getattr(error, "persist", False):
        await db.commit()
    else:
        await db.rollback()
    raise HTTPException(error.status_code, detail={"code": error.code, "message": error.message, **getattr(error, "security_detail", {})}) from error


async def _execute(db: AsyncSession, row: CardOperationORM) -> CardOperationView:
    """授权已提交后单独受理；银行拒绝时终止本次授权，不自动重试新参数。"""
    user_id, operation_id = row.user_id, row.operation_id
    try:
        result = await cards.execute_card_operation(
            db, user_id=user_id, operation_id=operation_id, action=row.action,
            card_id=row.card_id, card_state_version=row.card_state_version, params=row.params)
        auth.consume(row, jsonable_encoder(result))
        await db.commit()
        return _view(row)
    except BankError as error:
        await db.rollback()
        row = await auth.owned_operation(db, user_id, operation_id)
        auth.mark_failed(db, row, error.code)
        await db.commit()
        raise HTTPException(error.status_code, detail={"code": error.code, "message": error.message, **getattr(error, "security_detail", {})}) from error


@router.get("", response_model=list[CardView])
async def list_user_cards(current_user: dict = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)) -> list[CardView]:
    return [CardView(**item) for item in await cards.list_cards(db, user_id=current_user["user_id"])]


@router.post("/operations", response_model=CardOperationView, status_code=status.HTTP_201_CREATED)
async def preview_operation(request: CardOperationCreate, current_user: dict = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)) -> CardOperationView:
    try:
        row = await open_preview(db, user_id=current_user["user_id"], request=request)
        await db.commit()
        return _view(row)
    except (BankError, CardAuthorizationError) as error:
        await _fail(db, error)


@router.get("/operations/{operation_id}", response_model=CardOperationView)
async def get_operation(operation_id: str, current_user: dict = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)) -> CardOperationView:
    try:
        return _view(await auth.owned_operation(db, current_user["user_id"], operation_id))
    except CardAuthorizationError as error:
        await _fail(db, error)


@router.post("/operations/{operation_id}/confirm", response_model=CardOperationView)
async def confirm_operation(operation_id: str, request: CardConfirmRequest,
                            current_user: dict = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)) -> CardOperationView:
    try:
        row = await auth.owned_operation(db, current_user["user_id"], operation_id)
        if row.status == auth.COMPLETED and request.digest == row.param_digest:
            return _view(row)
        if row.action != "card_lock":
            from ..security.circuit_breaker import check_trade
            await check_trade(db, row.user_id, issued_at=row.created_at, authorization=True)
        if row.status != auth.AUTHORIZED:
            auth.record_confirmation(db, row, request.digest)
            await db.commit()
        if row.status == auth.AWAITING_MFA:
            return _view(row)
    except CardAuthorizationError as error:
        from ..security.circuit_breaker import report_violation
        await report_violation(db, current_user["user_id"], error.code)
        await _fail(db, error)
    return await _execute(db, row)


@router.post("/operations/{operation_id}/verify", response_model=CardOperationView)
async def verify_operation(operation_id: str, request: CardVerifyRequest,
                           current_user: dict = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)) -> CardOperationView:
    user_id = current_user["user_id"]
    try:
        row = await auth.owned_operation(db, user_id, operation_id)
        if row.status == auth.COMPLETED:
            return _view(row)
        if row.status != auth.AUTHORIZED:
            from ..security.circuit_breaker import check_trade
            await check_trade(db, user_id, issued_at=row.created_at, authorization=True)
            auth.require_verification_pending(db, row)
            try:
                await verify_second_factor(db, user_id, request.proof, method=request.method,
                                           challenge_id=request.challenge_id,
                                           purpose="card", reference_id=operation_id)
            except BankError as error:
                await db.rollback()
                if error.code in ("TOTP_INVALID", "TOTP_REPLAY", "SMS_INVALID", "SECURITY_LOCKED"):
                    row = await auth.owned_operation(db, user_id, operation_id)
                    locked = auth.record_failed_verification(db, row, request.method)
                    await db.commit()
                    if locked is not None and error.code != "SECURITY_LOCKED":
                        raise locked from error
                raise HTTPException(error.status_code,
                                    detail={"code": error.code, "message": error.message, **getattr(error, "security_detail", {})}) from error
            auth.record_verification(db, row, request.method)
            await db.commit()
    except CardAuthorizationError as error:
        await _fail(db, error)
    return await _execute(db, row)


@router.post("/operations/{operation_id}/cancel", response_model=CardOperationView)
async def cancel_operation(operation_id: str, current_user: dict = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)) -> CardOperationView:
    try:
        row = await auth.owned_operation(db, current_user["user_id"], operation_id)
        auth.cancel(db, row)
        await db.commit()
        return _view(row)
    except CardAuthorizationError as error:
        await _fail(db, error)


@router.get("/{card_id}/transactions", response_model=list[CardAuthorizationView])
async def list_card_transactions(card_id: str, current_user: dict = Depends(get_current_user),
                                 db: AsyncSession = Depends(get_db)) -> list[CardAuthorizationView]:
    """本卡最近20笔用卡授权，含被拒绝的交易及原因。"""
    try:
        rows = await cards.list_card_authorizations(db, user_id=current_user["user_id"], card_id=card_id)
    except BankError as error:
        await _fail(db, error)
    return [CardAuthorizationView(**row) for row in rows]


@router.post("/{card_id}/transactions", response_model=CardPurchaseResult, status_code=status.HTTP_201_CREATED)
async def simulate_purchase(card_id: str, request: CardPurchaseRequest,
                            current_user: dict = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)) -> CardPurchaseResult:
    """模拟商户发起的用卡交易；被拒绝也返回201并保留授权记录，approved 区分结果。"""
    try:
        result = await cards.simulate_card_purchase(
            db, user_id=current_user["user_id"], card_id=card_id, operation_id=request.operation_id,
            amount_fen=yuan_to_fen(request.amount), merchant=request.merchant, channel=request.channel)
        await db.commit()
    except BankError as error:
        await _fail(db, error)
    return CardPurchaseResult(**jsonable_encoder(result))
