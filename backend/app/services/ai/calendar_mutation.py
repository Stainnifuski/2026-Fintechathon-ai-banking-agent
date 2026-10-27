"""User-confirmed calendar mutations prepared by NOVA."""
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from sqlalchemy import select

from ...domain.time import utc_iso, utc_now
from ...models import CalendarEventORM
from ...schemas.nova import StrictModel
from ..calendar import events, reads
from .card_subscription_tools import CardTaskError, _owned_session


class ManipulateCalendarArguments(StrictModel):
    action: Literal['add', 'modify', 'delete'] = Field(description='添加、修改或删除日程')
    task_entry_id: str | None = Field(default=None, max_length=64, description='续接未完成日程任务时填写其内部任务 ID')
    match_title: str | None = Field(default=None, max_length=200, description='按现有事件名称筛选，可为部分名称')
    match_date: str | None = Field(default=None, description='按现有事件发生日期筛选，YYYY-MM-DD')
    match_time: str | None = Field(default=None, description='按现有事件开始时间筛选，HH:MM')
    match_date_from: str | None = Field(default=None, description='现有事件发生日期范围的起始日，含当天')
    match_date_to: str | None = Field(default=None, description='现有事件发生日期范围的结束日，含当天')
    match_time_from: str | None = Field(default=None, description='现有事件开始时间范围的起始时刻，含该时刻')
    match_time_to: str | None = Field(default=None, description='现有事件开始时间范围的结束时刻，含该时刻')
    event_ids: list[str] | None = Field(default=None, max_length=50, description='已有事件的内部 ID，可指定多个')
    title: str | None = Field(default=None, max_length=200, description='新增或修改后的事件名称')
    start_date: str | None = Field(default=None, description='新增或修改后的开始日期，YYYY-MM-DD')
    start_time: str | None = Field(default=None, description='新增或修改后的开始时间，HH:MM')
    end_date: str | None = Field(default=None, description='新增或修改后的结束日期，YYYY-MM-DD')
    end_time: str | None = Field(default=None, description='新增或修改后的结束时间，HH:MM；省略表示时间点或保留原持续时间')
    location: str | None = Field(default=None, max_length=500, description='新增或修改后的地点')
    description: str | None = Field(default=None, max_length=2000, description='新增或修改后的备注')
    timezone: str | None = Field(default=None, max_length=80, description='用于解释日期和时间的 IANA 时区')

    @field_validator('match_date', 'match_date_from', 'match_date_to', 'start_date', 'end_date')
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            date.fromisoformat(value)
        return value

    @field_validator('match_time', 'match_time_from', 'match_time_to', 'start_time', 'end_time')
    @classmethod
    def valid_time(cls, value):
        if value is not None:
            time.fromisoformat(value)
            if len(value) != 5:
                raise ValueError('时间须为 HH:MM')
        return value

    @field_validator('timezone')
    @classmethod
    def valid_zone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ValueError, ZoneInfoNotFoundError):
                raise ValueError('请使用合法 IANA 时区') from None
        return value


CHANGE_FIELDS = ('title', 'start_date', 'start_time', 'end_date', 'end_time', 'location', 'description')
FILTER_FIELDS = ('match_title', 'match_date', 'match_time', 'match_date_from', 'match_date_to',
                 'match_time_from', 'match_time_to', 'event_ids')


def _snapshot(row: CalendarEventORM) -> dict:
    return {'updated_at': utc_iso(events.utc(row.updated_at)), 'starts_at': utc_iso(events.utc(row.starts_at)),
            'ends_at': utc_iso(events.utc(row.ends_at)) if row.ends_at else None,
            'title': row.title, 'location': row.location, 'description': row.description}


def _target(row: CalendarEventORM, zone: str) -> dict:
    z = ZoneInfo(zone)
    return {'event_id': row.event_id, 'title': row.title,
            'starts_at_local': events.utc(row.starts_at).astimezone(z).isoformat(),
            'ends_at_local': events.utc(row.ends_at).astimezone(z).isoformat() if row.ends_at else None,
            'location': row.location, 'recurring': bool(row.ics_text)}


