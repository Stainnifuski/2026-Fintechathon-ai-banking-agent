"""理财复用3号统一第二因素；历史点击验证不再签发或受理。"""
import hashlib
import json
from sqlalchemy.ext.asyncio import AsyncSession


def verification_reference(rows, *, grouped=False) -> str:
    """短信绑定当前完整授权集合与摘要；改金额/版本不能复用旧挑战。"""
    if not grouped:
        return rows[0].authorization_id
    payload=sorted((r.authorization_id,r.param_digest) for r in rows)
    return "WGROUP"+hashlib.sha256(json.dumps(payload,separators=(",",":" )).encode()).hexdigest()[:56]


def verification_mode(db: AsyncSession) -> dict:
    from ..security.sms import sms_enabled, get_sms_provider
    return {"mode":"UNIFIED_MFA", "simulated":False, "replacement_required":False,
        "provider_version":"unified-mfa-v1", "permission_level":"red",
        "authorization_method":"totp_or_sms", "methods":["totp","sms"] if sms_enabled() else ["totp"],
        "sms_simulated":sms_enabled() and get_sms_provider().name=="mock",
        "notice":"本人确认后使用验证器或短信验证码；默认短信为模拟发送，需校验验证码。"}
