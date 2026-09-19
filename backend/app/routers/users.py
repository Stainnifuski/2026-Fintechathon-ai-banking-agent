"""转账收款人查询接口。"""

from fastapi import APIRouter, Depends
from ..middleware.auth import get_current_user
from ..services.bank import list_recipients as bank_list_recipients

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("/recipients")
async def list_recipients(
    current_user: dict = Depends(get_current_user),
) -> list[dict]:
    """返回其它活跃用户及其首张可收款账户，供转账表单选择。"""
    return await bank_list_recipients(current_user["user_id"])