async def _matches(db, user_id: str, filters: dict, zone: str) -> list[CalendarEventORM]:
    rows = (await db.scalars(select(CalendarEventORM).where(CalendarEventORM.user_id == user_id)
                             .order_by(CalendarEventORM.starts_at, CalendarEventORM.event_id))).all()
    if filters.get('event_ids'):
        requested = set(filters['event_ids'])
        rows = [row for row in rows if row.event_id in requested]
        if len(rows) != len(requested):
            raise CardTaskError('部分日程不存在或无权访问', 'CALENDAR_EVENT_NOT_FOUND', 404)
    if filters.get('match_title'):
        needle = filters['match_title'].casefold()
        rows = [row for row in rows if any(needle in term.casefold() for term in reads.metadata(row)['_search_terms'])]
    if any(filters.get(key) for key in ('match_date', 'match_time', 'match_date_from', 'match_date_to',
                                       'match_time_from', 'match_time_to')):
        z = ZoneInfo(zone)
        wanted_day = date.fromisoformat(filters['match_date']) if filters.get('match_date') else None
        wanted_time = time.fromisoformat(filters['match_time']) if filters.get('match_time') else None
        day_from = date.fromisoformat(filters['match_date_from']) if filters.get('match_date_from') else None
        day_to = date.fromisoformat(filters['match_date_to']) if filters.get('match_date_to') else None
        time_from = time.fromisoformat(filters['match_time_from']) if filters.get('match_time_from') else None
        time_to = time.fromisoformat(filters['match_time_to']) if filters.get('match_time_to') else None
        if day_from and day_to and day_to < day_from:
            raise CardTaskError('结束日期不得早于开始日期', 'CALENDAR_INVALID_FILTER')
        if time_from and time_to and time_to < time_from:
            raise CardTaskError('结束时间不得早于开始时间', 'CALENDAR_INVALID_FILTER')
        def matching(local):
            local_day = local.date()
            local_time = local.time().replace(second=0, microsecond=0, tzinfo=None)
            return ((wanted_day is None or local_day == wanted_day) and
                    (day_from is None or local_day >= day_from) and (day_to is None or local_day <= day_to) and
                    (wanted_time is None or local_time == wanted_time) and
                    (time_from is None or local_time >= time_from) and (time_to is None or local_time <= time_to))
        selected = []
        for row in rows:
            if row.ics_text and (wanted_day or day_from or day_to):
                first_day = wanted_day or day_from or (day_to - timedelta(days=61))
                last_day = wanted_day or day_to or (day_from + timedelta(days=61))
                if (last_day - first_day).days > 61:
                    raise CardTaskError('重复日程查询范围最多 62 天', 'CALENDAR_FILTER_TOO_WIDE')
                start = datetime.combine(first_day, time.min, z)
                occurrences = await reads.occurrences(row, start, datetime.combine(last_day + timedelta(days=1), time.min, z), zone)
                if any(matching(item['starts_at'].astimezone(z)) for item in occurrences):
                    selected.append(row)
            elif matching(events.utc(row.starts_at).astimezone(z)):
                selected.append(row)
        rows = selected
    if len(rows) > 50:
        raise CardTaskError('匹配超过 50 项，请缩小筛选范围', 'CALENDAR_TOO_MANY_MATCHES')
    return rows


