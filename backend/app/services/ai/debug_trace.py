"""Opt-in, owner-scoped NOVA diagnostics. Never writes content to application logs."""
import contextvars
import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..bank import nova_conversations

_active = contextvars.ContextVar('nova_debug_trace', default=None)
_root = Path(os.getenv('NOVA_DEBUG_DIR', '/app/var/nova-debug'))
_secret_keys = re.compile(r'(^|_)(authorization|api_key|access_token|refresh_token|password|secret|otp|totp|verification_code|sms_code|token)($|_)', re.I)
_bearer = re.compile(r'Bearer\s+[A-Za-z0-9._~+/=-]+', re.I)
_embedded_secret = re.compile(r'(["\'](?:authorization|api_key|access_token|refresh_token|password|secret|otp|totp|verification_code|sms_code)["\']\s*:\s*["\'])[^"\']+', re.I)


def _clean(value):
    if isinstance(value, dict):
        return {str(k): ('[已隐藏]' if _secret_keys.search(str(k)) else _clean(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, str):
        return nova_conversations.redact(_embedded_secret.sub(r'\1[已隐藏]', _bearer.sub('Bearer [已隐藏]', value)))
    return value if value is None or isinstance(value, (bool, int, float)) else str(value)


def record(stage: str, content) -> None:
    trace = _active.get()
    if trace is None:
        return
    entry = {'at': datetime.now(timezone.utc).isoformat(), 'stage': stage, 'content': _clean(content)}
    trace['events'].append(entry)


def _path(user_id: str, request_id: str) -> Path:
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', request_id):
        raise ValueError('Invalid debug request id')
    owner = hashlib.sha256(user_id.encode()).hexdigest()
    return _root / owner / f'{request_id}.json.enc'


@contextmanager
def capture(user_id: str, request_id: str, enabled: bool, session_id: str | None = None):
    if not enabled:
        yield
        return
    trace = {'version': 2, 'request_id': request_id, 'session_id': session_id,
             'started_at': datetime.now(timezone.utc).isoformat(), 'events': [], 'truncated': False}
    token = _active.set(trace)
    try:
        yield
    except BaseException as error:
        record('error', {'type': type(error).__name__, 'code': getattr(error, 'code', None)})
        raise
    finally:
        _active.reset(token)
        path = _path(user_id, request_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        content = json.dumps(trace, ensure_ascii=False, default=str)
        tmp = path.with_suffix('.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(nova_conversations._encrypt(content))
        os.replace(tmp, path)


def read(user_id: str, request_id: str) -> bytes:
    path = _path(user_id, request_id)
    return nova_conversations._decrypt(path.read_text()).encode()


def list_recent(user_id: str, session_id: str, limit: int = 20) -> list[dict]:
    """Find persisted traces for one owned conversation without downloading them."""
    directory = _root / hashlib.sha256(user_id.encode()).hexdigest()
    if not directory.is_dir():
        return []
    matches = []
    for path in sorted(directory.glob('*.json.enc'), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            trace = json.loads(nova_conversations._decrypt(path.read_text()))
        except (ValueError, OSError):
            continue
        if trace.get('session_id') == session_id:
            matches.append({'request_id': trace['request_id'],
                            'started_at': trace.get('started_at'),
                            'event_count': len(trace.get('events', [])),
                            'has_error': any(item.get('stage') == 'error' for item in trace.get('events', []))})
            if len(matches) >= limit:
                break
    return matches
