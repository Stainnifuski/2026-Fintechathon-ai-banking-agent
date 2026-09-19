"""JWT 认证中间件"""

import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

# ──────────────────── 常量 ────────────────────

JWT_SECRET: str = os.getenv("JWT_SECRET", "dev-secret-key-change-in-production")
JWT_ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

security_scheme = HTTPBearer(auto_error=False)


# ──────────────────── Token 操作 ────────────────────


def create_access_token(
    data: dict, expires_delta: int | None = None
) -> str:
    """生成 JWT 访问令牌。

    Args:
        data: 令牌载荷数据（如 {"sub": user_id}）。
        expires_delta: 过期时间（分钟），默认使用 ACCESS_TOKEN_EXPIRE_MINUTES。

    Returns:
        编码后的 JWT 字符串。
    """
    to_encode = data.copy()
    expire_minutes = expires_delta or ACCESS_TOKEN_EXPIRE_MINUTES
    expire = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
    to_encode.update({"exp": expire})

    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    """验证 JWT 令牌并返回载荷。

    Args:
        token: JWT 字符串。

    Returns:
        解码后的载荷字典，验证失败返回 None。
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> dict:
    """FastAPI 依赖函数：从请求头提取并验证 JWT，返回当前用户信息。

    Args:
        credentials: HTTP Bearer 认证凭据。

    Returns:
        用户信息字典，至少包含 user_id。

    Raises:
        HTTPException: Token 无效时抛出 401。
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = verify_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌缺少用户信息")
    return {
        "user_id": user_id,
        "name": payload.get("name", ""),
        "role": payload.get("role", "user"),
    }
