"""Authenticated audio-to-text only; never invokes NOVA or any bank action."""
import os
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..middleware.auth import get_current_user

router = APIRouter(prefix='/api/v1/nova/voice', tags=['voice'])
MAX_BYTES = 4 * 1024 * 1024
AUDIO_TYPES = {'audio/webm', 'audio/ogg', 'audio/mp4', 'audio/wav', 'audio/x-wav', 'audio/mpeg'}
active_users: set[str] = set()


def service_url():
    value = os.getenv('SENSEVOICE_URL', '').rstrip('/')
    if not value:
        raise HTTPException(503, detail={'code': 'ASR_UNAVAILABLE', 'message': '语音服务未配置，仍可输入文字。'})
    parts = urlsplit(value)
    if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
        raise HTTPException(503, detail={'code': 'ASR_UNAVAILABLE', 'message': '语音服务地址配置无效。'})
    return value


@router.get('/status')
async def status(response: Response, user: dict = Depends(get_current_user)):
    response.headers['Cache-Control'] = 'no-store'
    try:
        url = service_url()
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            result = await client.get(url + '/health')
            result.raise_for_status()
            data = result.json()
        ready = data.get('ready') is True
        message = '语音输入已就绪' if ready else (
            '语音模型正在下载或加载，稍后点击重试；仍可输入文字。' if data.get('loading')
            else '语音模型加载失败，请检查语音服务日志；仍可输入文字。')
        return {'ready': ready, 'message': message, 'max_seconds': 45, 'max_bytes': MAX_BYTES}
    except HTTPException as error:
        return {'ready': False, 'message': error.detail['message'], 'max_seconds': 45, 'max_bytes': MAX_BYTES}
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return {'ready': False, 'message': '语音服务暂不可用，请重试；仍可输入文字。',
                'max_seconds': 45, 'max_bytes': MAX_BYTES}


@router.post('/transcriptions')
async def transcribe(request: Request, response: Response, user: dict = Depends(get_current_user)):
    response.headers['Cache-Control'] = 'no-store'
    url = service_url()
    media_type = request.headers.get('content-type', '').split(';', 1)[0].lower()
    if media_type not in AUDIO_TYPES:
        raise HTTPException(415, detail={'code': 'ASR_AUDIO_TYPE', 'message': '录音格式不支持，请重新录音。'})
    owner = user['user_id']
    # Event-loop-local admission check before the first await; bounded across users.
    if owner in active_users or len(active_users) >= 2:
        raise HTTPException(429, detail={'code': 'ASR_BUSY', 'message': '语音识别忙，请稍后重试。'})
    active_users.add(owner)
    try:
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise HTTPException(413, detail={'code': 'ASR_TOO_LARGE', 'message': '录音超过4MB，请缩短录音。'})
        if not data:
            raise HTTPException(422, detail={'code': 'ASR_EMPTY', 'message': '录音为空，请重新录音。'})
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(80, connect=3), trust_env=False) as client:
                result = await client.post(url + '/transcribe', content=bytes(data),
                                           headers={'Content-Type': media_type})
                if result.status_code in {413, 422, 429}:
                    messages = {413: '录音超过限制，请缩短录音。',
                                422: '没有识别到有效语音，请检查麦克风并录制45秒以内的语音。',
                                429: '语音识别忙，请稍后重试。'}
                    raise HTTPException(result.status_code, detail={'code': 'ASR_REJECTED', 'message': messages[result.status_code]})
                result.raise_for_status()
                payload = result.json()
                text = payload.get('text')
                if not isinstance(text, str) or not text.strip() or len(text) > 2000:
                    raise HTTPException(422, detail={'code': 'ASR_TEXT_INVALID', 'message': '识别结果为空或过长，请缩短录音后重试。'})
                return {'text': text.strip(), 'model': 'SenseVoiceSmall'}
        except httpx.TimeoutException:
            raise HTTPException(504, detail={'code': 'ASR_TIMEOUT', 'message': '语音识别超时，请缩短录音后重试；未发送对话。'}) from None
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            raise HTTPException(503, detail={'code': 'ASR_UNAVAILABLE', 'message': '语音识别服务暂不可用，请稍后重试；未发送对话。'}) from None
    finally:
        active_users.discard(owner)
