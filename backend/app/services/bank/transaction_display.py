"""Customer-facing transfer counterparties, resolved inside the bank boundary."""
from sqlalchemy import select

from ...models.account import AccountORM
from ...models.user import UserORM


async def transfer_counterparty_names(db, transactions) -> dict[str, str]:
    account_ids = {tx.counterparty for tx in transactions
                   if tx.type in {"transfer_out", "transfer_in"} and tx.counterparty}
    if not account_ids:
        return {}
    rows = await db.execute(select(AccountORM.account_id, UserORM.name)
                            .join(UserORM, UserORM.user_id == AccountORM.user_id)
                            .where(AccountORM.account_id.in_(account_ids)))
    return {account_id: name for account_id, name in rows}


def display_counterparty(transaction, names: dict[str, str]) -> str:
    if transaction.type not in {"transfer_out", "transfer_in"}:
        return transaction.counterparty
    return names.get(transaction.counterparty) or (
        "对方账户信息不可用" if transaction.counterparty.startswith("A") else transaction.counterparty
    )
