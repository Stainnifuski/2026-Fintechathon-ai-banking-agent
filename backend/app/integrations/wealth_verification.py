"""理财模拟授权适配点；后续对接3号统一高风险授权，历史证据保持原样。"""

import os
from sqlalchemy.ext.asyncio import AsyncSession


def demo_verification_enabled(db: AsyncSession) -> bool:
    """普通演示库默认启用；显式false可停用，数据库名不决定授权方式。"""
    return os.getenv("WEALTH_DEMO_VERIFICATION_ENABLED", "true").lower() == "true"


def verification_mode(db: AsyncSession) -> dict:
    """只返回服务端策略；前端不能指定风险级别或签发授权证据。"""
    demo = demo_verification_enabled(db)
    return {"mode": "SIMULATED_CLICK" if demo else "TOTP", "simulated": demo,
            "replacement_required": demo,
            "provider_version": "demo-click-v2" if demo else "totp-v1",
            "permission_level": "red", "authorization_method": "simulated_payment_password" if demo else "totp",
            "integration_target": "project_unified_high_risk_authorization",
            "notice": "演示仅记录本人点击授权，不采集或验证支付密码、短信码、生物识别。"}