async def manipulate_calendar_event(arguments: ManipulateCalendarArguments, context):
    zone = arguments.timezone or (context.page.calendar.timezone if context.page.calendar else 'Asia/Shanghai')
    filters = {key: getattr(arguments, key) for key in FILTER_FIELDS if getattr(arguments, key) is not None}
    changes = {key: getattr(arguments, key) for key in CHANGE_FIELDS if getattr(arguments, key) is not None}
    if arguments.action != 'add' and not filters:
        return {'error': 'MISSING_FILTER', 'message': '修改或删除日程时，请至少指定名称、日期、时间或事件 ID'}
    if arguments.action == 'modify' and not changes:
        return {'error': 'MISSING_CHANGES', 'message': '请指定要修改的日程字段'}
    rows = [] if arguments.action == 'add' else await _matches(context.db, context.user_id, filters, zone)
    if arguments.action != 'add' and not rows:
        return {'error': 'NO_MATCHES', 'message': '没有找到符合筛选条件的日程'}
    if arguments.action == 'modify' and any(row.ics_text for row in rows):
        return {'error': 'IMPORTED_EVENT_NOT_EDITABLE', 'message': '导入或重复日程暂不能修改；可删除整个系列后重新添加'}
    if not (context.session_id and context.request_id):
        return {'error': 'NO_CONVERSATION', 'message': '请在聊天会话中操作日程'}
    session = await _owned_session(context.db, context.user_id, context.session_id, create=True)
    state = json.loads(json.dumps(session.state or {}))
    entries = list(state.get('calendar_tasks', []))
    existing = next((item for item in entries if item.get('kind') == 'mutation' and
                     item.get('status') == 'PENDING' and not item.get('closed') and
                     (item.get('entry_id') == arguments.task_entry_id if arguments.task_entry_id else
                      item.get('origin_request_id') == context.request_id and item.get('action') == arguments.action)), None)
    if arguments.task_entry_id and (existing is None or existing.get('action') != arguments.action):
        return {'error': 'CALENDAR_TASK_NOT_FOUND', 'message': '未找到可续接的日程任务卡'}
    if not existing and not arguments.task_entry_id and not context.task_switch_approved and not context.execution_step:
        from .task_flow import active, propose
        current = await active(context)
        if current and current.get('entry') and current['type'] == 'calendar' and current.get('request_id') != context.request_id:
            pending = await propose(context, '新的日程操作')
            if pending:
                return pending
    if existing:
        if arguments.action != 'add':
            return {'error': 'CALENDAR_TASK_ACTION_UNSUPPORTED', 'message': '此日程任务暂不能通过聊天续接'}
        existing['changes'] = {**existing.get('changes', {}), **changes}
        existing['timezone'] = arguments.timezone or existing['timezone']
        session.state = state
        await context.db.commit()
        return {'task_updated': True, 'entry_id': existing['entry_id'], 'action': 'add',
                'changes': existing['changes'], 'next_step': '已更新原日程任务卡；请用户检查并点击确认。'}
    entry = {'kind': 'mutation', 'entry_id': uuid4().hex, 'origin_request_id': context.request_id,
             'action': arguments.action, 'timezone': zone, 'filters': filters, 'changes': changes,
             'targets': [_target(row, zone) for row in rows],
             'snapshots': {row.event_id: _snapshot(row) for row in rows}, 'status': 'PENDING', 'closed': False}
    state['calendar_tasks'] = (entries + [entry])[-30:]
    session.state = state
    await context.db.commit()
    return {'task_opened': True, 'entry_id': entry['entry_id'], 'action': arguments.action,
            'matched_count': len(rows), 'targets': entry['targets'], 'changes': changes,
            'next_step': '已在聊天中打开日程任务卡。请用户检查目标和改动，并主动点击确认按钮后才执行。'}


def mutation_view(entry: dict) -> dict:
    return {key: entry.get(key) for key in ('kind', 'entry_id', 'origin_request_id', 'action', 'timezone',
            'filters', 'changes', 'targets', 'status', 'result')}


def _local_start(changes: dict, zone: str, original: datetime | None = None) -> datetime | None:
    if original is None and not (changes.get('start_date') and changes.get('start_time')):
        return None
    local = original.astimezone(ZoneInfo(zone)) if original else None
    day = date.fromisoformat(changes['start_date']) if changes.get('start_date') else local.date()
    clock = time.fromisoformat(changes['start_time']) if changes.get('start_time') else local.timetz().replace(tzinfo=None)
    return datetime.combine(day, clock, ZoneInfo(zone)).astimezone(timezone.utc)


