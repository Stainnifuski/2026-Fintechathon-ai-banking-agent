"""应用审计日志服务；银行流水访问位于模拟银行服务内。"""

from uuid import uuid4

from ..models.audit_log import AuditLogORM
from .audit.privacy import clean


async def create_audit_log(audit_data: dict) -> str:
    """保留审计中间件使用的写入入口。"""
    from ..database.connection import AsyncSessionLocal
    audit_id = f"AUD-{uuid4().hex[:16].upper()}"
    async with AsyncSessionLocal() as session:
        session.add(AuditLogORM(
            audit_id=audit_id, session_id=audit_data.get("session_id", ""), user_id=audit_data.get("user_id", ""),
            intent=audit_data.get("intent", ""), permission_level=audit_data.get("permission_level", "green"),
            action=audit_data.get("action", ""), params=clean(audit_data.get("params", {})),
            user_confirmed=audit_data.get("user_confirmed"), mfa_method=audit_data.get("mfa_method"),
            result=audit_data.get("result", "success"), decision_chain=clean(audit_data.get("decision_chain", [])),
            ip=audit_data.get("ip"), device_id=audit_data.get("device_id"),
        ))
        await session.commit()
    return audit_id
