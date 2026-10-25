"""短信验证码（SMS OTP）工具：验证码生成、哈希比对与可插拔短信通道。

短信码是与 TOTP 并列的第二因素，二者择一完成红色强验证。验证码只以 HMAC
哈希落库，并绑定到具体业务操作（purpose + reference_id），不能跨操作复用。

默认使用 ``MockSmsProvider``：不连接任何真实运营商，只把短信写入进程内
收件箱并记录脱敏日志，供演示环境的开发收件箱读取。接入真实通道时实现
``SmsProvider.send`` 并在 ``get_sms_provider`` 中按 ``SMS_PROVIDER`` 注册。
"""

import hashlib
import hmac
import logging
import os
import secrets
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from ..domain.phone import mask_phone

logger = logging.getLogger(__name__)

CODE_LENGTH = 6
# 单条验证码有效期（秒）与同一操作重发冷却（秒）。
CODE_TTL_SECONDS = int(os.getenv("SMS_CODE_TTL_SECONDS", "300"))
RESEND_COOLDOWN_SECONDS = int(os.getenv("SMS_RESEND_COOLDOWN_SECONDS", "60"))
# 每个用户每小时最多发送条数；单条验证码最多尝试次数。
MAX_SENDS_PER_HOUR = int(os.getenv("SMS_MAX_SENDS_PER_HOUR", "10"))
MAX_ATTEMPTS = 5

PURPOSE_LABELS = {
    "transfer": "转账",
    "card": "卡片管理",
    "wealth": "理财交易",
}


class SmsDeliveryError(Exception):
    """短信通道发送失败。"""


@dataclass(frozen=True)
class SmsMessage:
    phone: str
    content: str
    sent_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SmsProvider(Protocol):
    name: str

    async def send(self, phone: str, content: str) -> None: ...


class MockSmsProvider:
    """演示通道：不真实发送，只保存最近的短信供开发收件箱查看。"""

    name = "mock"

    def __init__(self, capacity: int = 200) -> None:
        self.outbox: deque[SmsMessage] = deque(maxlen=capacity)

    async def send(self, phone: str, content: str) -> None:
        self.outbox.append(SmsMessage(phone=phone, content=content))
        logger.info("模拟短信已发送至 %s", mask_phone(phone))

    def messages_for(self, phone: str, limit: int = 5) -> list[SmsMessage]:
        return [m for m in reversed(self.outbox) if m.phone == phone][:limit]


_provider: SmsProvider | None = None


def get_sms_provider() -> SmsProvider:
    """按 ``SMS_PROVIDER`` 返回短信通道单例；未知取值拒绝启动发送。"""
    global _provider
    if _provider is None:
        name = os.getenv("SMS_PROVIDER", "mock").strip().lower()
        if name != "mock":
            raise SmsDeliveryError(f"未支持的短信通道: {name}")
        _provider = MockSmsProvider()
    return _provider


def set_sms_provider(provider: SmsProvider | None) -> None:
    """替换短信通道（测试或接入真实通道时使用）。"""
    global _provider
    _provider = provider


def sms_enabled() -> bool:
    """``MFA_SMS_ENABLED=false`` 时只保留验证器 TOTP 一种第二因素。"""
    return os.getenv("MFA_SMS_ENABLED", "true").strip().lower() == "true"


def dev_inbox_enabled() -> bool:
    """开发收件箱只在模拟通道且显式开启时可用。"""
    return (os.getenv("SMS_PROVIDER", "mock").strip().lower() == "mock"
            and os.getenv("SMS_DEV_INBOX_ENABLED", "true").strip().lower() == "true")


def generate_code() -> str:
    return f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"


def _hash_key() -> bytes:
    from .auth_configuration import jwt_signing_secret
    return (os.getenv("SMS_CODE_HASH_KEY") or jwt_signing_secret()).encode()


def hash_code(challenge_id: str, code: str) -> str:
    """以挑战 ID 为上下文做 HMAC，库内不保存明文验证码。"""
    return hmac.new(_hash_key(), f"{challenge_id}:{code}".encode(), hashlib.sha256).hexdigest()


def code_matches(challenge_id: str, code: str, code_hash: str) -> bool:
    return hmac.compare_digest(hash_code(challenge_id, code.strip()), code_hash)


def render_message(purpose: str, code: str) -> str:
    label = PURPOSE_LABELS.get(purpose, "高风险")
    minutes = max(1, CODE_TTL_SECONDS // 60)
    return (f"【AI 模拟银行】您正在进行{label}操作，验证码 {code}，{minutes}分钟内有效。"
            "银行工作人员不会向您索取验证码，请勿泄露。")
