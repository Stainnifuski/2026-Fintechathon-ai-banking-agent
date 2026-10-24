"""日历工具闭环：日期、搜索、重复例外、页面选择和身份隔离。"""
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.unit.test_calendar import store, ics, vevent
from backend.app.models import CalendarEventORM
from backend.app.schemas.calendar import CalendarEventCreate
from backend.app.schemas.nova import PageContext, CalendarContext, CalendarSelection
from backend.app.services.calendar import events
from backend.app.services.ai import nova, calendar_tools
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.services.ai.llm import LLMService
from backend.app.routers.nova import router
from backend.app.database.connection import get_db
from backend.app.middleware.auth import create_access_token


def call(name, args=None):
    return {'function': {'name': name, 'arguments': json.dumps(args or {})}}


def ctx(db, selection=None):
    return ToolContext('U1', db, PageContext(page='thirdParty', calendar=CalendarContext(
        visible_month='2026-10', selected_date='2026-10-15', selected_event=selection)))


async def seed():
    point = await events.create_event('U1', CalendarEventCreate(title='项目答辩', location='一号会议室', description='x' * 2500,
        starts_at='2026-10-15T09:00:00+08:00'))
    await events.create_event('U1', CalendarEventCreate(title='跨日旅行', starts_at='2026-10-14T23:00:00+08:00', ends_at='2026-10-15T02:00:00+08:00'))
    await events.create_event('U1', CalendarEventCreate(title='已结束', starts_at='2026-10-14T22:00:00+08:00', ends_at='2026-10-15T00:00:00+08:00'))
    await events.create_event('U1', CalendarEventCreate(title='明日边界', starts_at='2026-10-16T00:00:00+08:00'))
    foreign = await events.create_event('U2', CalendarEventCreate(title='项目答辩', starts_at='2026-10-15T09:00:00+08:00'))
    await events.import_events('U1', ics(
        vevent('all-day', 'DTSTART;VALUE=DATE:20261015', 'DTEND;VALUE=DATE:20261017', '全天活动'),
        vevent('weekly', 'DTSTART:20261001T090000Z', 'DTEND:20261001T100000Z\r\nRRULE:FREQ=WEEKLY;COUNT=10\r\nEXDATE:20261008T090000Z', '例会'),
        vevent('weekly', 'DTSTART:20261015T110000Z', 'DTEND:20261015T120000Z\r\nRECURRENCE-ID:20261015T090000Z\r\nLOCATION:二号会议室', '改期例会'),
    ), 'Asia/Shanghai')
    return point, foreign


@pytest.mark.asyncio
async def test_date_range_matches_calendar_and_pagination(store):
    await seed()
    async with store() as db:
        before = await db.scalar(select(func.count()).select_from(CalendarEventORM))
        result = await nova.execute_tool(call('list_calendar_events', {'page_size': 2}), ctx(db))
        data = result['data']
        assert data['total'] == 4 and len(data['items']) == 2 and data['has_more']
        full = await nova.execute_tool(call('list_calendar_events', {'page_size': 50}), ctx(db))
        assert {item['title'] for item in full['data']['items']} == {'跨日旅行', '项目答辩', '全天活动', '改期例会'}
        page = await events.list_events('U1', datetime.fromisoformat(data['query']['start']),
                                       datetime.fromisoformat(data['query']['end_exclusive']), 'Asia/Shanghai')
        assert [(item['event_id'], item['starts_at']) for item in full['data']['items']] == [
            (item['event_id'], item['starts_at'].isoformat()) for item in page]
        item = next(item for item in full['data']['items'] if item['title'] == '全天活动')
        assert item['start_date'] == '2026-10-15' and item['end_date_exclusive'] == '2026-10-17'
        assert 'starts_at_local' not in item
        assert result['data_kind'] == 'user_calendar' and 'currency' not in result
        assert await db.scalar(select(func.count()).select_from(CalendarEventORM)) == before


