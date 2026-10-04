"""受认证保护的卡片管理接口：预览 → 本人确认（黄）/确认+TOTP（红）→ 银行受理。"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..integrations import card_authorization as auth
from ..integrations.card_authorization import CardAuthorizationError
from ..middleware.auth import get_current_user
from ..models.card import CardOperationORM
from ..schemas.bank import BANK_ERROR_RESPONSES
from ..schemas.cards import (
    CardConfirmRequest, CardOperationCreate, CardOperationView, CardVerifyRequest, CardView,
)
from ..services.bank import BankError, card_service as cards, verify_second_factor

router = APIRouter(prefix="/api/v1/cards", tags=["cards"], responses=BANK_ERROR_RESPONSES)

ACTION_LABELS = {
    "card_apply_virtual": "申请虚拟卡",
    "card_set_transaction_limits": "设置交易限额",
    "card_adjust_credit_limit": "调整信用额度",
    "card_unlock": "解锁卡片",
    "card_report_lost": "卡片挂失",
}


def _message(row: CardOperationORM) -> str:
    label = ACTION_LABELS[row.action]
    if row.status == auth.AWAITING_CONFIRMATION:
        if row.permission_level == "red":
            return f"{label}为高风险操作：请核对银行返回的变更，确认后还需输入验证器动态码"
        return f"请核对银行返回的变更，确认后{label}"
    return {
        auth.AWAITING_MFA: f"已确认{label}，请输入验证器6位动态码完成强验证",
        auth.AUTHORIZED: f"{label}已授权，正在提交银行",
        auth.COMPLETED: f"{label}已完成",
        auth.CANCELLED: f"{label}已取消，未提交银行",
        auth.EXPIRED: f"{label}确认已过期，未提交银行，请重新预览",
    }.get(row.status, f"{label}未执行（{row.error_code}）")


def _view(row: CardOperationORM) -> CardOperationView:
    return CardOperationView(
        operation_id=row.operation_id, action=row.action, status=row.status,
        permission_level=row.permission_level,
        requires_confirmation=row.permission_level in ("yellow", "red"),
        requires_mfa=row.permission_level == "red",
        digest=row.param_digest, card_id=row.card_id, card=row.preview.get("card"),
        changes=row.preview.get("changes", []), result=row.result, error_code=row.error_code,
        failed_verifications=row.failed_verifications, expires_at=row.expires_at,
        allowed_actions=auth.allowed_actions(row), message=_message(row),
    )


async def _fail(db: AsyncSession, error: BankError | CardAuthorizationError) -> None:
    if getattr(error, "persist", False):
        await db.commit()
    else:
        await db.rollback()
    raise HTTPException(error.status_code, detail={"code": error.code, "message": error.message}) from error


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
        auth.mark_failed(row, error.code)
        await db.commit()
        raise HTTPException(error.status_code, detail={"code": error.code, "message": error.message}) from error


@router.get("", response_model=list[CardView])
async def list_user_cards(current_user: dict = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)) -> list[CardView]:
    return [CardView(**item) for item in await cards.list_cards(db, user_id=current_user["user_id"])]


@router.post("/operations", response_model=CardOperationView, status_code=status.HTTP_201_CREATED)
async def preview_operation(request: CardOperationCreate, current_user: dict = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)) -> CardOperationView:
    user_id = current_user["user_id"]
    params = request.bank_params()
    try:
        existing = await db.get(CardOperationORM, request.operation_id)
        if existing is not None:
            # 原号重放直接返回原预览，避免以新银行状态覆盖用户已核对的内容。
            row = await auth.open_operation(
                db, user_id=user_id, operation_id=request.operation_id, action=request.action,
                card_id=existing.card_id, card_state_version=existing.card_state_version,
                params=params, preview=existing.preview)
            if row.card_id != request.card_id:
                raise CardAuthorizationError("原操作号参数冲突", "IDEMPOTENCY_CONFLICT")
            return _view(row)
        preview = await cards.preview_card_operation(
            db, user_id=user_id, action=request.action, card_id=request.card_id, params=params)
        row = await auth.open_operation(
            db, user_id=user_id, operation_id=request.operation_id, action=request.action,
            card_id=preview["card_id"], card_state_version=preview["card_state_version"],
            params=params, preview=jsonable_encoder(preview))
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
        if row.status != auth.AUTHORIZED:
            auth.record_confirmation(db, row, request.digest)
            await db.commit()
        if row.status == auth.AWAITING_MFA:
            return _view(row)
    except CardAuthorizationError as error:
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
            auth.require_verification_pending(row)
            try:
                await verify_second_factor(db, user_id, request.proof, method=request.method,
                                           challenge_id=request.challenge_id,
                                           purpose="card", reference_id=operation_id)
            except BankError as error:
                await db.rollback()
                if error.code in ("TOTP_INVALID", "TOTP_REPLAY", "SMS_INVALID"):
                    row = await auth.owned_operation(db, user_id, operation_id)
                    locked = auth.record_failed_verification(row)
                    await db.commit()
                    if locked is not None:
                        raise locked from error
                raise HTTPException(error.status_code,
                                    detail={"code": error.code, "message": error.message}) from error
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
        auth.cancel(row)
        await db.commit()
        return _view(row)
    except CardAuthorizationError as error:
        await _fail(db, error)
