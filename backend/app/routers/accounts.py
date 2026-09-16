"""账户路由"""

from fastapi import APIRouter, HTTPException

from ..models.account import AccountBalanceSchema, AccountSchema
from ..services.account_service import get_account_balance, get_user_accounts

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


@router.get("/{user_id}/balance", response_model=list[AccountBalanceSchema])
async def query_balance(user_id: str) -> list[AccountBalanceSchema]:
    """查询用户所有账户余额。"""

    accounts = await get_account_balance(user_id)
    if not accounts:
        raise HTTPException(status_code=404, detail="未找到该用户的账户信息")

    return [AccountBalanceSchema(**acc) for acc in accounts]


@router.get("/{user_id}/accounts", response_model=list[AccountSchema])
async def list_accounts(user_id: str) -> list[AccountSchema]:
    """获取用户账户列表。"""

    accounts = await get_user_accounts(user_id)
    if not accounts:
        raise HTTPException(status_code=404, detail="未找到该用户的账户信息")

    return [AccountSchema(**acc) for acc in accounts]
