import json

import httpx
import pytest
from fastapi import FastAPI

from backend.app.middleware.auth import get_current_user
from backend.app.routers.nova import router
from backend.app.services.ai import debug_trace


@pytest.mark.asyncio
async def test_debug_trace_is_opt_in_encrypted_redacted_and_owner_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_trace, '_root', tmp_path)
    request_id = 'debugrequest123'
    with debug_trace.capture('alice', request_id, False):
        debug_trace.record('user_request', {'message': 'off'})
    assert not list(tmp_path.rglob('*'))

    with debug_trace.capture('alice', request_id, True):
        debug_trace.record('model_request', {'messages': ['给13812345678转账', 'Bearer abc.def'],
                                             'api_key': 'private-key', 'arguments': '{"otp":"123456"}'})
        debug_trace.record('model_response', {'content': '已处理'})
    raw = debug_trace._path('alice', request_id).read_text()
    assert '已处理' not in raw
    assert 'private-key' not in raw
    data = json.loads(debug_trace.read('alice', request_id))
    assert [event['stage'] for event in data['events']] == ['model_request', 'model_response']
    assert '13812345678' not in json.dumps(data, ensure_ascii=False)
    assert 'abc.def' not in json.dumps(data, ensure_ascii=False)
    assert '123456' not in json.dumps(data, ensure_ascii=False)
    with pytest.raises(FileNotFoundError):
        debug_trace.read('bob', request_id)

    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://bank') as client:
        assert (await client.get(f'/api/v1/nova/debug/{request_id}')).status_code == 401
        app.dependency_overrides[get_current_user] = lambda: {'user_id': 'bob'}
        assert (await client.get(f'/api/v1/nova/debug/{request_id}')).status_code == 404
        app.dependency_overrides[get_current_user] = lambda: {'user_id': 'alice'}
        response = await client.get(f'/api/v1/nova/debug/{request_id}')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert response.json()['events'][1]['content']['content'] == '已处理'
