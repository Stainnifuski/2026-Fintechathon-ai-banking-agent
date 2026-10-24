import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from backend.app.routers import voice
from backend.app.middleware.auth import get_current_user


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv('SENSEVOICE_URL', 'http://sensevoice:50000')
    voice.active_users.clear()
    application = FastAPI()
    application.include_router(voice.router)
    return application


def proxy_client(app, monkeypatch, handler):
    original = httpx.AsyncClient
    client = original(transport=httpx.ASGITransport(app=app), base_url='http://bank')
    monkeypatch.setattr(voice.httpx, 'AsyncClient', lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))
    app.dependency_overrides[get_current_user] = lambda: {'user_id': 'voice-test'}
    return client


@pytest.mark.asyncio
async def test_voice_requires_login_before_reading_audio(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://bank') as client:
        result = await client.post('/api/v1/nova/voice/transcriptions', content=b'audio', headers={'Content-Type': 'audio/wav'})
        assert result.status_code == 401
        assert not voice.active_users


@pytest.mark.asyncio
async def test_transcription_only_returns_text_without_forwarding_credentials(app, monkeypatch):
    def upstream(request):
        assert request.url == 'http://sensevoice:50000/transcribe'
        assert request.content == b'example-audio'
        assert 'authorization' not in request.headers
        return httpx.Response(200, json={'text': '  给李四转100元  '})
    async with proxy_client(app, monkeypatch, upstream) as client:
        response = await client.post('/api/v1/nova/voice/transcriptions', content=b'example-audio',
            headers={'Content-Type': 'audio/webm;codecs=opus', 'Authorization': 'Bearer never-forward'})
    assert response.json() == {'text': '给李四转100元', 'model': 'SenseVoiceSmall'}
    assert response.headers['cache-control'] == 'no-store'
    assert not voice.active_users


@pytest.mark.asyncio
@pytest.mark.parametrize('body,media,status', [(b'', 'audio/wav', 422),
    (b'a', 'text/plain', 415), (b'x' * (voice.MAX_BYTES + 1), 'audio/wav', 413)],
    ids=['empty', 'wrong-media', 'over-limit'])
async def test_invalid_audio_does_not_reach_asr(app, monkeypatch, body, media, status):
    def unexpected(_):
        pytest.fail('Rejected audio reached ASR')
    async with proxy_client(app, monkeypatch, unexpected) as client:
        response = await client.post('/api/v1/nova/voice/transcriptions', content=body, headers={'Content-Type': media})
    assert response.status_code == status
    assert not voice.active_users


@pytest.mark.asyncio
@pytest.mark.parametrize('upstream_status,expected', [(413, 413), (422, 422), (429, 429), (500, 503), (503, 503)])
async def test_upstream_errors_are_sanitized_and_admission_is_released(app, monkeypatch, upstream_status, expected):
    async with proxy_client(app, monkeypatch, lambda _: httpx.Response(upstream_status, text='secret-internal-message')) as client:
        response = await client.post('/api/v1/nova/voice/transcriptions', content=b'a', headers={'Content-Type': 'audio/wav'})
    assert response.status_code == expected
    assert 'secret-internal-message' not in response.text
    assert not voice.active_users


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [{'text': ''}, {'text': 123}, {'text': 'a' * 2001}, []])
async def test_invalid_asr_response_is_not_passed_to_input(app, monkeypatch, payload):
    async with proxy_client(app, monkeypatch, lambda _: httpx.Response(200, json=payload)) as client:
        response = await client.post('/api/v1/nova/voice/transcriptions', content=b'a', headers={'Content-Type': 'audio/wav'})
    assert response.status_code in (422, 503)
    assert not voice.active_users


@pytest.mark.asyncio
async def test_timeout_is_recoverable(app, monkeypatch):
    def timeout(_):
        raise httpx.ReadTimeout('private endpoint')
    async with proxy_client(app, monkeypatch, timeout) as client:
        response = await client.post('/api/v1/nova/voice/transcriptions', content=b'a', headers={'Content-Type': 'audio/wav'})
    assert response.status_code == 504
    assert 'private endpoint' not in response.text
    assert not voice.active_users


@pytest.mark.asyncio
async def test_busy_user_is_rejected_without_second_upload(app, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    async def upstream(_):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={'text': '测试'})
    async with proxy_client(app, monkeypatch, upstream) as client:
        first = asyncio.create_task(client.post('/api/v1/nova/voice/transcriptions', content=b'a', headers={'Content-Type': 'audio/wav'}))
        await entered.wait()
        second = await client.post('/api/v1/nova/voice/transcriptions', content=b'b', headers={'Content-Type': 'audio/wav'})
        assert second.status_code == 429
        release.set()
        assert (await first).status_code == 200
    assert not voice.active_users


@pytest.mark.asyncio
@pytest.mark.parametrize('ready,loading', [(True, False), (False, True), (False, False)])
async def test_readiness_is_independent_of_bank_health(app, monkeypatch, ready, loading):
    async with proxy_client(app, monkeypatch, lambda _: httpx.Response(200, json={'ready': ready, 'loading': loading})) as client:
        response = await client.get('/api/v1/nova/voice/status')
    assert response.status_code == 200
    assert response.json()['ready'] is ready
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
async def test_unconfigured_service_keeps_text_mode_available(app, monkeypatch):
    monkeypatch.delenv('SENSEVOICE_URL')
    app.dependency_overrides[get_current_user] = lambda: {'user_id': 'voice-test'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://bank') as client:
        assert (await client.get('/api/v1/nova/voice/status')).json()['ready'] is False
        assert (await client.post('/api/v1/nova/voice/transcriptions')).status_code == 503