@pytest.mark.asyncio
async def test_search_details_selection_recurring_exceptions_and_long_notes(store, monkeypatch):
    point, _ = await seed()
    monkeypatch.setattr(calendar_tools, 'utc_now', lambda: datetime(2026, 10, 10, tzinfo=timezone.utc))
    async with store() as db:
        context = ctx(db)
        # 搜索整个保存集合，页面月份不是搜索边界；ICS例外标题和地点也参与搜索。
        search = await nova.execute_tool(call('search_calendar_events', {'keyword': '改期'}), context)
        item = search['data']['items'][0]
        assert search['data']['total'] == 1 and item['time_meaning'] == 'series_original_start'
        series_id = item['event_id']
        detail = await nova.execute_tool(call('get_calendar_event', {'event_id': series_id}), context)
        assert detail['data']['items'][0]['title'] == '改期例会'
        assert detail['data']['items'][0]['starts_at'] == '2026-10-15T11:00:00+00:00'
        assert detail['data']['query']['upcoming_only']
        selected = CalendarSelection(event_id=series_id, starts_at='2026-10-15T11:00:00Z')
        detail = await nova.execute_tool(call('get_calendar_event'), ctx(db, selected))
        assert detail['data']['total'] == 1 and detail['data']['items'][0]['location'] == '二号会议室'
        for target in ('2026-10-08T09:00:00Z', '2026-10-15T09:00:00Z'):
            result = await nova.execute_tool(call('get_calendar_event', {'event_id': series_id, 'occurrence_start': target}), context)
            assert result['error'] == 'CALENDAR_EVENT_NOT_FOUND'
        search = await nova.execute_tool(call('search_calendar_events', {'keyword': '一号会议室'}), context)
        assert search['data']['total'] == 1 and search['data']['items'][0]['description_has_more']
        detail = await nova.execute_tool(call('get_calendar_event', {'event_id': point['event_id']}), context)
        assert len(detail['data']['items'][0]['description']) == 2000
        more = await nova.execute_tool(call('get_calendar_event', {'event_id': point['event_id'], 'description_offset': 2000}), context)
        assert len(more['data']['items'][0]['description']) == 500 and not more['data']['items'][0]['description_has_more']


@pytest.mark.asyncio
async def test_all_day_timezone_dst_and_invalid_or_foreign_queries(store):
    _, foreign = await seed()
    async with store() as db:
        context = ctx(db)
        result = await nova.execute_tool(call('list_calendar_events', {'timezone': 'America/New_York'}), context)
        all_day = next(item for item in result['data']['items'] if item['all_day'])
        assert all_day['start_date'] == '2026-10-15'
        dst = await nova.execute_tool(call('list_calendar_events', {'start_date': '2026-11-01', 'end_date': '2026-11-02', 'timezone': 'America/New_York'}), context)
        assert dst['data']['query']['start'].endswith('-04:00') and dst['data']['query']['end_exclusive'].endswith('-05:00')
        for event_id in (foreign['event_id'], 'missing'):
            assert (await nova.execute_tool(call('get_calendar_event', {'event_id': event_id}), context))['error'] == 'CALENDAR_EVENT_NOT_FOUND'
        assert (await nova.execute_tool(call('get_calendar_event'), context))['error'] == 'MISSING_SELECTION'
        for name in ('list_calendar_events', 'search_calendar_events', 'get_calendar_event'):
            assert (await nova.execute_tool(call(name, {'user_id': 'U2'}), context))['error'] == 'INVALID_ARGUMENTS'
        for args in ({'timezone': '../UTC'}, {'start_date': '2026-02-30'}, {'page_size': 51}, {'page': '1'}):
            assert (await nova.execute_tool(call('list_calendar_events', args), context))['error'] == 'INVALID_ARGUMENTS'
        assert (await nova.execute_tool(call('list_calendar_events', {'start_date': '2026-01-01', 'end_date': '2026-04-01'}), context))['error'] == 'CALENDAR_QUERY_INVALID'
        assert (await nova.execute_tool(call('search_calendar_events', {'keyword': ' '}), context))['error'] == 'INVALID_ARGUMENTS'


@pytest.mark.asyncio
async def test_offline_dates_names_and_multiple_matches(store, monkeypatch):
    await seed()
    monkeypatch.setattr(calendar_tools, 'utc_now', lambda: datetime(2026, 10, 14, 1, tzinfo=timezone.utc))
    async with store() as db:
        for question in ('明天有什么规划？', '2026年10月15日有什么安排？', '这一天有什么规划？'):
            reply = await nova.chat(question, [], context=ctx(db))
            assert reply['mode'] == 'offline' and reply['tool_calls'][0]['name'] == 'list_calendar_events'
            assert '项目答辩' in reply['reply']
        reply = await nova.chat('项目答辩什么时候举行？', [], context=ctx(db))
        assert [c['name'] for c in reply['tool_calls']] == ['search_calendar_events', 'get_calendar_event']
        assert '2026-10-15T09:00:00+08:00' in reply['reply']
        await events.create_event('U1', CalendarEventCreate(title='项目答辩第二场', starts_at='2026-10-20T09:00:00+08:00'))
        reply = await nova.chat('项目答辩什么时候举行？', [], context=ctx(db))
        assert '多个匹配' in reply['reply'] and len(reply['tool_calls']) == 1
        assert not (await nova.chat('删除明天的日程', [], context=ctx(db)))['tool_calls']


