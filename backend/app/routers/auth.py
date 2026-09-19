"""注册、登录及当前用户接口。"""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..middleware.auth import create_access_token, get_current_user
from ..models.account import AccountORM
from ..models.user import LoginSchema, TokenSchema, UserCreateSchema, UserORM, UserSchema
from ..security.passwords import hash_password, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _public_user(user: UserORM) -> UserSchema:
    return UserSchema.model_validate(user)


@router.post("/register", response_model=TokenSchema, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreateSchema, db: AsyncSession = Depends(get_db)) -> TokenSchema:
    """注册用户并创建一张余额为零的活期账户。"""
    phone = payload.phone.strip()
    if len(phone) < 6:
        raise HTTPException(status_code=422, detail="请输入有效手机号")
    if len(payload.password) < 8:
        raise HTTPException(status_code=422, detail="密码至少需要 8 位")
    exists = await db.execute(select(UserORM).where(UserORM.phone == phone))
    if exists.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="该手机号已注册")

    user_id = f"U{uuid4().hex[:10].upper()}"
    account_id = f"A{uuid4().hex[:10].upper()}"
    user = UserORM(
        user_id=user_id,
        name=payload.name.strip(),
        phone=phone,
        id_card="未填写",
        password_hash=hash_password(payload.password),
        risk_level=payload.risk_level,
        status="active",
    )
    account = AccountORM(
        account_id=account_id,
        user_id=user_id,
        account_type="savings",
        card_number=f"6214****{account_id[-4:]}",
        balance=0.0,
        daily_transfer_used=0.0,
        daily_transfer_limit=50000.0,
        status="active",
    )
    db.add(user)
    await db.flush()
    db.add(account)
    await db.commit()
    await db.refresh(user)
    return TokenSchema(
        access_token=create_access_token({"sub": user.user_id, "name": user.name}),
        user=_public_user(user),
    )


@router.post("/login", response_model=TokenSchema)
async def login(payload: LoginSchema, db: AsyncSession = Depends(get_db)) -> TokenSchema:
    """使用手机号和密码登录。"""
    result = await db.execute(select(UserORM).where(UserORM.phone == payload.phone.strip()))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="手机号或密码错误")
    if user.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="用户已被停用")
    return TokenSchema(
        access_token=create_access_token({"sub": user.user_id, "name": user.name}),
        user=_public_user(user),
    )


@router.get("/me", response_model=UserSchema)
async def me(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> UserSchema:
    result = await db.execute(select(UserORM).where(UserORM.user_id == current_user["user_id"]))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return _public_user(user)
