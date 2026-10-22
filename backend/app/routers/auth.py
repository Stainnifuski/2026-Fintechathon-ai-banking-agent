"""注册、登录及当前用户接口。"""

from ..schemas.bank import BANK_ERROR_RESPONSES

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..middleware.auth import create_access_token, get_current_user
from ..models.user import (
    LoginSchema,
    SmsChallengeSchema,
    SmsInboxMessage,
    SmsSendRequest,
    TokenSchema,
    TotpConfirmRequest,
    TotpEnrollSchema,
    TotpStatusSchema,
    UserCreateSchema,
    UserSchema,
)
from ..services.bank import (
    BankError,
    authenticate_customer,
    begin_totp_enrollment,
    confirm_totp_enrollment,
    get_customer,
    register_customer,
    send_sms_challenge,
    sms_dev_inbox,
    totp_status,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"], responses=BANK_ERROR_RESPONSES)
from ..services.audit.context import bind_user


def _raise_bank_error(error: BankError) -> None:
    raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": error.message, **getattr(error, "security_detail", {})}) from error


@router.post("/register", response_model=TokenSchema, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreateSchema) -> TokenSchema:
    """注册用户并创建一张余额为零的活期账户。"""
    phone = payload.phone.strip()
    if len(phone) < 6:
        raise HTTPException(status_code=422, detail="请输入有效手机号")
    if len(payload.password) < 8:
        raise HTTPException(status_code=422, detail="密码至少需要 8 位")
    try:
        user = await register_customer(
            name=payload.name,
            phone=phone,
            password=payload.password,
            risk_level=payload.risk_level,
        )
    except BankError as error:
        _raise_bank_error(error)
    bind_user(user["user_id"])
    return TokenSchema(
        access_token=create_access_token({"sub": user["user_id"], "name": user["name"]}),
        user=UserSchema(**user),
    )


@router.post("/login", response_model=TokenSchema)
async def login(payload: LoginSchema) -> TokenSchema:
    """使用手机号和密码登录。"""
    try:
        user = await authenticate_customer(payload.phone, payload.password)
    except BankError as error:
        _raise_bank_error(error)
    bind_user(user["user_id"])
    return TokenSchema(
        access_token=create_access_token({"sub": user["user_id"], "name": user["name"]}),
        user=UserSchema(**user),
    )


@router.get("/me", response_model=UserSchema)
async def me(current_user: dict = Depends(get_current_user)) -> UserSchema:
    user = await get_customer(current_user["user_id"])
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return UserSchema(**user)


@router.get("/totp/status", response_model=TotpStatusSchema)
async def totp_status_endpoint(
    current_user: dict = Depends(get_current_user),
) -> TotpStatusSchema:
    """查询当前用户是否已绑定 TOTP 验证器。"""
    try:
        return TotpStatusSchema(**await totp_status(current_user["user_id"]))
    except BankError as error:
        _raise_bank_error(error)


@router.post("/totp/enroll", response_model=TotpEnrollSchema)
async def totp_enroll(
    current_user: dict = Depends(get_current_user),
) -> TotpEnrollSchema:
    """开始绑定：生成密钥与二维码，供验证器 App 扫码。"""
    try:
        return TotpEnrollSchema(**await begin_totp_enrollment(current_user["user_id"]))
    except BankError as error:
        _raise_bank_error(error)


@router.post("/totp/confirm", response_model=TotpStatusSchema)
async def totp_confirm(
    payload: TotpConfirmRequest,
    current_user: dict = Depends(get_current_user),
) -> TotpStatusSchema:
    """确认绑定：校验一次验证码后正式启用 TOTP。"""
    try:
        return TotpStatusSchema(
            **await confirm_totp_enrollment(current_user["user_id"], payload.code)
        )
    except BankError as error:
        _raise_bank_error(error)


@router.post("/sms/send", response_model=SmsChallengeSchema)
async def sms_send(
    payload: SmsSendRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SmsChallengeSchema:
    """向本人登记手机号发送绑定到指定操作的短信验证码（验证器之外的第二因素）。"""
    try:
        return SmsChallengeSchema(**await send_sms_challenge(
            db, current_user["user_id"], payload.purpose, payload.reference_id
        ))
    except BankError as error:
        await db.rollback()
        _raise_bank_error(error)


@router.get("/sms/dev-inbox", response_model=list[SmsInboxMessage])
async def sms_dev_inbox_endpoint(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SmsInboxMessage]:
    """仅模拟短信通道：查看本人最近收到的模拟短信，生产通道下不可用。"""
    try:
        return [SmsInboxMessage(**m) for m in await sms_dev_inbox(db, current_user["user_id"])]
    except BankError as error:
        _raise_bank_error(error)


@router.get("/security/status")
async def security_status(current_user: dict = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)) -> dict:
    """Read only the authenticated owner's locks, never verification secrets."""
    from ..security.circuit_breaker import status as lock_status
    return await lock_status(db, current_user["user_id"])
