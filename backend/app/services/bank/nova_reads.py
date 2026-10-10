"""NOVA 只读 DTO；所有银行 ORM 查询和归属检查保留在银行服务层。"""
import re

from sqlalchemy import select

from ...domain.money import exact_fen
from ...models.account import AccountORM
from ...models.transaction import TransactionORM
from ...models.wealth_research import WealthContextORM
from .data_service import list_accounts
from .bill_service import transaction_view
from .transfer_service import BankError


async def financial_overview(db, user_id: str) -> dict:
    rows = await list_accounts(user_id, session=db)
    accounts = [{
        "account_id": row["account_id"], "account_type": row["account_type"],
        "card_number_masked": "****" + re.sub(r"\D", "", row["card_number"])[-4:],
        "status": row["status"], "balance_fen": exact_fen(row["balance"]),
        "available_fen": row["available_fen"], "held_fen": row["held_fen"],
    } for row in rows]
    # 首页仅把 savings 类型计入当前可用资金，信用额度不计入现金。
    cash = [row for row in accounts if row["account_type"] == "savings"]
    return {"accounts": accounts, "cash_account_type": "savings",
            "totals": {key: sum(row[key] for row in cash)
                       for key in ("balance_fen", "available_fen", "held_fen")},
            "notices": ["总额仅汇总储蓄账户，与首页当前可用资金口径一致。",
                        "不包含未来收入、信用额度及理财测试上下文账户；全部为模拟银行数据。"]}


async def transaction_detail(db, user_id: str, tx_id: str) -> dict:
    row = await db.scalar(select(TransactionORM).join(
        AccountORM, TransactionORM.account_id == AccountORM.account_id).where(
        AccountORM.user_id == user_id,
        AccountORM.account_id.not_in(select(WealthContextORM.account_id)),
        TransactionORM.tx_id == tx_id, TransactionORM.status == "completed"))
    if row is None:
        raise BankError("流水不存在或无权访问", "TRANSACTION_NOT_FOUND", 404)
    return transaction_view(row)
