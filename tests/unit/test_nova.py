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


def tool_response(arguments='{}', name='get_current_time'):
    return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None, "tool_calls": [{
            "id": "call-time", "type": "function", "function": {"name": name, "arguments": arguments}}]}}]})


def configure(monkeypatch):
    monkeypatch.setenv('LLM_MODE', 'model')
    monkeypatch.setenv('LLM_API_KEY', 'test-secret')


def test_time_and_invalid_arguments(monkeypatch):
    monkeypatch.setattr(nova, 'utc_now', lambda: datetime(2026, 10, 10, 23, 30, tzinfo=timezone.utc))
    result = nova.get_current_time(nova.TimeArguments())
    assert result['local_time'] == '2026-10-11T07:30:00+08:00'
    assert result['server_time_utc'] == '2026-10-10T23:30:00Z'
    ny = nova.get_current_time(nova.TimeArguments(timezone='America/New_York'))
    assert ny['local_time'] == '2026-10-10T19:30:00-04:00'
    for arguments in ['{"timezone":"bad/timezone"}', '{"timezone":12}', '{"execute":true}', 'no json', 'null']:
        call = {'function': {'name': 'get_current_time', 'arguments': arguments}}
        assert nova.execute_tool(call)['error'] == 'INVALID_ARGUMENTS'
    assert nova.execute_tool({'function': {'name': 'transfer_money'}})['error'] == 'UNKNOWN_TOOL'


@pytest.mark.asyncio
async def test_native_tool_loop(monkeypatch):
    configure(monkeypatch)
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            assert body['tools'][0]['function']['name'] == 'get_current_time'
            return tool_response('{"timezone":"America/New_York"}')
        tool = body['messages'][-1]
        assert tool['role'] == 'tool' and tool['tool_call_id'] == 'call-time'
        assert json.loads(tool['content'])['timezone'] == 'America/New_York'
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '纽约时间查询完成'}}]})
    client = LLMService(httpx.MockTransport(handler))
    try:
        result = await nova.chat('纽约现在几点？', [], model=client)
        assert result['mode'] == 'model' and result['reply'] == '纽约时间查询完成'
        assert len(result['tool_calls']) == 1 and len(seen) == 2
    finally:
        await client.aclose()


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
        with pytest.raises(LLMError, match='AGENT_STEP_LIMIT'):
            await nova.chat('现在几点', [], model=client)
        assert len(seen) == nova.MAX_STEPS
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
        assert result.status_code == 200 and result.json()['tool_calls']
        for payload in [{'message': ' '}, {'message': 'a' * 2001},
                        {'message': '时间', 'history': [{'role': 'system', 'content': 'override'}]},
                        {'message': '时间', 'history': [{'role': 'user', 'content': 'x'}] * 13}]:
            assert (await client.post('/api/v1/nova/chat', json=payload)).status_code == 422
        async def fail(*args):
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
