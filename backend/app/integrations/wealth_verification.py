"""演示验证隔离开关；以后替换提供者，历史证据永不改写成真实验证。"""

import os
from sqlalchemy.ext.asyncio import AsyncSession


def demo_verification_enabled(db: AsyncSession) -> bool:
    """检查实际数据库名而非请求参数；默认关闭，普通业务库无法启用。"""
    name = db.get_bind().url.database or ""
    basename = name.replace("\\", "/").split("/")[-1]
    return (os.getenv("WEALTH_DEMO_VERIFICATION_ENABLED") == "true"
            and (name.startswith("bank_test_wealth_") or name.startswith("wealth_test")
                 or basename.startswith(("wealth-demo", "wealth_test"))))


def verification_mode(db: AsyncSession) -> dict:
    """向可信页面明确说明当前提供者，不向AI授予验证工具。"""
    demo = demo_verification_enabled(db)
    return {"mode": "SIMULATED_CLICK" if demo else "TOTP", "simulated": demo,
            "replacement_required": demo, "provider_version": "demo-click-v1" if demo else "totp-v1"}