@pytest.mark.asyncio
async def test_authenticated_calendar_tool_loop_and_page_validation(store, monkeypatch):
    point, _ = await seed()
    seen = []
    def handler(request):
        body = json.loads(request.content)
        if body.get('response_format'):
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
                'content': json.dumps({'mode': 'direct_react', 'domains': ['calendar'],
                    'has_dependencies': False, 'goal_plan': None})}}]})
        seen.append(body)
        if len(seen) == 1:
            assert any(point['event_id'] in message.get('content', '') for message in body['messages'])
            assert 'user_id' not in json.dumps(body)
            return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
                'content': None, 'tool_calls': [{'id': 'calendar', 'type': 'function', 'function': {
                    'name': 'get_calendar_event', 'arguments': '{}'}}]}}]})
        tool = body['messages'][-1]
        assert tool['tool_call_id'] == 'calendar' and json.loads(tool['content'])['data']['total'] == 1
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '项目答辩10月15日上午9点举行。'}}]})
    monkeypatch.setenv('LLM_MODE', 'model')
    monkeypatch.setenv('LLM_API_KEY', 'test-secret')
    model = LLMService(httpx.MockTransport(handler))
    monkeypatch.setattr(nova, 'llm', model)
    app = FastAPI()
    app.include_router(router)
    async def get_session():
        async with store() as db:
            yield db
    app.dependency_overrides[get_db] = get_session
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.post('/api/v1/nova/chat', json={'message': '日程'})).status_code == 401
            client.headers['Authorization'] = 'Bearer ' + create_access_token({'sub': 'U1'})
            calendar = {'visible_month': '2026-10', 'selected_date': '2026-10-15', 'selected_event': {
                'event_id': point['event_id'], 'starts_at': '2026-10-15T01:00:00Z'}}
            reply = await client.post('/api/v1/nova/chat', json={'message': '这个活动什么时候举行？', 'page_context': {'page': 'thirdParty', 'calendar': calendar}})
            assert reply.status_code == 200 and '项目答辩' in reply.json()['reply']
            assert '2026-10-15T09:00:00+08:00' in reply.json()['reply']
            assert reply.json()['grounding']['status'] == 'grounded'
            for patch in ({'timezone': 'bad-zone'}, {'selected_date': '2026-02-30'}, {'user_id': 'U2'},
                          {'selected_event': {'event_id': point['event_id'], 'starts_at': '2026-10-15T09:00:00'}}):
                assert (await client.post('/api/v1/nova/chat', json={'message': '日程', 'page_context': {
                    'page': 'thirdParty', 'calendar': {**calendar, **patch}}})).status_code == 422
            assert (await client.post('/api/v1/nova/chat', json={'message': '日程', 'page_context': {'page': 'home', 'calendar': calendar}})).status_code == 422
    finally:
        await model.aclose()


@pytest.mark.asyncio
async def test_detail_uses_local_day_and_default_future_scope_does_not_hide_saved_records(store, monkeypatch):
    late = await events.create_event('U1', CalendarEventCreate(title='凌晨活动', starts_at='2026-10-15T23:00:00Z'))
    past = await events.create_event('U1', CalendarEventCreate(title='历史答辩', starts_at='2025-01-01T09:00:00Z'))
    await events.import_events('U1', ics(vevent('yearly', 'DTSTART:20270101T090000Z',
        'RRULE:FREQ=YEARLY;COUNT=3', '年度活动')), 'Asia/Shanghai')
    monkeypatch.setattr(calendar_tools, 'utc_now', lambda: datetime(2026, 10, 10, tzinfo=timezone.utc))
    async with store() as db:
        result = await nova.execute_tool(call('get_calendar_event', {'event_id': late['event_id']}), ctx(db))
        assert result['data']['total'] == 1
        assert result['data']['items'][0]['starts_at_local'] == '2026-10-16T07:00:00+08:00'
        search = await nova.execute_tool(call('search_calendar_events', {'keyword': '历史答辩'}), ctx(db))
        assert search['data']['total'] == 1 and search['data']['items'][0]['event_id'] == past['event_id']
        search = await nova.execute_tool(call('search_calendar_events', {'keyword': '年度活动'}), ctx(db))
        event_id = search['data']['items'][0]['event_id']
        result = await nova.execute_tool(call('get_calendar_event', {'event_id': event_id}), ctx(db))
        assert result['data']['total'] == 0 and result['data']['query']['upcoming_only']
        result = await nova.execute_tool(call('get_calendar_event', {'event_id': event_id,
            'start_date': '2027-01-01', 'end_date': '2027-01-02'}), ctx(db))
        assert result['data']['total'] == 1


