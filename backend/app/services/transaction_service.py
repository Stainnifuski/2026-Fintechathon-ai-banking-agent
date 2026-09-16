"""交易业务逻辑 - 转账核心"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select, or_

from ..database.connection import AsyncSessionLocal
from ..models.account import AccountORM
from ..models.contact import ContactORM
from ..models.transaction import TransactionORM
from ..models.audit_log import AuditLogORM


async def execute_transfer(
    from_account: str,
    to_contact: str,
    amount: float,
    note: str | None = None,
    channel: str = "agent",
) -> dict:
    """执行转账操作。

    完整流程：余额校验 → 扣款 → 对方入账 → 生成交易记录。

    Args:
        from_account: 转出账户ID。
        to_contact: 收款联系人ID或账号。
        amount: 转账金额。
        note: 转账备注。
        channel: 操作渠道。

    Returns:
        转账结果字典，包含 tx_id, status, amount 等信息。

    Raises:
        ValueError: 账户不存在或余额不足时抛出。
    """
    async with AsyncSessionLocal() as session:
        # 查询转出账户
        stmt = select(AccountORM).where(AccountORM.account_id == from_account)
        result = await session.execute(stmt)
        sender = result.scalar_one_or_none()

        if sender is None:
            raise ValueError(f"转出账户 {from_account} 不存在")

        if sender.balance < amount:
            raise ValueError(
                f"余额不足：当前余额 {sender.balance}，转账金额 {amount}"
            )

        if sender.daily_transfer_used + amount > sender.daily_transfer_limit:
            raise ValueError(
                f"超出日转账限额：限额 {sender.daily_transfer_limit}，"
                f"已用 {sender.daily_transfer_used}，本次 {amount}"
            )

        # 扣款
        sender.balance -= amount
        sender.daily_transfer_used += amount

        # 生成交易记录
        now = datetime.now()
        tx_id = f"TX{now.strftime('%Y%m%d')}{uuid4().hex[:6].upper()}"

        tx_out = TransactionORM(
            tx_id=tx_id,
            account_id=from_account,
            type="transfer_out",
            amount=amount,
            counterparty=to_contact,
            category="transfer",
            note=note,
            timestamp=now,
            status="completed",
            channel=channel,
        )
        session.add(tx_out)

        await session.commit()

        return {
            "tx_id": tx_id,
            "status": "completed",
            "amount": amount,
            "from_account": from_account,
            "to_contact": to_contact,
            "message": f"成功转账 {amount} 元",
            "timestamp": now,
        }


async def find_contact(user_id: str, keyword: str) -> list[dict]:
    """按名字/手机号/昵称模糊匹配联系人。

    Args:
        user_id: 用户ID。
        keyword: 搜索关键词。

    Returns:
        匹配的联系人列表，按转账次数降序排列。
    """
    async with AsyncSessionLocal() as session:
        like_pattern = f"%{keyword}%"
        stmt = (
            select(ContactORM)
            .where(ContactORM.user_id == user_id)
            .where(
                or_(
                    ContactORM.name.like(like_pattern),
                    ContactORM.nickname.like(like_pattern),
                    ContactORM.phone.like(like_pattern),
                )
            )
            .order_by(ContactORM.transfer_count.desc())
        )
        result = await session.execute(stmt)
        contacts = result.scalars().all()
        return [
            {
                "contact_id": c.contact_id,
                "name": c.name,
                "nickname": c.nickname,
                "phone": c.phone,
                "account_number": c.account_number,
                "bank": c.bank,
                "transfer_count": c.transfer_count,
                "last_transfer": str(c.last_transfer) if c.last_transfer else None,
            }
            for c in contacts
        ]


async def get_transaction_history(
    user_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """查询用户交易记录。

    Args:
        user_id: 用户ID。
        start_date: 起始日期，格式 YYYY-MM-DD。
        end_date: 结束日期，格式 YYYY-MM-DD。
        limit: 返回记录条数上限。

    Returns:
        交易记录字典列表。
    """
    async with AsyncSessionLocal() as session:
        # 先查用户的所有账户ID
        acc_stmt = select(AccountORM.account_id).where(AccountORM.user_id == user_id)
        acc_result = await session.execute(acc_stmt)
        account_ids = [row[0] for row in acc_result.all()]

        if not account_ids:
            return []

        stmt = (
            select(TransactionORM)
            .where(TransactionORM.account_id.in_(account_ids))
            .order_by(TransactionORM.timestamp.desc())
            .limit(limit)
        )

        if start_date:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            stmt = stmt.where(TransactionORM.timestamp >= start_dt)
        if end_date:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            stmt = stmt.where(TransactionORM.timestamp <= end_dt)

        result = await session.execute(stmt)
        transactions = result.scalars().all()
        return [
            {
                "tx_id": tx.tx_id,
                "account_id": tx.account_id,
                "type": tx.type,
                "amount": tx.amount,
                "counterparty": tx.counterparty,
                "category": tx.category,
                "note": tx.note,
                "timestamp": tx.timestamp,
                "status": tx.status,
                "channel": tx.channel,
            }
            for tx in transactions
        ]


async def get_transaction_detail(tx_id: str) -> dict | None:
    """查询单笔交易详情。

    Args:
        tx_id: 交易ID。

    Returns:
        交易详情字典，未找到返回 None。
    """
    async with AsyncSessionLocal() as session:
        stmt = select(TransactionORM).where(TransactionORM.tx_id == tx_id)
        result = await session.execute(stmt)
        tx = result.scalar_one_or_none()
        if tx is None:
            return None
        return {
            "tx_id": tx.tx_id,
            "account_id": tx.account_id,
            "type": tx.type,
            "amount": tx.amount,
            "counterparty": tx.counterparty,
            "category": tx.category,
            "note": tx.note,
            "timestamp": tx.timestamp,
            "status": tx.status,
            "channel": tx.channel,
        }


async def create_audit_log(audit_data: dict) -> str:
    """创建审计日志记录。

    Args:
        audit_data: 审计日志数据字典。

    Returns:
        生成的审计日志ID。
    """
    async with AsyncSessionLocal() as session:
        now = datetime.now()
        audit_id = f"AUD-{now.strftime('%Y%m%d')}-{uuid4().hex[:6].upper()}"

        log = AuditLogORM(
            audit_id=audit_id,
            session_id=audit_data.get("session_id", ""),
            user_id=audit_data.get("user_id", ""),
            timestamp=now,
            intent=audit_data.get("intent", ""),
            permission_level=audit_data.get("permission_level", "green"),
            action=audit_data.get("action", ""),
            params=audit_data.get("params", {}),
            user_confirmed=audit_data.get("user_confirmed"),
            mfa_method=audit_data.get("mfa_method"),
            result=audit_data.get("result", "success"),
            decision_chain=audit_data.get("decision_chain", []),
            ip=audit_data.get("ip"),
            device_id=audit_data.get("device_id"),
        )
        session.add(log)
        await session.commit()

        return audit_id
