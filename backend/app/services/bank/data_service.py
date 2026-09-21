"""4号模拟银行持有的客户、账户与流水数据访问实现。

银行所有表只能在 ``backend.app.services.bank`` 包内被直接查询或修改。
外层 HTTP 路由只接收普通 DTO，不得接触银行会话或 ORM。
"""

from uuid import uuid4

from sqlalchemy import select

from ...database.connection import AsyncSessionLocal
from ...models.account import AccountORM
from ...models.transaction import TransactionORM
from ...models.user import UserORM
from ...security.passwords import hash_password, verify_password
from .transfer_service import BankError


def _account_dict(account: AccountORM) -> dict:
    return {
        "account_id": account.account_id,
        "user_id": account.user_id,
        "account_type": account.account_type,
        "card_number": account.card_number,
        "balance": account.balance,
        "daily_transfer_used": account.daily_transfer_used,
        "daily_transfer_limit": account.daily_transfer_limit,
        "status": account.status,
    }


def _user_dict(user: UserORM) -> dict:
    return {
        "user_id": user.user_id,
        "name": user.name,
        "phone": user.phone,
        "id_card": user.id_card,
        "risk_level": user.risk_level,
        "created_at": user.created_at,
        "status": user.status,
    }


def _transaction_dict(transaction: TransactionORM) -> dict:
    return {
        "tx_id": transaction.tx_id,
        "account_id": transaction.account_id,
        "type": transaction.type,
        "amount": transaction.amount,
        "counterparty": transaction.counterparty,
        "category": transaction.category,
        "note": transaction.note,
        "timestamp": transaction.timestamp,
        "status": transaction.status,
        "channel": transaction.channel,
    }


async def list_accounts(user_id: str) -> list[dict]:
    async with AsyncSessionLocal() as session:
        accounts = (
            await session.execute(
                select(AccountORM)
                .where(AccountORM.user_id == user_id)
                .order_by(AccountORM.account_type.desc(), AccountORM.account_id)
            )
        ).scalars().all()
    return [_account_dict(account) for account in accounts]


async def list_balances(
    user_id: str, account_type: str | None = None
) -> list[dict]:
    statement = select(AccountORM).where(AccountORM.user_id == user_id)
    if account_type:
        statement = statement.where(AccountORM.account_type == account_type)
    async with AsyncSessionLocal() as session:
        accounts = (await session.execute(statement)).scalars().all()
    return [_account_dict(account) for account in accounts]


async def list_recipients(subject_user_id: str) -> list[dict]:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(UserORM, AccountORM)
                .join(AccountORM, AccountORM.user_id == UserORM.user_id)
                .where(
                    UserORM.user_id != subject_user_id,
                    UserORM.status == "active",
                    AccountORM.status == "active",
                )
                .order_by(
                    UserORM.name, AccountORM.account_type.desc(), AccountORM.account_id
                )
            )
        ).all()
    seen: set[str] = set()
    recipients: list[dict] = []
    for user, account in rows:
        if user.user_id in seen:
            continue
        seen.add(user.user_id)
        recipients.append(
            {
                "user_id": user.user_id,
                "name": user.name,
                "phone": f"{user.phone[:3]}****{user.phone[-4:]}",
                "account_id": account.account_id,
                "bank": "AI 模拟银行",
            }
        )
    return recipients


async def register_customer(
    *,
    name: str,
    phone: str,
    password: str,
    risk_level: str,
) -> dict:
    phone = phone.strip()
    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(UserORM).where(UserORM.phone == phone))
        if existing.scalar_one_or_none():
            raise BankError("该手机号已注册", "PHONE_ALREADY_REGISTERED", 409)

        user_id = f"U{uuid4().hex[:10].upper()}"
        account_id = f"A{uuid4().hex[:10].upper()}"
        user = UserORM(
            user_id=user_id,
            name=name.strip(),
            phone=phone,
            id_card="未填写",
            password_hash=hash_password(password),
            risk_level=risk_level,
            status="active",
        )
        account = AccountORM(
            account_id=account_id,
            user_id=user_id,
            account_type="savings",
            card_number=f"6214****{account_id[-4:]}",
            balance=0.0,
            daily_transfer_used=0.0,
            daily_transfer_limit=50000.0,
            status="active",
        )
        session.add(user)
        await session.flush()
        session.add(account)
        await session.commit()
        await session.refresh(user)
        return _user_dict(user)


async def authenticate_customer(phone: str, password: str) -> dict:
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(UserORM).where(UserORM.phone == phone.strip()))
        ).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise BankError("手机号或密码错误", "INVALID_CREDENTIALS", 401)
    if user.status != "active":
        raise BankError("用户已被停用", "CUSTOMER_INACTIVE", 403)
    return _user_dict(user)


async def get_customer(user_id: str) -> dict | None:
    async with AsyncSessionLocal() as session:
        user = await session.get(UserORM, user_id)
    return _user_dict(user) if user else None


async def list_transactions(user_id: str, limit: int = 50) -> list[dict]:
    async with AsyncSessionLocal() as session:
        records = (
            await session.execute(
                select(TransactionORM)
                .join(AccountORM)
                .where(AccountORM.user_id == user_id)
                .order_by(TransactionORM.timestamp.desc())
                .limit(limit)
            )
        ).scalars().all()
    return [_transaction_dict(transaction) for transaction in records]


async def get_transaction(user_id: str, transaction_id: str) -> dict | None:
    async with AsyncSessionLocal() as session:
        transaction = (
            await session.execute(
                select(TransactionORM)
                .join(AccountORM)
                .where(
                    TransactionORM.tx_id == transaction_id,
                    AccountORM.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
    return _transaction_dict(transaction) if transaction else None
