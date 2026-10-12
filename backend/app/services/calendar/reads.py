"""Agent 日历读取与归属校验；不改变事件、规则或导入记录。"""
from asyncio import to_thread
from datetime import datetime
from zoneinfo import ZoneInfo

from icalendar import Calendar
from sqlalchemy import select

from ...models.calendar_event import CalendarEventORM
from . import events


def metadata(row) -> dict:
    view = events.manual_view(row)
    terms = [row.title, row.location]
    if row.ics_text:
        components = Calendar.from_ical(row.ics_text).walk('VEVENT')
        base = next((c for c in components if 'RECURRENCE-ID' not in c), components[0])
        view.update(events.event_values(base, row.time_zone), source='ics',
                    recurring=any('RRULE' in c or 'RDATE' in c for c in components))
        terms = [str(c.get(key, '')) for c in components
                 if str(c.get('STATUS', '')).upper() != 'CANCELLED' for key in ('SUMMARY', 'LOCATION')]
    return {**view, '_search_terms': terms}


async def get_record(db, user_id: str, event_id: str):
    row = await db.scalar(select(CalendarEventORM).where(
        CalendarEventORM.user_id == user_id, CalendarEventORM.event_id == event_id))
    if row is None:
        raise events.CalendarError('活动不存在或无权访问', 404)
    return row


async def search_records(db, user_id: str, keyword: str, page: int, page_size: int) -> dict:
    rows = (await db.scalars(select(CalendarEventORM).where(
        CalendarEventORM.user_id == user_id).order_by(CalendarEventORM.starts_at, CalendarEventORM.event_id))).all()
    def match():
        result = []
        for row in rows:
            item = metadata(row)
            if any(keyword.casefold() in term.casefold() for term in item.pop('_search_terms')):
                result.append(item)
        offset = (page - 1) * page_size
        return dict(items=result[offset:offset + page_size], total=len(result), page=page,
                    page_size=page_size, has_more=offset + page_size < len(result))
    return await to_thread(match)


async def occurrences(row, start: datetime, end: datetime, timezone: str):
    return await to_thread(events.expand_events, [row], start, end, ZoneInfo(timezone))
