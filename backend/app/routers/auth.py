"""注册、登录及当前用户接口。"""

from fastapi import APIRouter, Depends, HTTPException, status
from ..middleware.auth import create_access_token, get_current_user
from ..models.user import LoginSchema, TokenSchema, UserCreateSchema, UserSchema
from ..services.bank import (
    BankError,
    authenticate_customer,
    get_customer,
    register_customer,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _raise_bank_error(error: BankError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.message) from error


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
