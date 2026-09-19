"""用户可见流水查询；转账写入只允许通过任务编排和模拟银行层。"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.account import AccountORM
from ..models.audit_log import AuditLogORM
from ..models.transaction import TransactionORM


async def create_audit_log(audit_data: dict) -> str:
    """保留审计中间件使用的写入入口。"""
    from ..database.connection import AsyncSessionLocal
    audit_id = f"AUD-{uuid4().hex[:16].upper()}"
    async with AsyncSessionLocal() as session:
        session.add(AuditLogORM(
            audit_id=audit_id, session_id=audit_data.get("session_id", ""), user_id=audit_data.get("user_id", ""),
            intent=audit_data.get("intent", ""), permission_level=audit_data.get("permission_level", "green"),
            action=audit_data.get("action", ""), params=audit_data.get("params", {}),
            user_confirmed=audit_data.get("user_confirmed"), mfa_method=audit_data.get("mfa_method"),
            result=audit_data.get("result", "success"), decision_chain=audit_data.get("decision_chain", []),
            ip=audit_data.get("ip"), device_id=audit_data.get("device_id"),
        ))
        await session.commit()
    return audit_id


async def get_transaction_history(db: AsyncSession, user_id: str, limit: int = 50) -> list[dict]:
    records = (await db.execute(select(TransactionORM).join(AccountORM).where(
        AccountORM.user_id == user_id).order_by(TransactionORM.timestamp.desc()).limit(limit))).scalars().all()
    return [{"tx_id": tx.tx_id, "account_id": tx.account_id, "type": tx.type, "amount": tx.amount,
             "counterparty": tx.counterparty, "category": tx.category, "note": tx.note,
             "timestamp": tx.timestamp, "status": tx.status, "channel": tx.channel} for tx in records]


async def get_transaction_detail(db: AsyncSession, user_id: str, tx_id: str) -> dict | None:
    tx = (await db.execute(select(TransactionORM).join(AccountORM).where(
        TransactionORM.tx_id == tx_id, AccountORM.user_id == user_id))).scalar_one_or_none()
    if tx is None:
        return None
    return {"tx_id": tx.tx_id, "account_id": tx.account_id, "type": tx.type, "amount": tx.amount,
            "counterparty": tx.counterparty, "category": tx.category, "note": tx.note,
            "timestamp": tx.timestamp, "status": tx.status, "channel": tx.channel}
