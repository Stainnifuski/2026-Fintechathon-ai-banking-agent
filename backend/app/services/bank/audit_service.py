"""与银行事实同事务写入审计；不复制手机号、验证码或备注内容。"""

import hashlib
from sqlalchemy.ext.asyncio import AsyncSession
from ...models.audit_log import AuditLogORM


def record_bank_event(
    session: AsyncSession, *, event_key: str, subject_user_id: str,
    action: str, result: str, facts: dict, decision_chain: list[str],
) -> None:
    """仅记录银行实际验证的事实，授权等级由3/5号以操作号关联。"""
    digest = hashlib.sha256(f"{action}:{event_key}".encode()).hexdigest()
    session.add(AuditLogORM(
        audit_id=digest, session_id=event_key[:64], user_id=subject_user_id,
        intent="bank_business", permission_level="delegated",
        action=action, result=result, params=facts,
        user_confirmed=None, decision_chain=decision_chain,
    ))
