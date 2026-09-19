"""启动初始化入口；银行数据的实际访问仅发生在4号模拟银行内。"""

from ..services.bank.seed_service import seed_bank_data


async def seed_data() -> None:
    await seed_bank_data()