@pytest.mark.asyncio
async def test_create_event_directly_when_complete_otherwise_prefilled_chat_form(store, monkeypatch):
    from dataclasses import replace
    from backend.app.middleware.auth import get_current_user
    from backend.app.services.ai.transfer_manager import get_session
    from backend.app.services.bank import nova_conversations
    monkeypatch.setattr(calendar_tools, 'utc_now', lambda: datetime(2026, 10, 14, 1, tzinfo=timezone.utc))
    async with store() as db:
        session_id = (await nova_conversations.create(db, 'U1'))['session_id']

        def at(message, request_id):
            return replace(ctx(db), original_message=message, session_id=session_id, request_id=request_id)

        complete = await nova.execute_tool(call('create_calendar_event', {
            'title': '和李四吃饭', 'start_date': '2026-10-15', 'start_time': '18:30', 'end_time': '20:00',
            'location': '海底捞', 'time_quote': '明天晚上六点半到八点'}), at('明天晚上六点半到八点在海底捞和李四吃饭', 'R1'))
        event = complete['data']['event']
        assert complete['data']['created'] and event['starts_at_local'] == '2026-10-15T18:30:00+08:00'
        assert event['ends_at_local'] == '2026-10-15T20:00:00+08:00' and event['location'] == '海底捞'
        assert await db.scalar(select(func.count()).select_from(CalendarEventORM)) == 1

        missing = await nova.execute_tool(call('create_calendar_event', {
            'title': '看牙医', 'start_date': '2026-10-16', 'time_quote': '后天'}), at('后天去看牙医', 'R2'))
        assert not missing['data']['created'] and missing['data']['missing_fields'] == ['开始时间']
        assert missing['data']['prefill'] == {'title': '看牙医', 'start_date': '2026-10-16'}

        # A time the user never said is dropped, not created.
        invented = await nova.execute_tool(call('create_calendar_event', {
            'title': '开会', 'start_date': '2026-10-15', 'start_time': '09:00', 'location': '三楼',
            'time_quote': '明早九点'}), at('帮我记一下开会', 'R3'))
        assert not invented['data']['created'] and '时间' in invented['data']['notice']
        assert invented['data']['prefill'] == {'title': '开会'}
        past = await nova.execute_tool(call('create_calendar_event', {
            'title': '跑步', 'start_date': '2026-10-13', 'start_time': '07:00', 'time_quote': '昨天早上七点'}),
            at('昨天早上七点跑步', 'R4'))
        assert not past['data']['created'] and '已经过去' in past['data']['notice']
        assert await db.scalar(select(func.count()).select_from(CalendarEventORM)) == 1

        tasks = (await get_session(db, 'U1', session_id))['calendar_tasks']
        assert [bool(task['event']) for task in tasks] == [True, False, False, False]
        assert tasks[0]['event']['title'] == '和李四吃饭'

        app = FastAPI()
        app.include_router(router)
        user = {'user_id': 'U1'}
        app.dependency_overrides[get_current_user] = lambda: user
        async def use_db():
            yield db
        app.dependency_overrides[get_db] = use_db
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            # The user fills the chat form; it creates through the calendar API, then links the card.
            created = await events.create_event('U1', CalendarEventCreate(title='看牙医', starts_at='2026-10-16T10:00:00+08:00'))
            base = f"/api/v1/nova/task-sessions/{session_id}/calendar-tasks/{tasks[1]['entry_id']}"
            foreign = await events.create_event('U2', CalendarEventCreate(title='别人', starts_at='2026-10-16T10:00:00+08:00'))
            assert (await client.post(base + '/event', json={'event_id': foreign['event_id']})).status_code == 404
            linked = (await client.post(base + '/event', json={'event_id': created['event_id']})).json()['calendar_tasks']
            assert linked[1]['event']['title'] == '看牙医'
            remaining = (await client.post(f"/api/v1/nova/task-sessions/{session_id}/calendar-tasks/{tasks[3]['entry_id']}/dismiss")).json()
            assert len(remaining['calendar_tasks']) == 3
            await events.delete_event('U1', tasks[0]['event_id'])
            assert (await get_session(db, 'U1', session_id))['calendar_tasks'][0]['deleted'] is True
    assert '日历业务 Skill' in nova.SYSTEM_PROMPT
