"""理财模拟计算与交易日历；不访问数据库或模型。"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, localcontext
from .time import BEIJING

CALCULATION_VERSION = "mock-units-v1"
CALENDAR_VERSION = "mock-cn-2026-v1"
UNIT = Decimal("0.0001")
CENT = Decimal("0.01")
NAV_TICK = Decimal("0.00000001")
TZ = BEIJING
# 明确模拟固定休市日，不冒充现实交易所完整日历。
HOLIDAYS = frozenset(date(2026, 10, day) for day in range(1, 8))


def precise(value: str, tick: Decimal, *, positive: bool = True) -> Decimal:
    """只接受有限且不超约定精度的十进制字符串。"""
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("数值必须是十进制字符串")
    try:
        result = Decimal(value)
        if (not result.is_finite() or result < 0 or (positive and result == 0)
                or result > Decimal("1000000000000")
                or result != result.quantize(tick)):
            raise ValueError("数值或精度无效")
        return result
    except ArithmeticError as error:
        raise ValueError("数值无效") from error


def nav(value: str) -> Decimal:
    """读取8位净值。"""
    return precise(value, NAV_TICK)


def units(value: str, *, positive: bool = True) -> Decimal:
    """读取4位份额。"""
    return precise(value, UNIT, positive=positive)


def rate(value: str) -> Decimal:
    """读取模拟费率；费率不得超过100%。"""
    result = precise(value, NAV_TICK, positive=False)
    if result > 1:
        raise ValueError("费率超过100%")
    return result


@dataclass(frozen=True)
class PurchaseCalculation:
    gross_fen: int
    fee_fen: int
    net_fen: int
    units: str
    refund_fen: int
    rounding_yuan: str


def purchase(gross_fen: int, price: str, fee_rate: str) -> PurchaseCalculation:
    """申购：先按分计算外扣费用，再份额向下取整，尾差显式记账。"""
    if type(gross_fen) is not int or not 0 < gross_fen <= 2_000_000_000:
        raise ValueError("金额必须为正整数分且不超过业务上限")
    with localcontext() as ctx:
        ctx.prec = 48
        gross = Decimal(gross_fen) / 100
        price_value = nav(price)
        net = (gross / (1 + rate(fee_rate))).quantize(CENT, ROUND_DOWN)
        quantity = (net / price_value).quantize(UNIT, ROUND_DOWN)
        if quantity <= 0:
            raise ValueError("金额不足最小份额")
        residue = net - quantity * price_value
        refund = residue.quantize(CENT, ROUND_DOWN)
        return PurchaseCalculation(gross_fen, int((gross - net) * 100),
            int(net * 100), format(quantity, ".4f"), int(refund * 100),
            format(residue - refund, ".12f"))


@dataclass(frozen=True)
class RedemptionCalculation:
    gross_fen: int
    fee_fen: int
    cash_fen: int
    cost_fen: int
    realized_pnl_fen: int


def redeem(quantity: str, price: str, fee_rate: str, *, lot_units: str,
           lot_cost_fen: int) -> RedemptionCalculation:
    """按批次赎回；最后一笔释放全部剩余成本，不遗留成本尾差。"""
    with localcontext() as ctx:
        ctx.prec = 48
        requested, total = units(quantity), units(lot_units)
        if requested > total or type(lot_cost_fen) is not int or lot_cost_fen < 0:
            raise ValueError("批次份额或成本无效")
        gross = (requested * nav(price)).quantize(CENT, ROUND_HALF_UP)
        fee = (gross * rate(fee_rate)).quantize(CENT, ROUND_HALF_UP)
        cost = lot_cost_fen if requested == total else int(
            (Decimal(lot_cost_fen) * requested / total).to_integral_value(ROUND_DOWN))
        cash = int((gross - fee) * 100)
        return RedemptionCalculation(int(gross * 100), int(fee * 100), cash,
                                     cost, cash - cost)


def business_day(day: date, calendar_version: str = CALENDAR_VERSION) -> bool:
    """判断固定版本模拟营业日。"""
    from .wealth_calendar import CALENDAR_VERSION as CURRENT_CALENDAR,day_info
    if calendar_version==CURRENT_CALENDAR:return day_info(day)['trading']
    if calendar_version not in {CALENDAR_VERSION, "synthetic-weekdays-v1"}:
        raise ValueError("未知营业日历")
    return day.weekday() < 5 and (calendar_version == "synthetic-weekdays-v1" or day not in HOLIDAYS)


def advance_business(day: date, days: int, calendar_version: str = CALENDAR_VERSION) -> date:
    """从当前日期向后推进指定营业日；0也会顺延休市日。"""
    if type(days) is not int or not 0 <= days <= 366:
        raise ValueError("营业日偏移无效")
    while not business_day(day, calendar_version):
        day += timedelta(days=1)
    while days:
        day += timedelta(days=1)
        if business_day(day, calendar_version):
            days -= 1
    return day


def pricing_date(accepted_at: datetime, cutoff: str = "15:00", calendar_version: str = CALENDAR_VERSION) -> date:
    """营业日截止前当日计价，否则下一营业日；输入必须带时区。"""
    if accepted_at.tzinfo is None:
        raise ValueError("受理时间必须带时区")
    local = accepted_at.astimezone(TZ)
    cutoff_time = time.fromisoformat(cutoff)
    day = local.date()
    if business_day(day, calendar_version) and local.time() >= cutoff_time:
        day += timedelta(days=1)
    return advance_business(day, 0, calendar_version)
