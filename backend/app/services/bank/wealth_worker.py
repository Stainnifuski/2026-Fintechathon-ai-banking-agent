"""只结算已授权受理的理财订单，不创建交易或调整方案。"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ...models.wealth import WealthOrderORM
from .wealth_service import settle

logger = logging.getLogger(__name__)


async def settlement_tick(sessions: async_sessionmaker) -> None:
    """每笔短事务核对银行事实；多实例由账户锁及唯一分录保护。"""
    async with sessions() as db:
        keys = list(await db.scalars(select(WealthOrderORM.operation_id).where(
            WealthOrderORM.status.in_(["ACCEPTED", "PENDING_PRICING", "PENDING_CASH"]))))
    for key in keys:
        async with sessions() as db:
            try:
                await settle(db, key)
            except Exception:
                await db.rollback()
                # 不记录请求、凭据或个人账户，只报告待人工核对数量。
                logger.warning("理财结算暂未完成，将保留银行事实并重试")


async def settlement_loop(sessions: async_sessionmaker) -> None:
    """按业务时钟定期结算已受理订单，绝不延长安全授权。"""
    while True:
        await asyncio.sleep(30)
        await settlement_tick(sessions)
