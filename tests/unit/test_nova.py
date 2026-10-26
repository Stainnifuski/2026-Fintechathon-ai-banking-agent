"""工具闭环协议、时区和边界；不访问外网。"""
import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI

from backend.app.services.ai import nova
from backend.app.services.ai.llm import LLMError, LLMService
from backend.app.routers.nova import router
from backend.app.middleware.auth import create_access_token


@pytest.mark.asyncio
@pytest.mark.parametrize('page_data', [
    {'page': 'insights', 'observed_as_of': '2026-10-20T10:00:00+08:00'},
    {'page': 'thirdParty', 'calendar': {'visible_month': '2026-10', 'selected_date': '2026-10-20',
        'selected_event': {'event_id': 'test-event', 'starts_at': '2026-10-20T10:00:00+08:00'}}},
])
async def test_router_serializes_page_timestamps_before_model_call(page_data):
    from backend.app.schemas.nova import PageContext
    class Client:
        async def generate_structured(self, **kwargs):
            payload = json.loads(kwargs['messages'][1]['content'])
            assert payload['page_context'] == PageContext.model_validate(page_data).model_dump(mode='json', exclude_none=True)
            class Result:
                data = nova.RequestRoute(mode='direct_react', domains=['time'], has_dependencies=False)
            return Result()
    result = await nova.route_request(Client(), '现在几点？', [], PageContext.model_validate(page_data), None)
    assert result.domains == ['time']


def tool_response(arguments='{}', name='get_current_time'):
    return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None, "tool_calls": [{
            "id": "call-time", "type": "function", "function": {"name": name, "arguments": arguments}}]}}]})


def configure(monkeypatch):
    monkeypatch.setenv('LLM_MODE', 'model')
    monkeypatch.setenv('LLM_API_KEY', 'test-secret')


@pytest.mark.asyncio
async def test_time_and_invalid_arguments(monkeypatch):
    monkeypatch.setattr(nova, 'utc_now', lambda: datetime(2026, 10, 10, 23, 30, tzinfo=timezone.utc))
    result = nova.get_current_time(nova.TimeArguments())
    assert result['local_time'] == '2026-10-11T07:30:00+08:00'
    assert result['server_time_utc'] == '2026-10-10T23:30:00Z'
    ny = nova.get_current_time(nova.TimeArguments(timezone='America/New_York'))
    assert ny['local_time'] == '2026-10-10T19:30:00-04:00'
    for arguments in ['{"timezone":"bad/timezone"}', '{"timezone":12}', '{"execute":true}', 'no json', 'null']:
        call = {'function': {'name': 'get_current_time', 'arguments': arguments}}
        assert (await nova.execute_tool(call))['error'] == 'INVALID_ARGUMENTS'
    assert (await nova.execute_tool({'function': {'name': 'transfer_money'}}))['error'] == 'UNKNOWN_TOOL'


@pytest.mark.asyncio
async def test_native_tool_loop(monkeypatch):
    configure(monkeypatch)
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            assert {'declare_goal_plan', 'get_current_time'} <= {
                tool['function']['name'] for tool in body['tools']
            }
            return tool_response('{"timezone":"America/New_York"}')
        tool = body['messages'][-1]
        assert tool['role'] == 'tool' and tool['tool_call_id'] == 'call-time'
        assert json.loads(tool['content'])['timezone'] == 'America/New_York'
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '纽约时间查询完成'}}]})
    client = LLMService(httpx.MockTransport(handler))
    try:
        result = await nova.chat('纽约现在几点？', [], model=client)
        assert result['mode'] == 'model' and result['reply'] == '纽约时间查询完成'
        assert 'grounding' not in result
        assert len(result['tool_calls']) == 1 and len(seen) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_multi_goal_declares_structured_plan_before_react(monkeypatch):
    class PlanningModel:
        def __init__(self):
            self.turn = 0

        async def generate_with_tools(self, **kwargs):
            self.turn += 1
            class Result:
                pass
            result = Result()
            if self.turn == 1:
                result.data = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'plan', 'type': 'function', 'function': {
                        'name': 'declare_goal_plan',
                        'arguments': json.dumps({
                            'execution_mode': 'sequential',
                            'goals': [
                                {'goal_id': 'query', 'objective': '查询余额',
                                 'depends_on': [], 'completion_condition': '取得本人可用余额'},
                                {'goal_id': 'transfer', 'objective': '按查询结果发起转账草稿',
                                 'depends_on': ['query'], 'completion_condition': '生成待确认任务'},
                            ],
                        }, ensure_ascii=False),
                    },
                }]}
            else:
                assert all(tool['function']['name'] != 'declare_goal_plan'
                           for tool in kwargs['tools'])
                result.data = {'role': 'assistant', 'content': '计划已登记，后续按依赖执行。'}
            return result

    result = await nova.chat('查余额，如果够就转账', [], model=PlanningModel())
    assert result['goal_plan']['goals'][1]['depends_on'] == ['query']
    assert result['tool_calls'][0]['name'] == 'declare_goal_plan'


