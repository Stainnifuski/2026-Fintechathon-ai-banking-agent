"""转账收款人查询接口。"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..middleware.auth import get_current_user
from ..models.account import AccountORM
from ..models.user import UserORM

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("/recipients")
async def list_recipients(
    current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    """返回其它活跃用户及其首张可收款账户，供转账表单选择。"""
    result = await db.execute(
        select(UserORM, AccountORM)
        .join(AccountORM, AccountORM.user_id == UserORM.user_id)
        .where(UserORM.user_id != current_user["user_id"])
        .where(UserORM.status == "active", AccountORM.status == "active")
        .order_by(UserORM.name, AccountORM.account_type.desc(), AccountORM.account_id)
    )
    seen: set[str] = set()
    recipients = []
    for user, account in result.all():
        if user.user_id in seen:
            continue
        seen.add(user.user_id)
        recipients.append({"user_id": user.user_id, "name": user.name, "account_id": account.account_id, "bank": "AI 模拟银行"})
    return recipients
