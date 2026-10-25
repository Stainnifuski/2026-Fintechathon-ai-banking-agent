"""本人日历查询工具；事件系列搜索与实际发生时间分开返回。"""
from datetime import date, datetime, time, timedelta, timezone as utc_zone
import json
import re
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from sqlalchemy import select

from ...domain.time import utc_iso, utc_now
from ...schemas.nova import StrictModel
from ...models import CalendarEventORM
from ...schemas.calendar import CalendarEventCreate
from ..calendar import events, reads


class WindowArguments(StrictModel):
    start_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    end_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    timezone: str | None = Field(default=None, max_length=80)

    @field_validator('start_date', 'end_date')
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            date.fromisoformat(value)
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


class ListArguments(WindowArguments):
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=50)


class SearchArguments(StrictModel):
    keyword: str = Field(min_length=1, max_length=200)
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=10, ge=1, le=20)

    @field_validator('keyword')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('活动名称不能为空')
        return value.strip()


class DetailArguments(WindowArguments):
    event_id: str | None = Field(default=None, min_length=1, max_length=40)
    occurrence_start: datetime | None = Field(default=None, strict=False)
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=50)
    description_offset: int = Field(default=0, ge=0, le=10000)
    description_limit: int = Field(default=2000, ge=1, le=4000)

    @field_validator('occurrence_start')
    @classmethod
    def aware(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError('发生时间须带时区')
        return value


class CreateEventArguments(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=200, description='简短事件名称，概括用户要安排的事')
    start_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$', description='开始日期，按当前时间换算为YYYY-MM-DD')
    start_time: str | None = Field(default=None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$', description='开始时间HH:MM（24小时制）')
    end_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    end_time: str | None = Field(default=None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    location: str | None = Field(default=None, min_length=1, max_length=500, description='地点，逐字摘自用户原话')
    description: str | None = Field(default=None, min_length=1, max_length=2000, description='备注，逐字摘自用户原话')
    timezone: str | None = Field(default=None, max_length=80)
    time_quote: str | None = Field(default=None, max_length=80, description='用户原话中表示日期/时间的片段，逐字摘录，如“明天下午三点”')

    @field_validator('start_date', 'end_date')
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            date.fromisoformat(value)
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


def zone_name(arguments, context):
    return arguments.timezone or (context.page.calendar.timezone if context.page.calendar else 'Asia/Shanghai')


def window(arguments, context, *, default_date=None, default_days=1):
    zone = zone_name(arguments, context)
    selected = context.page.calendar.selected_date if context.page.calendar else None
    first = date.fromisoformat(arguments.start_date or default_date or selected or utc_now().astimezone(ZoneInfo(zone)).date().isoformat())
    last = date.fromisoformat(arguments.end_date) if arguments.end_date else first + timedelta(days=default_days)
    start = datetime.combine(first, time.min, ZoneInfo(zone))
    end = datetime.combine(last, time.min, ZoneInfo(zone))
    if not timedelta(0) < end.astimezone(utc_zone.utc) - start.astimezone(utc_zone.utc) <= timedelta(days=62):
        raise events.CalendarError('查询范围须大于零且不超过62天')
    return start, end, zone


def view(item, zone, *, description_offset=0, description_limit=400):
    result = dict(item)
    result.pop('_search_terms', None)
    description = result['description']
    result['description'] = description[description_offset:description_offset + description_limit]
    result['description_offset'] = description_offset
    result['description_total_length'] = len(description)
    result['description_has_more'] = description_offset + description_limit < len(description)
    if item['all_day']:
        result['start_date'] = item['starts_at'].date().isoformat()
        result['end_date_exclusive'] = item['ends_at'].date().isoformat() if item['ends_at'] else None
    else:
        result['starts_at_local'] = item['starts_at'].astimezone(ZoneInfo(zone)).isoformat()
        result['ends_at_local'] = item['ends_at'].astimezone(ZoneInfo(zone)).isoformat() if item['ends_at'] else None
    result['occurrence_key'] = f"{item['event_id']}@{utc_iso(item['starts_at'])}"
    return result


def envelope(source, data, zone):
    return dict(source=source, data_kind='user_calendar', timezone=zone, as_of=utc_iso(utc_now()), data=data)


def page_result(items, page, size):
    offset = (page - 1) * size
    return dict(items=items[offset:offset + size], total=len(items), page=page, page_size=size,
                has_more=offset + size < len(items))


async def list_calendar(arguments, context):
    start, end, zone = window(arguments, context)
    items = await events.list_events(context.user_id, start, end, zone, session=context.db)
    data = page_result([view(item, zone) for item in items], arguments.page, arguments.page_size)
    data['query'] = dict(start=start.isoformat(), end_exclusive=end.isoformat())
    return envelope('calendar.events', data, zone)


async def search_calendar(arguments, context):
    zone = context.page.calendar.timezone if context.page.calendar else 'Asia/Shanghai'
    data = await reads.search_records(context.db, context.user_id, arguments.keyword, arguments.page, arguments.page_size)
    data['items'] = [{**view(item, zone), 'time_meaning': 'series_original_start' if item['recurring'] else 'event_start'} for item in data['items']]
    data['keyword'] = arguments.keyword
    data['search_scope'] = 'all_saved_event_titles_and_locations'
    return envelope('calendar.search', data, zone)


async def detail_calendar(arguments, context):
    selected = context.page.calendar.selected_event if context.page.calendar else None
    event_id = arguments.event_id or (selected.event_id if selected else None)
    if event_id is None:
        return dict(error='MISSING_SELECTION', message='请指定活动，或在日历中选择活动')
    row = await reads.get_record(context.db, context.user_id, event_id)
    meta = reads.metadata(row)
    zone = zone_name(arguments, context)
    target = arguments.occurrence_start
    explicit_range = arguments.start_date is not None or arguments.end_date is not None
    if arguments.event_id is None and selected and 'occurrence_start' not in arguments.model_fields_set and not explicit_range:
        target = selected.starts_at
    if target is not None:
        # 兼容全天原日期与不同时区；再精确匹配该次开始时间。
        day = target.astimezone(ZoneInfo(zone)).date()
        start, end, zone = window(WindowArguments(start_date=(day - timedelta(days=1)).isoformat(),
            end_date=(day + timedelta(days=2)).isoformat(), timezone=zone), context)
    else:
        default = (utc_now().astimezone(ZoneInfo(zone)).date() if meta['recurring']
                   else meta['starts_at'].date() if meta['all_day']
                   else meta['starts_at'].astimezone(ZoneInfo(zone)).date())
        start, end, zone = window(arguments, context, default_date=default.isoformat(), default_days=30 if meta['recurring'] else 1)
    items = await reads.occurrences(row, start, end, zone)
    upcoming_only = meta['recurring'] and target is None and not explicit_range
    if target is not None:
        items = [item for item in items if events.utc(item['starts_at']) == events.utc(target)]
        if not items:
            raise events.CalendarError('该次活动不存在、已取消或已改期', 404)
    elif upcoming_only:
        now = utc_now()
        items = [item for item in items if (item['starts_at'].date() >= now.astimezone(ZoneInfo(zone)).date()
                  if item['all_day'] else events.utc(item['starts_at']) >= now)]
    data = page_result([view(item, zone, description_offset=arguments.description_offset,
                            description_limit=arguments.description_limit) for item in items], arguments.page, arguments.page_size)
    data.update(event_id=event_id, title=meta['title'], recurring=meta['recurring'],
                query=dict(start=start.isoformat(), end_exclusive=end.isoformat(), upcoming_only=upcoming_only),
                notices=['没有返回发生次数仅表示此查询窗口内没有安排。全天结束日期不包含当天。'])
    return envelope('calendar.event', data, zone)


CALENDAR_FIELD_LABELS = {'title': '事件名称', 'start_date': '日期', 'start_time': '开始时间', 'location': '地点', 'description': '备注'}
MAX_CHAT_CALENDAR_TASKS = 30


def _event_view(event: dict, zone: str) -> dict:
    tz = ZoneInfo(zone)
    starts = events.utc(event['starts_at']).astimezone(tz)
    ends = events.utc(event['ends_at']).astimezone(tz) if event.get('ends_at') else None
    return dict(event_id=event['event_id'], title=event['title'], location=event['location'],
                description=event['description'], starts_at_local=starts.isoformat(),
                ends_at_local=ends.isoformat() if ends else None)


async def _register_calendar_task(context, *, prefill: dict, zone: str, notice: str | None, event_id: str | None) -> None:
    if not (context.session_id and context.request_id):
        return
    from .card_subscription_tools import _owned_session
    row = await _owned_session(context.db, context.user_id, context.session_id, create=True)
    state = json.loads(json.dumps(row.state or {}))
    entries = list(state.get('calendar_tasks', []))
    entries.append({'entry_id': uuid4().hex, 'origin_request_id': context.request_id, 'prefill': prefill,
                    'timezone': zone, 'notice': notice, 'event_id': event_id, 'closed': False})
    state['calendar_tasks'] = entries[-MAX_CHAT_CALENDAR_TASKS:]
    row.state = state
    await context.db.commit()


async def create_calendar_event(arguments, context):
    """信息完整时直接创建本人日程；缺项或时间无法核验时在聊天中打开预填表单，由本人补全。"""
    zone = arguments.timezone or (context.page.calendar.timezone if context.page.calendar else 'Asia/Shanghai')
    message = context.original_message or ''
    values = arguments.model_dump(exclude_none=True, exclude={'timezone', 'time_quote'})
    unverified = []
    # 服务端只核验摘录确实出自用户原话；日期换算与字段含义由模型理解。
    if any(key in values for key in ('start_date', 'start_time', 'end_date', 'end_time')) and not (
            arguments.time_quote and arguments.time_quote.strip() and arguments.time_quote.strip() in message):
        for key in ('start_date', 'start_time', 'end_date', 'end_time'):
            values.pop(key, None)
        unverified.append('时间')
    for key in ('location', 'description'):
        if key in values and values[key].strip() not in message:
            values.pop(key)
            unverified.append(CALENDAR_FIELD_LABELS[key])
    notice = ('以下内容未能从你的原话中确认，请在表单中填写：' + '、'.join(unverified) + '。') if unverified else None
    starts = ends = None
    if 'start_date' in values and 'start_time' in values:
        tz = ZoneInfo(zone)
        starts = datetime.combine(date.fromisoformat(values['start_date']), time.fromisoformat(values['start_time']), tz)
        if 'end_time' in values:
            ends = datetime.combine(date.fromisoformat(values.get('end_date', values['start_date'])),
                                    time.fromisoformat(values['end_time']), tz)
            if ends <= starts:
                values.pop('end_time'); values.pop('end_date', None); ends = None
                notice = (notice or '') + '结束时间须晚于开始时间，请在表单中调整。'
        if starts.astimezone(utc_zone.utc) < utc_now():
            notice = (notice or '') + '开始时间已经过去，请确认日期和时间。'
    missing = [key for key in ('title', 'start_date', 'start_time') if key not in values]
    created = None
    if not missing and notice is None:
        created = await events.create_event(context.user_id, CalendarEventCreate(
            title=values['title'], location=values.get('location', ''), description=values.get('description', ''),
            starts_at=starts, ends_at=ends))
    await _register_calendar_task(context, prefill=values, zone=zone, notice=notice,
                                  event_id=created['event_id'] if created else None)
    if created:
        return envelope('calendar.created', dict(created=True, event=_event_view(created, zone),
            next_step='日程已创建；如有误可在聊天中的日程卡片删除。'), zone)
    return envelope('calendar.form', dict(created=False, form_opened=True, prefill=values,
        missing_fields=[CALENDAR_FIELD_LABELS[key] for key in missing], notice=notice,
        next_step='尚未创建。请在聊天中的日程表单补全' + '、'.join(CALENDAR_FIELD_LABELS[key] for key in missing)
                  + ('并核对' if missing else '核对') + '后点击创建。'), zone)


async def chat_calendar_tasks(db, user_id: str, state: dict) -> list[dict]:
    views = []
    for entry in state.get('calendar_tasks', []):
        if entry.get('closed'):
            continue
        event = None
        if entry.get('event_id'):
            row = await db.scalar(select(CalendarEventORM).where(
                CalendarEventORM.user_id == user_id, CalendarEventORM.event_id == entry['event_id']))
            event = events.manual_view(row) if row else None
        views.append({'entry_id': entry['entry_id'], 'origin_request_id': entry['origin_request_id'],
                      'prefill': entry['prefill'], 'timezone': entry['timezone'], 'notice': entry.get('notice'),
                      'event_id': entry.get('event_id'), 'event': event,
                      'deleted': bool(entry.get('event_id')) and event is None})
    return views


async def update_chat_calendar_task(db, *, user_id: str, session_id: str, entry_id: str,
                                    event_id: str | None = None, close: bool = False) -> list[dict]:
    """把本人在聊天表单中创建的日程挂到任务卡，或关闭未创建的表单。"""
    from .card_subscription_tools import CardTaskError, _owned_session
    row = await _owned_session(db, user_id, session_id)
    state = json.loads(json.dumps(row.state or {})) if row else {}
    entries = list(state.get('calendar_tasks', []))
    entry = next((item for item in entries if item['entry_id'] == entry_id and not item.get('closed')), None)
    if entry is None:
        raise CardTaskError('日程任务不存在', 'CALENDAR_TASK_NOT_FOUND', 404)
    if close:
        entry['closed'] = True
    else:
        owned = await db.scalar(select(CalendarEventORM).where(
            CalendarEventORM.user_id == user_id, CalendarEventORM.event_id == event_id))
        if owned is None:
            raise CardTaskError('日程不存在', 'CALENDAR_EVENT_NOT_FOUND', 404)
        entry.update(event_id=event_id, notice=None)
    state['calendar_tasks'] = entries
    row.state = state
    await db.commit()
    return await chat_calendar_tasks(db, user_id, state)


def calendar_tools():
    entries = [
        ('list_calendar_events', ListArguments, list_calendar,
         '查询本人某天或范围内实际日程（含跨日、全天、重复例外）。start_date/end_date用YYYY-MM-DD，end_date不含当天，最多62天。省略日期沿用日历选中日期，否则今天；timezone默认页面或北京时间。分页20条，最多50。'),
        ('search_calendar_events', SearchArguments, search_calendar,
         '按名称或地点关键词搜索本人所有已保存活动，不限日期。返回事件/系列候选；重复系列显示原始开始时间，不代表下次时间。匹配唯一活动后调用get_calendar_event取得实际发生时间；同名多项先让用户选择。'),
        ('get_calendar_event', DetailArguments, detail_calendar,
         '查询本人活动详情及实际发生次数。event_id省略继承页面选中活动及该次时间；显式ID默认单次原日期/重复系列未来30天（已开始定时事件不作为下次）。occurrence_start可指定带时区的实际开始时间。查询其他日期可给start_date/end_date（不含，最多62天）。长备注可通过description_offset/description_limit分页读取。'),
        ('create_calendar_event', CreateEventArguments, create_calendar_event,
         '为本人创建日程。根据用户本轮原话理解事件名称、日期、开始时间，以及可选的结束时间、地点、备注；日期按当前时间和时区换算为YYYY-MM-DD，时间为24小时制HH:MM，并把原话中的时间说法逐字放入time_quote；地点、备注逐字摘录。未说出的字段不填、不猜。名称、日期和开始时间齐全时直接创建；缺少任一项或时间无法核验时，聊天中会打开预填好的日程表单，由用户补全后创建。'),
    ]
    return {name: dict(schema={'type': 'function', 'function': dict(name=name, description=description,
        parameters=model.model_json_schema())}, arguments=model, execute=function, requires_identity=True)
        for name, model, function, description in entries}


async def offline_reply(message, context, execute):
    """常用日历措辞的有限离线查询；其余交给模型或提示补充。"""
    if context is None or re.search(r'添加|新建|创建|删除|取消|修改|改期|预约|转账', message):
        return None
    selected = context.page.calendar
    zone = selected.timezone if selected else 'Asia/Shanghai'
    today = utc_now().astimezone(ZoneInfo(zone)).date()
    args = {}
    name = None
    if re.search(r'这场|这个活动|选中.*活动', message):
        name = 'get_calendar_event'
    elif re.search(r'什么时候|何时|几时', message):
        keyword = re.split(r'(?:在)?什么时候|何时|几时', message)[0].strip(' “”"？?')
        keyword = re.sub(r'^(?:(?:请)?(?:帮我|告诉我|查看|查询|查一下|看一下|帮忙)\s*)+', '', keyword).strip()
        keyword = re.sub(r'^(?:下次|下一次)', '', keyword).strip()
        if keyword:
            name, args = 'search_calendar_events', {'keyword': keyword}
    elif re.search(r'日历|日程|安排|规划|活动', message):
        first = None
        last = None
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', message) or re.search(r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号]', message)
        try:
            if match:
                first = date(int(match[1] or today.year), int(match[2]), int(match[3]))
            elif '后天' in message:
                first = today + timedelta(days=2)
            elif '明天' in message:
                first = today + timedelta(days=1)
            elif '今天' in message:
                first = today
            elif re.search(r'下周|本周|这周', message):
                first = today - timedelta(days=today.weekday()) + timedelta(days=7 if '下周' in message else 0)
                last = first + timedelta(days=7)
            elif re.search(r'这天|这一天|选中日期|当天', message):
                if selected is None:
                    return dict(reply='请先在日历中选择日期，或说明要查询哪一天。', mode='offline', tool_calls=[])
                first = date.fromisoformat(selected.selected_date)
        except ValueError:
            return dict(reply='请提供有效日期，例如2026-10-15。', mode='offline', tool_calls=[])
        if first:
            name = 'list_calendar_events'
            args = dict(start_date=first.isoformat(), end_date=(last or first + timedelta(days=1)).isoformat(), timezone=zone)
    if name is None:
        return None
    async def run(tool_name, arguments):
        import json
        return await execute({'function': {'name': tool_name, 'arguments': json.dumps(arguments)}}, context)
    trace = []
    result = await run(name, args)
    trace.append(dict(name=name, result=result))
    if result.get('error'):
        return dict(reply=result.get('message', '日历查询暂时无法完成。'), mode='offline', tool_calls=trace)
    data = result['data']
    if name == 'search_calendar_events':
        if data['total'] == 1:
            name = 'get_calendar_event'
            result = await run(name, {'event_id': data['items'][0]['event_id']})
            trace.append(dict(name=name, result=result))
            if result.get('error'):
                return dict(reply=result.get('message', '活动详情未能查询。'), mode='offline', tool_calls=trace)
            data = result['data']
        elif data['total'] == 0:
            return dict(reply=f"本人已保存日历中没有找到名称或地点包含“{args['keyword']}”的活动。可以换个关键词查询。", mode='offline', tool_calls=trace)
        else:
            return dict(reply=f"找到多个匹配活动（共{data['total']}项，当前第{data['page']}页），请在日历中选择一项后问‘这个活动什么时候举行’：\n" + '\n'.join(
                f"- {item['title']} · {item['location'] or '地点未填写'}{' · 重复日程' if item['recurring'] else ''}"
                for item in data['items']), mode='offline', tool_calls=trace)
    query = data['query']
    reply = f"日历查询范围：{query['start']} 至 {query['end_exclusive']}（不含终点），共 {data['total']} 项。\n"
    for item in data['items']:
        when = f"{item['start_date']} 全天，结束日期 {item['end_date_exclusive']}（不含）" if item['all_day'] else item['starts_at_local'] + (f" 至 {item['ends_at_local']}" if item['ends_at_local'] else '')
        reply += f"- {when} · {item['title']} · {item['location'] or '地点未填写'}\n"
    if not data['total']:
        reply += '该查询窗口内没有安排；可以指定其他日期范围继续查询。'
    if data['has_more']:
        reply += '还有更多结果，当前仅显示本页。'
    return dict(reply=reply, mode='offline', tool_calls=trace)
