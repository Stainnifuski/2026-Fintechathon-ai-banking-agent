"""UTC事件瞬时与北京时间业务日期；不读取主机本地时区。"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = "Asia/Shanghai"
BEIJING = ZoneInfo(BUSINESS_TIMEZONE)


def utc_now() -> datetime:
    """获取现实UTC时间，不接受模拟时钟。"""
    return datetime.now(timezone.utc)


def as_utc(value: datetime, *, legacy_utc: bool = False) -> datetime:
    """仅数据库已定义UTC的SQLite字段允许补UTC，未知naive拒绝猜测。"""
    if value.tzinfo is None:
        if not legacy_utc:
            raise ValueError("事件时间缺少来源时区")
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utc_iso(value: datetime, *, legacy_utc: bool = False) -> str:
    """输出带Z的同一UTC瞬时。"""
    return as_utc(value, legacy_utc=legacy_utc).isoformat().replace("+00:00", "Z")
