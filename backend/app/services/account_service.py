"""账户业务逻辑"""

from sqlalchemy import select

from ..database.connection import AsyncSessionLocal
from ..models.account import AccountORM


async def get_user_accounts(user_id: str) -> list[dict]:
    """获取用户所有账户信息。

    Args:
        user_id: 用户ID。

    Returns:
        账户信息字典列表。
    """
    async with AsyncSessionLocal() as session:
        stmt = select(AccountORM).where(AccountORM.user_id == user_id)
        result = await session.execute(stmt)
        accounts = result.scalars().all()
        return [
            {
                "account_id": acc.account_id,
                "user_id": acc.user_id,
                "account_type": acc.account_type,
                "card_number": acc.card_number,
                "balance": acc.balance,
                "daily_transfer_used": acc.daily_transfer_used,
                "daily_transfer_limit": acc.daily_transfer_limit,
                "status": acc.status,
            }
            for acc in accounts
        ]


async def get_account_balance(
    user_id: str, account_type: str | None = None
) -> list[dict]:
    """查询用户账户余额。

    Args:
        user_id: 用户ID。
        account_type: 可选，账户类型过滤（savings/checking/credit）。

    Returns:
        账户余额信息列表。
    """
    async with AsyncSessionLocal() as session:
        stmt = select(AccountORM).where(AccountORM.user_id == user_id)
        if account_type:
            stmt = stmt.where(AccountORM.account_type == account_type)
        result = await session.execute(stmt)
        accounts = result.scalars().all()
        return [
            {
                "account_id": acc.account_id,
                "account_type": acc.account_type,
                "balance": acc.balance,
                "card_number": acc.card_number,
                "daily_transfer_used": acc.daily_transfer_used,
                "daily_transfer_limit": acc.daily_transfer_limit,
            }
            for acc in accounts
        ]


async def check_balance_sufficient(account_id: str, amount: float) -> bool:
    """检查账户余额是否充足。

    Args:
        account_id: 账户ID。
        amount: 需要的金额。

    Returns:
        余额是否充足。
    """
    async with AsyncSessionLocal() as session:
        stmt = select(AccountORM).where(AccountORM.account_id == account_id)
        result = await session.execute(stmt)
        account = result.scalar_one_or_none()
        if account is None:
            return False
        return account.balance >= amount


async def get_daily_transfer_used(account_id: str) -> float:
    """获取今日已转金额。

    Args:
        account_id: 账户ID。

    Returns:
        今日已转账金额。
    """
    async with AsyncSessionLocal() as session:
        stmt = select(AccountORM.daily_transfer_used).where(
            AccountORM.account_id == account_id
        )
        result = await session.execute(stmt)
        used = result.scalar_one_or_none()
        return used if used is not None else 0.0


async def update_daily_transfer_used(account_id: str, amount: float) -> None:
    """更新今日已转金额（累加）。

    Args:
        account_id: 账户ID。
        amount: 本次转账金额。
    """
    async with AsyncSessionLocal() as session:
        stmt = select(AccountORM).where(AccountORM.account_id == account_id)
        result = await session.execute(stmt)
        account = result.scalar_one_or_none()
        if account:
            account.daily_transfer_used += amount
            await session.commit()
