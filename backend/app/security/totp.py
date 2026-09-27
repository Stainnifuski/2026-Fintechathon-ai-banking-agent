"""TOTP（基于时间的一次性密码，RFC 6238）工具。

与 Google Authenticator / 微软 Authenticator / 1Password 等标准验证器 App 兼容：
用户扫描 otpauth:// 二维码后，App 每 30 秒基于共享密钥算出一个 6 位码，
后端用同一密钥独立算出并比对。全程本地计算，不依赖短信或第三方服务。
"""

import base64
import time
from io import BytesIO

import pyotp
import qrcode

# 验证器 App 中显示的发行方名称。
ISSUER_NAME = "AI 模拟银行"
# 允许的时间漂移窗口：1 表示接受前后各 30 秒，容忍手机与服务器的轻微时差。
VALID_WINDOW = 1
# TOTP 步长（秒），与验证器 App 默认一致。
TIME_STEP = 30


def generate_secret() -> str:
    """生成一个新的 base32 共享密钥。"""
    return pyotp.random_base32()


def provisioning_uri(secret: str, *, account_name: str) -> str:
    """生成 otpauth:// URI，供验证器 App 扫码或手动录入。

    Args:
        secret: base32 共享密钥。
        account_name: 在验证器中标识该账户的名称（如姓名或手机号）。
    """
    return pyotp.TOTP(secret).provisioning_uri(
        name=account_name, issuer_name=ISSUER_NAME
    )


def qr_data_uri(uri: str) -> str:
    """把 otpauth URI 渲染成 PNG 二维码，返回可直接用于 <img src> 的 data URI。"""
    image = qrcode.make(uri)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    return f"data:image/png;base64,{encoded}"


def current_timestep() -> int:
    """当前 TOTP 时间步序号，用于防重放。"""
    return int(time.time()) // TIME_STEP


def verify_code(secret: str, code: str) -> bool:
    """校验用户提交的 6 位验证码是否与密钥匹配。"""
    if not code or not code.strip():
        return False
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=VALID_WINDOW)
