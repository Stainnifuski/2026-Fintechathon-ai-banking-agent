"""保留导入系列原文，在查询窗口展开重复日程与例外。"""
from ..audit.spans import audited
from asyncio import to_thread
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from icalendar import Calendar
import recurring_ical_events
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...database.connection import AsyncSessionLocal
from ...models.calendar_event import CalendarEventORM
from ...schemas.calendar import CalendarEventCreate

MAX_FILE_SIZE = 2 * 1024 * 1024
MAX_COMPONENTS = 1000
MAX_OCCURRENCES = 3000


class CalendarError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def utc(value: datetime) -> datetime:
    # SQLite returns naive timestamps; all stored timed events are UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def event_values(component, time_zone: str) -> dict:
    if component.errors:
        raise ValueError("事件字段解析失败")
    start = component.decoded("DTSTART")
    if not isinstance(start, (date, datetime)):
        raise ValueError("缺少有效开始时间")
    all_day = not isinstance(start, datetime)
    end = component.decoded("DTEND", None)
    duration = component.decoded("DURATION", None)
    if end is not None and duration is not None:
        raise ValueError("结束时间和持续时长不能同时设置")
    if duration is not None:
        if not isinstance(duration, timedelta) or duration <= timedelta(0):
            raise ValueError("持续时长必须大于零")
        end = start + duration
    if all_day:
        end = end or start + timedelta(days=1)
        if isinstance(end, datetime) or not isinstance(end, date):
            raise ValueError("全天事件的结束时间必须为日期")
        start = datetime.combine(start, time.min, timezone.utc)
        end = datetime.combine(end, time.min, timezone.utc)
    else:
        zone = ZoneInfo(time_zone)
        start_prop = component.get("DTSTART")
        if start.tzinfo is None and start_prop.params.get("TZID"):
            raise ValueError("无法识别事件时区")
        start = start.replace(tzinfo=zone) if start.tzinfo is None else start
        if end is not None:
            if not isinstance(end, datetime):
                raise ValueError("定时事件的结束时间必须包含具体时间")
            end_prop = component.get("DTEND")
            if end.tzinfo is None and end_prop is not None and end_prop.params.get("TZID"):
                raise ValueError("无法识别结束时间时区")
            end = end.replace(tzinfo=zone) if end.tzinfo is None else end
        start, end = utc(start), utc(end) if end is not None else None
    # Recurrence expansion emits DTEND == DTSTART for zero-duration events.
    if end == start and not all_day:
        end = None
    if end is not None and end <= start:
        raise ValueError("结束时间必须晚于开始时间")
    title = str(component.get("SUMMARY", "未命名事件")).strip() or "未命名事件"
    description = str(component.get("DESCRIPTION", ""))
    location = str(component.get("LOCATION", ""))
    if len(title) > 200 or len(description) > 10000 or len(location) > 500:
        raise ValueError("事件名称、描述或地点超过长度限制")
    return dict(title=title, description=description, location=location,
                starts_at=start, ends_at=end, all_day=all_day)


def parse_import(data: bytes, time_zone: str) -> list[dict]:
    try:
        ZoneInfo(time_zone)
        calendar = Calendar.from_ical(data.decode("utf-8-sig"))
        if calendar.name != "VCALENDAR":
            raise ValueError("文件必须为 VCALENDAR")
        file_zone = str(calendar.get("X-WR-TIMEZONE", ""))
        if file_zone:
            try:
                ZoneInfo(file_zone)
                time_zone = file_zone
            except ZoneInfoNotFoundError:
                pass
        components = calendar.walk("VEVENT")
        if not components:
            raise ValueError("文件中没有日历事件")
        if len(components) > MAX_COMPONENTS:
            raise ValueError("单次最多导入 1000 个事件组件")
        groups: dict[str, list] = {}
        for index, component in enumerate(components, 1):
            event_values(component, time_zone)
            rules = component.get("RRULE")
            if rules is not None:
                if not isinstance(rules, dict) or rules.get("FREQ", [None])[0] not in {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}:
                    raise ValueError(f"第 {index} 个事件使用了不支持的重复频率（支持每日、每周、每月、每年）")
                if int(rules.get("INTERVAL", [1])[0]) < 1:
                    raise ValueError("重复间隔必须大于零")
                if any(len(rules.get(key, [])) > 1 for key in ("BYHOUR", "BYMINUTE", "BYSECOND")):
                    raise ValueError("暂不支持一天内多次触发的重复规则")
            uid = str(component.get("UID", ""))
            identity = uid or component.to_ical().decode("utf-8")
            key = sha256(identity.encode()).hexdigest()
            groups.setdefault(key, []).append(component)
        result = []
        for key, group in groups.items():
            base = next((item for item in group if "RECURRENCE-ID" not in item), group[0])
            values = event_values(base, time_zone)
            item_calendar = Calendar()
            item_calendar.add("VERSION", "2.0")
            item_calendar.add("PRODID", "-//BankAgent//Calendar//ZH")
            item_calendar.add("X-WR-TIMEZONE", time_zone)
            for tz_component in calendar.walk("VTIMEZONE"):
                item_calendar.add_component(tz_component)
            for component in group:
                item_calendar.add_component(component)
            # Validate recurrence expressions before persisting anything.
            recurring_ical_events.of(item_calendar).between(values["starts_at"], values["starts_at"] + timedelta(days=2))
            result.append({**values, "time_zone": time_zone, "import_key": key, "ics_text": item_calendar.to_ical().decode("utf-8")})
        return result
    except (ValueError, TypeError, KeyError, OverflowError, ZoneInfoNotFoundError) as error:
        raise CalendarError(f"无法导入日历：{error}") from error


