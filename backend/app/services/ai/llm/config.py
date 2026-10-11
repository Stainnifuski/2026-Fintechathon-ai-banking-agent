"""全局模型配置；业务模块不再读取服务商密钥或启用开关。"""

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

from .errors import LLMError

logger = logging.getLogger(__name__)
_warned_legacy: set[str] = set()
_PLACEHOLDERS = {"your-api-key", "your-api-key-here", "placeholder", "替换为你的密钥"}


def _valid_key(value: str) -> bool:
    return bool(value and value.lower() not in _PLACEHOLDERS)


def _connection() -> tuple[str, str, str, bool]:
    """旧密钥仅继承本组或明确配置的全局地址，不猜测服务商。"""
    key = os.getenv("LLM_API_KEY", "").strip()
    if _valid_key(key):
        return key, os.getenv("LLM_BASE_URL", "").strip(), os.getenv("LLM_MODEL", "").strip(), False
    for prefix in ("TRANSFER_MODEL", "WEALTH_MODEL", "BILL_MODEL"):
        legacy_key = os.getenv(prefix + "_API_KEY", "").strip()
        if _valid_key(legacy_key):
            if prefix not in _warned_legacy:
                logger.warning("%s_* 已弃用，请迁移到统一 LLM_* 配置；该配置将用于所有 AI 模块", prefix)
                _warned_legacy.add(prefix)
            legacy_base = os.getenv(prefix + "_BASE_URL", "").strip()
            shared_base = os.getenv("LLM_BASE_URL", "").strip()
            model = os.getenv(prefix + "_NAME", "").strip()
            if not legacy_base or legacy_base.rstrip("/") == shared_base.rstrip("/"):
                model = model or os.getenv("LLM_MODEL", "").strip()
            return legacy_key, legacy_base or shared_base, model, True
    return "", os.getenv("LLM_BASE_URL", "").strip(), os.getenv("LLM_MODEL", "").strip(), False


def get_mode() -> Literal["offline", "model"]:
    """auto 在存在有效密钥时启用模型；旧业务开关不再生效。"""
    mode = os.getenv("LLM_MODE", "auto").strip().lower()
    if mode not in {"auto", "offline", "model"}:
        raise LLMError("MODEL_CONFIGURATION_INVALID")
    return "offline" if mode == "offline" or (mode == "auto" and not _connection()[0]) else "model"


def _number(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError()
        return value
    except ValueError:
        raise LLMError("MODEL_CONFIGURATION_INVALID") from None


def _integer(name: str, default: int, maximum: int) -> int:
    value = _number(name, default, minimum=1, maximum=maximum)
    if not value.is_integer():
        raise LLMError("MODEL_CONFIGURATION_INVALID")
    return int(value)


def get_timeout() -> float:
    """理解层的时限不依赖服务商配置，也适用于注入模型。"""
    return _number("LLM_TIMEOUT", 35, minimum=0.001, maximum=600)


@dataclass(frozen=True)
class LLMSettings:
    api_key: str = field(repr=False)
    base_url: str
    model: str
    provider: Literal["deepseek", "compatible"]
    mode: Literal["offline", "model"]
    timeout: float
    connect_timeout: float
    max_connections: int
    max_tokens: int
    temperature: float


def get_settings() -> LLMSettings:
    """默认沿用团队 DeepSeek 服务；其他服务须指定模型名。"""
    mode = get_mode()
    key, base, model, legacy = _connection()
    if (mode == "model" and not key) or (legacy and not base):
        raise LLMError("MODEL_CONFIGURATION_MISSING")
    if not key.isascii() or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in key):
        raise LLMError("MODEL_CONFIGURATION_INVALID")
    base = (base or "https://api.deepseek.com").rstrip("/")
    try:
        url = urlsplit(base)
        if (url.scheme not in {"https", "http"} or not url.hostname or url.username
                or url.password or url.query or url.fragment or url.port == 0
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in base)
                or url.path.endswith("/chat/completions")):
            raise ValueError()
    except ValueError:
        raise LLMError("MODEL_CONFIGURATION_INVALID") from None
    provider = os.getenv("LLM_PROVIDER", "").strip().lower() or (
        "deepseek" if url.hostname == "api.deepseek.com" else "compatible"
    )
    if provider not in {"deepseek", "compatible"}:
        raise LLMError("MODEL_CONFIGURATION_INVALID")
    if not model:
        if url.hostname != "api.deepseek.com":
            raise LLMError("MODEL_CONFIGURATION_MISSING")
        model = "deepseek-flash"
    if any(ord(c) < 32 or ord(c) == 127 for c in model):
        raise LLMError("MODEL_CONFIGURATION_INVALID")
    return LLMSettings(
        api_key=key, base_url=base, model=model, provider=provider, mode=mode,
        timeout=get_timeout(),
        connect_timeout=_number("LLM_CONNECT_TIMEOUT", 10, minimum=0.001, maximum=600),
        max_connections=_integer("LLM_MAX_CONNECTIONS", 4, 100),
        max_tokens=_integer("LLM_MAX_TOKENS", 4096, 1000000),
        temperature=_number("LLM_TEMPERATURE", 0, minimum=0, maximum=2),
    )
