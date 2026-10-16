"""NOVA 只读 DTO；所有银行 ORM 查询和归属检查保留在银行服务层。"""
from .account_scope import isolated_account_ids

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ...domain.money import exact_fen
from ...models.account import AccountORM
from ...models.transaction import TransactionORM
from ...models.wealth_research import WealthContextORM
from .data_service import list_accounts
from .bill_service import transaction_view
from .transfer_service import BankError


async def financial_overview(db, user_id: str) -> dict:
    # NOVA may already have read other tools. Use a dedicated consistent snapshot,
    # just like the public AssetSummary endpoint, without resetting its transaction.
    from .wealth_asset_summary import begin_read
    async with async_sessionmaker(db.bind, expire_on_commit=False)() as snapshot_db:
        await begin_read(snapshot_db)
        return await _financial_overview(snapshot_db, user_id)


async def _financial_overview(db, user_id: str) -> dict:
    from .wealth_service import assets
    summary = (await assets(db, user_id))['asset_summary']
    rows = await list_accounts(user_id, session=db)
    accounts = [{
        "account_id": row["account_id"], "account_type": row["account_type"],
        "card_number_masked": "****" + re.sub(r"\D", "", row["card_number"])[-4:],
        "status": row["status"], "balance_fen": exact_fen(row["balance"]),
        "available_fen": row["available_fen"], "held_fen": row["held_fen"],
    } for row in rows]
    # Preserve the existing ordinary savings cash DTO; total assets have a
    # separate authoritative scope and must never be inferred from this subtotal.
    cash = [row for row in accounts if row["account_type"] == "savings"]
    return {"accounts": accounts, "cash_account_type": "savings", "asset_summary": summary,
            "totals": {key: sum(row[key] for row in cash)
                       for key in ("balance_fen", "available_fen", "held_fen")},
            "notices": ["totals 仅为普通储蓄账户现金小计，不是首页总资产，也不代表当前理财范围的全部可用现金。",
                        "首页和理财页总资产以 asset_summary 为准：当前授权借记账户现金＋持仓市值＋赎回应收；冻结已包含在现金中，不重复相加。",
                        "asset_summary 的 account_ids、asset_scope_id 和 snapshot_id 标明当前授权范围及快照；切换理财模拟上下文时，该范围可能与普通储蓄账户小计不同。",
                        "缺估值时总资产为 null，不能当成零；不包含未来收入或信用额度；全部为模拟银行数据。"]}


async def transaction_detail(db, user_id: str, tx_id: str) -> dict:
    row = await db.scalar(select(TransactionORM).join(
        AccountORM, TransactionORM.account_id == AccountORM.account_id).where(
        AccountORM.user_id == user_id,
        AccountORM.account_id.not_in(isolated_account_ids()),
        TransactionORM.tx_id == tx_id, TransactionORM.status == "completed"))
    if row is None:
        raise BankError("流水不存在或无权访问", "TRANSACTION_NOT_FOUND", 404)
    return transaction_view(row)