@pytest.mark.asyncio
async def test_structured_route_forces_single_transfer_tool(monkeypatch):
    class RoutedModel:
        async def generate_structured(self, **kwargs):
            class Result:
                data = nova.RequestRoute(
                    mode='direct_react', domains=['transfer'],
                    has_dependencies=False,
                )
            return Result()

        async def generate_with_tools(self, **kwargs):
            assert kwargs['tool_choice'] == 'required'
            assert [tool['function']['name'] for tool in kwargs['tools']] == [
                'manage_transfer_task'
            ]
            class Result:
                data = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'transfer', 'type': 'function',
                    'function': {'name': 'manage_transfer_task',
                                     'arguments': '{"action":"create","recipient":"李四","amount_fen":10000}'},
                }]}
            return Result()

    async def execute(call, context):
        assert json.loads(call['function']['arguments']) == {
            'action': 'create', 'recipient': '李四', 'amount_fen': 10000}
        return {
            'reply': '已生成待确认计划', 'session_id': 'S1',
            'session_version': 1, 'focus_task_id': 'T1', 'tasks': [],
        }

    monkeypatch.setattr(nova, 'get_mode', lambda: 'model')
    monkeypatch.setattr(nova, 'llm', RoutedModel())
    monkeypatch.setattr(nova, 'execute_tool', execute)
    result = await nova.chat('给李四转100元', [])
    assert '已生成待确认计划' in result['reply']
    assert result['tool_calls'][0]['name'] == 'manage_transfer_task'


@pytest.mark.asyncio
async def test_loop_limit_and_failure_feedback(monkeypatch):
    configure(monkeypatch)
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) > 1:
            assert json.loads(body['messages'][-1]['content'])['error'] == 'UNKNOWN_TOOL'
        return tool_response(name='transfer_money')
    client = LLMService(httpx.MockTransport(handler))
    try:
        with pytest.raises(LLMError, match='AGENT_NO_PROGRESS'):
            await nova.chat('现在几点', [], model=client)
        assert len(seen) == 3
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_total_timeout(monkeypatch):
    class Slow:
        async def generate_with_tools(self, **kwargs):
            await asyncio.sleep(1)
    monkeypatch.setattr(nova, 'TOTAL_TIMEOUT', 0.01)
    with pytest.raises(LLMError, match='AGENT_TIMEOUT'):
        await nova.chat('时间', [], model=Slow())


@pytest.mark.asyncio
async def test_offline_queries_and_unsupported_location():
    result = await nova.chat('现在几点了？', [])
    assert result['mode'] == 'offline' and len(result['tool_calls']) == 1
    result = await nova.chat('纽约现在几点？', [])
    assert not result['tool_calls'] and '启用模型' in result['reply']


