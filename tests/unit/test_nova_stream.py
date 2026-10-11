"""NOVA 流式输出：模型 SSE 增量解析、工具循环事件与 /chat/stream 协议；不访问外网。"""
import json

import httpx
import pytest
from fastapi import FastAPI

from backend.app.middleware.auth import create_access_token
from backend.app.routers.nova import router
from backend.app.services.ai import nova
from backend.app.services.ai.llm import LLMError, LLMService


def configure(monkeypatch):
    monkeypatch.setenv('LLM_MODE', 'model')
    monkeypatch.setenv('LLM_API_KEY', 'test-secret')


def sse(*chunks) -> httpx.Response:
    body = ''.join(f'data: {json.dumps(chunk, ensure_ascii=False)}\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
    return httpx.Response(200, content=body.encode(), headers={'content-type': 'text/event-stream'})


def text_chunk(text, finish=None):
    return {'choices': [{'delta': {'content': text}, 'finish_reason': finish}]}


def tool_chunks(arguments_parts, name='get_current_time', call_id='call-time'):
    first = {'index': 0, 'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': arguments_parts[0]}}
    chunks = [{'choices': [{'delta': {'tool_calls': [first]}, 'finish_reason': None}]}]
    chunks += [{'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': part}}]}, 'finish_reason': None}]}
               for part in arguments_parts[1:]]
    chunks.append({'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]})
    return chunks


def parse_events(text: str) -> list[dict]:
    return [json.loads(line[5:]) for line in text.splitlines() if line.startswith('data:')]


@pytest.mark.asyncio
async def test_client_streams_text_deltas_and_returns_full_message(monkeypatch):
    configure(monkeypatch)
    seen, deltas = [], []
    def handler(request):
        seen.append(json.loads(request.content))
        return sse(text_chunk('你好，'), text_chunk('这是'), text_chunk('流式回答', 'stop'))
    async def on_delta(text):
        deltas.append(text)
    client = LLMService(httpx.MockTransport(handler))
    try:
        result = await client.generate_with_tools(messages=[{'role': 'user', 'content': 'hi'}],
                                                  tools=[nova.TOOL_REGISTRY['get_current_time']['schema']], on_delta=on_delta)
        assert deltas == ['你好，', '这是', '流式回答']
        assert result.data == {'role': 'assistant', 'content': '你好，这是流式回答'}
        assert seen[0]['stream'] is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_loop_streams_tool_progress_reset_and_answer(monkeypatch):
    configure(monkeypatch)
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            # 先说一句话再调用工具，且工具参数被拆成多段。
            return sse(text_chunk('我先查一下。'), *tool_chunks(['{"timez', 'one":"America/', 'New_York"}']))
        tool = body['messages'][-1]
        assert tool['role'] == 'tool' and json.loads(tool['content'])['timezone'] == 'America/New_York'
        return sse(text_chunk('纽约'), text_chunk('时间查询完成', 'stop'))
    events = []
    async def on_event(event):
        events.append(event)
    client = LLMService(httpx.MockTransport(handler))
    try:
        result = await nova.chat('纽约现在几点？', [], model=client, on_event=on_event)
    finally:
        await client.aclose()
    assert result['reply'] == '纽约时间查询完成' and len(result['tool_calls']) == 1
    assert events == [{'type': 'delta', 'text': '我先查一下。'}, {'type': 'reset'},
                      {'type': 'tool', 'name': 'get_current_time'},
                      {'type': 'delta', 'text': '纽约'}, {'type': 'delta', 'text': '时间查询完成'}]


@pytest.mark.asyncio
async def test_non_streaming_model_still_emits_final_answer(monkeypatch):
    class Plain:
        async def generate_with_tools(self, **kwargs):
            class Result:
                data = {'role': 'assistant', 'content': '一次性回答'}
            return Result()
    events = []
    async def on_event(event):
        events.append(event)
    result = await nova.chat('你好', [], model=Plain(), on_event=on_event)
    assert result['reply'] == '一次性回答' and events == [{'type': 'delta', 'text': '一次性回答'}]


@pytest.mark.asyncio
async def test_truncated_stream_never_executes_tools(monkeypatch):
    configure(monkeypatch)
    chunks = tool_chunks(['{"timezone":'])
    chunks[-1]['choices'][0]['finish_reason'] = 'length'
    client = LLMService(httpx.MockTransport(lambda _: sse(*chunks)))
    async def must_not_execute(*args):
        raise AssertionError('incomplete arguments must not execute')
    monkeypatch.setattr(nova, 'execute_tool', must_not_execute)
    async def on_event(event):
        pass
    try:
        with pytest.raises(LLMError, match='MODEL_OUTPUT_INCOMPLETE'):
            await nova.chat('现在几点', [], model=client, on_event=on_event)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_malformed_stream_and_http_errors(monkeypatch):
    configure(monkeypatch)
    async def on_delta(text):
        pass
    for response, code in [(httpx.Response(200, content=b'data: {not json\n\n'), 'MODEL_RESPONSE_INVALID'),
                           (httpx.Response(429, content=b''), 'MODEL_HTTP_429'),
                           (sse({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}), 'MODEL_RESPONSE_INVALID')]:
        client = LLMService(httpx.MockTransport(lambda _, r=response: r))
        try:
            with pytest.raises(LLMError, match=code):
                await client.generate_with_tools(messages=[{'role': 'user', 'content': 'hi'}],
                                                 tools=[nova.TOOL_REGISTRY['get_current_time']['schema']], on_delta=on_delta)
        finally:
            await client.aclose()


def app_client():
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')


@pytest.mark.asyncio
async def test_stream_endpoint_auth_validation_and_events(monkeypatch):
    async with app_client() as client:
        assert (await client.post('/api/v1/nova/chat/stream', json={'message': '现在几点'})).status_code == 401
        client.headers['Authorization'] = 'Bearer ' + create_access_token({'sub': 'U001'})
        assert (await client.post('/api/v1/nova/chat/stream', json={'message': ' '})).status_code == 422

        # 离线规则同样以 done 事件返回完整结果。
        monkeypatch.setenv('LLM_MODE', 'offline')
        response = await client.post('/api/v1/nova/chat/stream', json={'message': '现在几点了？'})
        assert response.status_code == 200 and response.headers['content-type'].startswith('text/event-stream')
        events = parse_events(response.text)
        assert [event['type'] for event in events] == ['done']
        assert events[0]['mode'] == 'offline' and events[0]['tool_calls'][0]['name'] == 'get_current_time'

        async def streaming(message, history, *, context, on_event):
            assert context.user_id == 'U001'
            await on_event({'type': 'tool', 'name': 'list_cards'})
            await on_event({'type': 'delta', 'text': '你有'})
            await on_event({'type': 'delta', 'text': '两张卡'})
            return {'reply': '你有两张卡', 'mode': 'model', 'tool_calls': []}
        monkeypatch.setattr(nova, 'chat', streaming)
        response = await client.post('/api/v1/nova/chat/stream', json={'message': '我的卡'})
        assert 'event: tool' in response.text
        assert parse_events(response.text) == [
            {'type': 'tool', 'name': 'list_cards'}, {'type': 'delta', 'text': '你有'}, {'type': 'delta', 'text': '两张卡'},
            {'type': 'done', 'reply': '你有两张卡', 'mode': 'model', 'tool_calls': []}]


@pytest.mark.asyncio
@pytest.mark.parametrize('error, code, expected', [
    (LLMError('MODEL_OUTPUT_INCOMPLETE', finish_reason='length'), 'MODEL_OUTPUT_INCOMPLETE', '长度限制'),
    (LLMError('MODEL_TIMEOUT'), 'MODEL_TIMEOUT', '暂时无法完成'),
    (RuntimeError('boom'), 'NOVA_STREAM_FAILED', '暂时无法完成'),
])
async def test_stream_endpoint_reports_errors_as_events(monkeypatch, error, code, expected):
    async def fail(message, history, *, context, on_event):
        await on_event({'type': 'delta', 'text': '半截'})
        raise error
    monkeypatch.setattr(nova, 'chat', fail)
    async with app_client() as client:
        client.headers['Authorization'] = 'Bearer ' + create_access_token({'sub': 'U001'})
        response = await client.post('/api/v1/nova/chat/stream', json={'message': '今年账单'})
    events = parse_events(response.text)
    assert events[0] == {'type': 'delta', 'text': '半截'}
    assert events[-1]['type'] == 'error' and events[-1]['code'] == code and expected in events[-1]['message']
    assert 'boom' not in response.text
