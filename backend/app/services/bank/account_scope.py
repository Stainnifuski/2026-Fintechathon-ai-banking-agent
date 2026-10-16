"""Shared bank accounts stay visible across scenes; only explicit sandbox funds are isolated."""
from sqlalchemy import func, select
from ...models.wealth_research import WealthContextORM


def isolated_account_ids():
    # Legacy contexts remain isolated until explicitly bound by the bank admin.
    return select(WealthContextORM.account_id).where(
        func.coalesce(WealthContextORM.data["account_scope"].as_string(), "isolated") != "shared_bank"
    )


def is_shared(context):
    return context is not None and context.data.get("account_scope") == "shared_bank"
