"""所有模拟银行扣款共用的可用现金口径。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.wealth import WealthCashHoldORM


async def held_cash(db: AsyncSession, account_id: str) -> int:
    """只计有效占款；调用方必须先持有账户锁。"""
    return int(await db.scalar(select(func.coalesce(func.sum(WealthCashHoldORM.amount_fen), 0)).where(
        WealthCashHoldORM.account_id == account_id, WealthCashHoldORM.status == "HELD")) or 0)


async def available_cash(db: AsyncSession, account_id: str, balance_fen: int) -> int:
    """账面现金减有效占款；不会把赎回应收算成现金。"""
    return balance_fen - await held_cash(db, account_id)
