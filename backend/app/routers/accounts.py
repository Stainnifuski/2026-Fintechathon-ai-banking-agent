"""账户路由"""

from fastapi import APIRouter, Depends, HTTPException
from ..models.account import AccountBalanceSchema, AccountSchema
from ..middleware.auth import get_current_user
from ..services.bank import list_accounts as bank_list_accounts
from ..services.bank import list_balances as bank_list_balances

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


@router.get("/{user_id}/balance", response_model=list[AccountBalanceSchema])
async def query_balance(
    user_id: str,
    current_user: dict = Depends(get_current_user),
) -> list[AccountBalanceSchema]:
    """查询用户所有账户余额。"""

    if user_id != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="无权查询其他用户账户")
    accounts = await bank_list_balances(user_id)
    if not accounts:
        raise HTTPException(status_code=404, detail="未找到该用户的账户信息")

    return [AccountBalanceSchema(**acc) for acc in accounts]


@router.get("/{user_id}/accounts", response_model=list[AccountSchema])
async def list_accounts(
    user_id: str,
    current_user: dict = Depends(get_current_user),
) -> list[AccountSchema]:
    """获取用户账户列表。"""

    if user_id != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="无权查询其他用户账户")
    accounts = await bank_list_accounts(user_id)
    if not accounts:
        raise HTTPException(status_code=404, detail="未找到该用户的账户信息")

    return [AccountSchema(**acc) for acc in accounts]