@audited("calendar")
async def create_event(user_id: str, request: CalendarEventCreate) -> dict:
    async with AsyncSessionLocal() as session:
        event = CalendarEventORM(event_id=f"CE{uuid4().hex}", user_id=user_id, **request.model_dump())
        session.add(event)
        await session.commit()
        return manual_view(event)


def manual_view(event: CalendarEventORM) -> dict:
    return dict(event_id=event.event_id, title=event.title, description=event.description,
                location=event.location, starts_at=utc(event.starts_at),
                ends_at=utc(event.ends_at) if event.ends_at else None,
                all_day=False, recurring=False, source="manual")


@audited("calendar")
async def import_events(user_id: str, data: bytes, time_zone: str) -> dict:
    if not data or len(data) > MAX_FILE_SIZE:
        raise CalendarError("请选择非空且不超过 2 MB 的 .ics 文件", 413)
    parsed = await to_thread(parse_import, data, time_zone)
    counts = dict(created=0, updated=0, skipped=0, total=len(parsed))
    async with AsyncSessionLocal() as session:
        existing = {item.import_key: item for item in (await session.execute(
            select(CalendarEventORM).where(CalendarEventORM.user_id == user_id, CalendarEventORM.import_key.is_not(None))
        )).scalars()}
        for values in parsed:
            values.pop("all_day")
            event = existing.get(values["import_key"])
            if event is None:
                session.add(CalendarEventORM(event_id=f"CE{uuid4().hex}", user_id=user_id, **values))
                counts["created"] += 1
            elif event.ics_text == values["ics_text"] and event.time_zone == values["time_zone"]:
                counts["skipped"] += 1
            else:
                for key, value in values.items():
                    setattr(event, key, value)
                counts["updated"] += 1
        try:
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise CalendarError("日历正在被另一请求更新，请重试导入", 409) from error
    return counts


@audited("calendar")
async def list_events(user_id: str, start: datetime, end: datetime, time_zone: str,
                      *, session: AsyncSession | None = None) -> list[dict]:
    if start.tzinfo is None or end.tzinfo is None or not timedelta(0) < end - start <= timedelta(days=62):
        raise CalendarError("查询时间必须包含时区，范围须大于零且不超过 62 天")
    try:
        zone = ZoneInfo(time_zone)
    except ZoneInfoNotFoundError as error:
        raise CalendarError("无法识别日历时区") from error
    if session is None:
        async with AsyncSessionLocal() as owned_session:
            rows = (await owned_session.scalars(select(CalendarEventORM).where(CalendarEventORM.user_id == user_id))).all()
    else:
        rows = (await session.scalars(select(CalendarEventORM).where(CalendarEventORM.user_id == user_id))).all()
    return await to_thread(expand_events, rows, start, end, zone)


def expand_events(rows: list[CalendarEventORM], start: datetime, end: datetime, zone: ZoneInfo) -> list[dict]:
    result = []
    for event in rows:
        if event.ics_text:
            calendar = Calendar.from_ical(event.ics_text)
            recurring = any("RRULE" in item or "RDATE" in item for item in calendar.walk("VEVENT"))
            occurrences = recurring_ical_events.of(calendar).between(start.astimezone(zone), end.astimezone(zone))
            for component in occurrences:
                if str(component.get("STATUS", "")).upper() == "CANCELLED":
                    continue
                values = event_values(component, event.time_zone)
                result.append(dict(event_id=event.event_id, **values, recurring=recurring, source="ics"))
        else:
            values = manual_view(event)
            if values["starts_at"] < end and (values["ends_at"] > start if values["ends_at"] else values["starts_at"] >= start):
                result.append(values)
        if len(result) > MAX_OCCURRENCES:
            raise CalendarError("该时间范围的事件超过 3000 条，请缩小查询范围")
    return sorted(result, key=lambda item: (item["starts_at"], item["title"]))


@audited("calendar")
async def delete_event(user_id: str, event_id: str) -> None:
    async with AsyncSessionLocal() as session:
        event = await session.get(CalendarEventORM, event_id)
        if event is None or event.user_id != user_id:
            raise CalendarError("事件不存在", 404)
        await session.delete(event)
        await session.commit()