def _times(changes: dict, zone: str, row: CalendarEventORM | None) -> tuple[datetime, datetime | None]:
    old_start = events.utc(row.starts_at) if row else None
    old_end = events.utc(row.ends_at) if row and row.ends_at else None
    start = _local_start(changes, zone, old_start)
    if start is None:
        raise CardTaskError('请填写事件名称、日期和开始时间', 'CALENDAR_MISSING_FIELDS')
    if changes.get('end_time'):
        day = date.fromisoformat(changes.get('end_date') or start.astimezone(ZoneInfo(zone)).date().isoformat())
        end = datetime.combine(day, time.fromisoformat(changes['end_time']), ZoneInfo(zone)).astimezone(timezone.utc)
    elif row and old_end:
        end = start + (old_end - old_start)
    else:
        end = None
    if end is not None and end <= start:
        raise CardTaskError('结束时间必须晚于开始时间', 'CALENDAR_INVALID_TIME')
    return start, end


async def confirm_calendar_task(db, *, user_id: str, session_id: str, entry_id: str, changes: dict | None = None) -> list[dict]:
    from .calendar_tools import chat_calendar_tasks
    session = await _owned_session(db, user_id, session_id)
    state = json.loads(json.dumps(session.state or {})) if session else {}
    entry = next((item for item in state.get('calendar_tasks', []) if item.get('entry_id') == entry_id and item.get('kind') == 'mutation'), None)
    if entry is None or entry.get('closed') or entry.get('status') != 'PENDING':
        raise CardTaskError('日程任务不存在或已完成', 'CALENDAR_TASK_NOT_FOUND', 404)
    values = dict(entry.get('changes') or {})
    if changes:
        values.update(changes)
    action, zone = entry['action'], entry['timezone']
    if 'title' in values and not values['title'].strip():
        raise CardTaskError('事件名称不能为空', 'CALENDAR_MISSING_TITLE')
    if values.get('end_date') and not values.get('end_time'):
        raise CardTaskError('填写结束日期时还需填写结束时间', 'CALENDAR_INVALID_TIME')
    result = []
    if action == 'add':
        if not values.get('title') or not values.get('title').strip():
            raise CardTaskError('请填写事件名称', 'CALENDAR_MISSING_TITLE')
        start, end = _times(values, zone, None)
        row = CalendarEventORM(event_id=f'CE{uuid4().hex}', user_id=user_id, title=values['title'].strip(),
                               starts_at=start, ends_at=end, location=values.get('location', ''),
                               description=values.get('description', ''), time_zone=zone)
        db.add(row)
        result.append(_target(row, zone))
    else:
        ids = [target['event_id'] for target in entry['targets']]
        rows = (await db.scalars(select(CalendarEventORM).where(CalendarEventORM.user_id == user_id,
                    CalendarEventORM.event_id.in_(ids)).with_for_update())).all()
        by_id = {row.event_id: row for row in rows}
        if len(by_id) != len(ids) or any(_snapshot(by_id[event_id]) != snapshot for event_id, snapshot in entry['snapshots'].items()):
            raise CardTaskError('日程已变化，请重新筛选后操作', 'CALENDAR_STALE_TASK', 409)
        for event_id in ids:
            row = by_id[event_id]
            if action == 'delete':
                result.append(_target(row, zone))
                await db.delete(row)
            else:
                if row.ics_text:
                    raise CardTaskError('导入日程暂不能修改', 'IMPORTED_EVENT_NOT_EDITABLE')
                start, end = _times(values, zone, row)
                row.starts_at, row.ends_at = start, end
                for key in ('title', 'location', 'description'):
                    if key in values:
                        setattr(row, key, values[key])
                result.append(_target(row, zone))
    entry['status'] = 'COMPLETED'
    entry['result'] = result
    entry['changes'] = values
    session.state = state
    await db.commit()
    return await chat_calendar_tasks(db, user_id, state)