@pytest.mark.asyncio
async def test_api_auth_validation_and_model_error(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post('/api/v1/nova/chat', json={'message': '现在几点'})).status_code == 401
        client.headers['Authorization'] = 'Bearer ' + create_access_token({'sub': 'U001'})
        result = await client.post('/api/v1/nova/chat', json={'message': '现在几点了？'})
        assert result.status_code == 200
        assert result.json()['tool_calls'] == []
        assert '离线模式' in result.json()['reply']
        for payload in [{'message': ' '}, {'message': 'a' * 2001},
                        {'message': '时间', 'history': [{'role': 'system', 'content': 'override'}]},
                        {'message': '时间', 'history': [{'role': 'user', 'content': 'x'}] * 13}]:
            assert (await client.post('/api/v1/nova/chat', json=payload)).status_code == 422
        async def fail(*args, **kwargs):
            raise LLMError('MODEL_TIMEOUT')
        monkeypatch.setattr(nova, 'chat', fail)
        result = await client.post('/api/v1/nova/chat', json={'message': '时间'})
        assert result.status_code == 503 and result.json()['detail']['code'] == 'MODEL_TIMEOUT'


@pytest.mark.asyncio
@pytest.mark.parametrize('message', [
    {'content': None, 'tool_calls': [{'id': 'a', 'type': 'function', 'function': {'name': 'get_current_time', 'arguments': {}}}]},
    {'content': None, 'tool_calls': [{'type': 'function', 'function': {'name': 'get_current_time', 'arguments': '{}'}}]},
    {'content': 'invalid', 'tool_calls': []},
])
async def test_invalid_native_response(monkeypatch, message):
    configure(monkeypatch)
    client = LLMService(httpx.MockTransport(lambda _: httpx.Response(200, json={
        'choices': [{'finish_reason': 'tool_calls', 'message': message}]})))
    try:
        with pytest.raises(LLMError, match='MODEL_RESPONSE_INVALID'):
            await nova.chat('现在几点', [], model=client)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_nova_report_uses_larger_budget_but_respects_global_cap(monkeypatch):
    configure(monkeypatch)
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '年度报告完整生成'}}]})
    client = LLMService(httpx.MockTransport(handler))
    try:
        monkeypatch.setenv('LLM_MAX_TOKENS', '4096')
        await nova.chat('请帮我查看今年的账单情况', [], model=client)
        assert seen[-1]['max_tokens'] == 4096
        monkeypatch.setenv('LLM_MAX_TOKENS', '1200')
        await nova.chat('请帮我查看今年的账单情况', [], model=client)
        assert seen[-1]['max_tokens'] == 1200
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_incomplete_tool_arguments_never_execute(monkeypatch, caplog):
    configure(monkeypatch)
    payload = {'choices': [{'finish_reason': 'length', 'message': {'content': None, 'tool_calls': [{
        'id': 'bad', 'type': 'function', 'function': {'name': 'get_current_time', 'arguments': '{"timezone":'}}]}}],
        'usage': {'completion_tokens': 4096}, 'secret': 'private-response'}
    client = LLMService(httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    async def must_not_execute(*args):
        raise AssertionError('incomplete arguments must not execute')
    monkeypatch.setattr(nova, 'execute_tool', must_not_execute)
    try:
        with pytest.raises(LLMError, match='MODEL_OUTPUT_INCOMPLETE') as error:
            await nova.chat('今年账单', [], model=client)
        assert error.value.finish_reason == 'length'
        assert 'reason=length max_tokens=4096 completion_tokens=4096' in caplog.text
        assert 'private-response' not in caplog.text and 'test-secret' not in caplog.text
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('reason,expected', [('length', '长度限制'), ('insufficient_system_resource', '被中断')])
async def test_incomplete_reply_has_specific_api_message(monkeypatch, reason, expected):
    app = FastAPI()
    app.include_router(router)
    async def fail(*args, **kwargs):
        raise LLMError('MODEL_OUTPUT_INCOMPLETE', finish_reason=reason)
    monkeypatch.setattr(nova, 'chat', fail)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        client.headers['Authorization'] = 'Bearer ' + create_access_token({'sub': 'U001'})
        result = await client.post('/api/v1/nova/chat', json={'message': '今年账单'})
        assert result.status_code == 503 and result.json()['detail']['code'] == 'MODEL_OUTPUT_INCOMPLETE'
        assert expected in result.json()['detail']['message']
