"""Bounded, ephemeral audio adapter for the pinned official SenseVoice model.

Private Compose network only. User authentication belongs to the bank proxy.
No database, user IDs, transcripts or recordings are persisted here.
"""
import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

MAX_BYTES = 4 * 1024 * 1024
MAX_SECONDS = 45
SAMPLE_RATE = 16000
APPROVED_MODEL_REVISION = '3847d57b6bdf2dd8875cb1508d2af43d80a16bf7'
MODEL_REVISION = os.getenv('SENSEVOICE_MODEL_REVISION', APPROVED_MODEL_REVISION)
state = {'ready': False, 'loading': True, 'failed': False}
model = None
inference_lock = asyncio.Lock()
logger = logging.getLogger('bank.voice')


def load_model():
    global model
    if MODEL_REVISION != APPROVED_MODEL_REVISION:
        raise ValueError('ASR_MODEL_REVISION_NOT_APPROVED')
    import torch
    from huggingface_hub import snapshot_download
    from funasr import AutoModel
    # Register only the source pinned in the image. False below also prevents
    # FunASR from pip-installing a model repository's requirements at runtime.
    from model import SenseVoiceSmall as OfficialSenseVoiceSmall  # noqa: F401
    torch.set_num_threads(int(os.getenv('OMP_NUM_THREADS', '2')))
    path = snapshot_download('FunAudioLLM/SenseVoiceSmall', revision=MODEL_REVISION,
                             ignore_patterns=['*.onnx', '*.gguf', 'example/*', 'examples/*'])
    model = AutoModel(model=path, trust_remote_code=False, device='cpu', ncpu=2,
                      disable_update=True, disable_pbar=True, disable_log=True)


async def initialize():
    try:
        await asyncio.to_thread(load_model)
        state['ready'] = True
    except Exception as error:
        state['failed'] = True
        # Never print audio, transcripts, full HTTP URLs or credentials.
        logger.error('SenseVoice initialization failed: %s', type(error).__name__)
    finally:
        state['loading'] = False


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(initialize())
    yield
    if not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get('/health')
async def health():
    return {**state, 'model': 'SenseVoiceSmall', 'device': 'cpu',
            'model_revision': MODEL_REVISION, 'max_seconds': MAX_SECONDS}


async def decode(data):
    """Decode in memory, limiting decoded duration as well as upload size."""
    process = await asyncio.create_subprocess_exec(
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', 'pipe:0',
        '-t', str(MAX_SECONDS + 1), '-vn', '-ar', str(SAMPLE_RATE), '-ac', '1',
        '-f', 'f32le', 'pipe:1', stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        pcm, _ = await asyncio.wait_for(process.communicate(data), timeout=15)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode != 0:
        raise HTTPException(422, '音频无法解码，请重新录音')
    import numpy as np
    samples = np.frombuffer(pcm, dtype='<f4').copy()
    duration = len(samples) / SAMPLE_RATE
    if not 0.2 <= duration <= MAX_SECONDS:
        raise HTTPException(422, '请录制0.2至45秒语音')
    if not np.isfinite(samples).all() or float(np.max(np.abs(samples))) < 0.001:
        raise HTTPException(422, '没有检测到有效声音，请靠近麦克风重试')
    return samples, duration


def recognize(samples):
    import torch
    with torch.inference_mode():
        rows = model.generate(input=samples, cache={}, language='auto', use_itn=True,
                              disable_pbar=True, disable_log=True)
    raw = rows[0].get('text', '') if rows else ''
    return re.sub(r'<\|[^>]*\|>', '', raw).strip()


@app.post('/transcribe')
async def transcribe(request: Request):
    if not state['ready']:
        raise HTTPException(503, '识别服务尚未就绪')
    if inference_lock.locked():
        raise HTTPException(429, '识别服务正在处理其他录音，请稍后重试')
    async with inference_lock:
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise HTTPException(413, '录音文件超过4MB，请缩短录音')
        if not data:
            raise HTTPException(422, '录音为空')
        start = time.monotonic()
        try:
            samples, duration = await decode(bytes(data))
            # Keep the lock until the worker really finishes, even on disconnect.
            worker = asyncio.create_task(asyncio.to_thread(recognize, samples))
            try:
                text = await asyncio.shield(worker)
            except asyncio.CancelledError:
                await worker
                raise
        except HTTPException:
            raise
        except Exception as error:
            logger.warning('Transcription failed: %s', type(error).__name__)
            raise HTTPException(422, '识别失败，请重新录音') from None
        if not text:
            raise HTTPException(422, '没有识别到文字，请重新录音')
        return JSONResponse({'text': text, 'duration_seconds': round(duration, 2),
                             'processing_ms': round((time.monotonic() - start) * 1000)},
                            headers={'Cache-Control': 'no-store'})
