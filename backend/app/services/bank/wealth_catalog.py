"""虚构产品及固定可重放净值；不会改变任何账户资金。"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.wealth import business_day, CALENDAR_VERSION
from ...models.wealth import WealthProductORM, WealthProductVersionORM, WealthNavORM

CATALOG = (
    ("MW001", "模拟清泉现金理财", "bank_wealth", "cash", 1, 0, "0", 1, 1),
    ("MW002", "模拟远山债券理财", "bank_wealth", "bond", 2, 30, "0.002", 1, 2),
    ("MW003", "模拟海岚均衡理财", "bank_wealth", "mixed", 3, 90, "0.005", 2, 3),
    ("MF001", "模拟晨露现金基金", "open_fund", "cash", 1, 0, "0", 1, 1),
    ("MF002", "模拟青石债券基金", "open_fund", "bond", 2, 0, "0.003", 1, 2),
    ("MF003", "模拟星河混合基金", "open_fund", "mixed", 3, 0, "0.008", 2, 3),
    ("MF004", "模拟云帆权益基金", "open_fund", "equity", 4, 0, "0.01", 2, 3),
    ("MF005", "模拟光合指数基金", "open_fund", "index", 4, 0, "0.006", 2, 3),
)


async def seed_catalog(db: AsyncSession) -> None:
    """只补充未存在的产品与净值，不覆盖旧规则或历史价格。"""
    now = datetime.now(timezone.utc)
    for index, (pid, name, kind, category, risk, lock, fee, confirmation, cash) in enumerate(CATALOG):
        vid = f"{pid}:1"
        if await db.get(WealthProductORM, pid) is None:
            db.add(WealthProductORM(product_id=pid, current_version=1, status="on_sale"))
            await db.flush()
        if await db.get(WealthProductVersionORM, vid) is None:
            db.add(WealthProductVersionORM(version_id=vid, product_id=pid, version=1,
                name=name, kind=kind, category=category, risk_level=risk,
                rules={"currency": "CNY", "min_amount_fen": 10000, "max_amount_fen": 2_000_000_000,
                    "purchase_fee_rate": fee, "redeem_fee_bands": [{"days_lt": 7, "rate": "0.015"},
                    {"days_lt": 30, "rate": "0.005"}, {"days_lt": 1000000, "rate": "0"}],
                    "holding_days": lock, "confirm_business_days": confirmation,
                    "cash_business_days": cash, "cutoff": "15:00", "calendar_version": CALENDAR_VERSION,
                    "cancel_before_pricing": True, "purchase_paused": False, "redeem_paused": False},
                disclosure={"version": 1, "nature": "可变净值份额制模拟",
                    "manager": "虚构青穗模拟管理机构", "seller": "比赛模拟银行展示与模拟受理",
                    "risk": "本金可能亏损，风险匹配不保证收益", "fees": "申购费外扣；赎回按持有日数；管理托管费已含模拟净值",
                    "liquidity": "仅按模拟营业日受理，到账前不可使用", "holding_period_days": lock,
                    "pricing": "截止前当日，截止后下一模拟营业日；缺净值等待",
                    "settlement": f"计价后{confirmation}营业日确认，赎回{cash}营业日到账",
                    "cancellation": "计价日截止前且尚未计价可显式撤单",
                    "limitations": "无真实行情、分红结转、税务与真实资金连接"}, created_at=now))
            await db.flush()
        existing_dates = set((await db.scalars(select(WealthNavORM.nav_date).where(
            WealthNavORM.version_id == vid))).all())
        day, end = date(2026, 1, 1), date(2027, 12, 31)
        while day <= end:
            if business_day(day) and day not in existing_dates:
                offset = (day - date(2026, 1, 1)).days
                # 固定锯齿波明确含下跌，不能宣传未来回报。
                price = (Decimal("1.00") + Decimal(index) / 100
                    + Decimal((offset % 23) - 11) * Decimal("0.0007")).quantize(Decimal("0.00000001"))
                db.add(WealthNavORM(nav_id=f"{vid}:{day.isoformat()}", version_id=vid,
                                     nav_date=day, price=format(price, ".8f")))
            day += timedelta(days=1)
    await db.flush()
