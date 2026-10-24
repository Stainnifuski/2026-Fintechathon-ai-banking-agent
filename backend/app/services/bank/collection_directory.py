"""Owner-scoped directory reads for chat collection drafts (bank-owned ORMs stay in this package)."""
from sqlalchemy import select

from ...models import AccountORM, ContactORM, UserORM
from .account_scope import isolated_account_ids


async def collection_contacts(db, user_id: str) -> list[dict]:
    """Saved contacts that map to exactly one active bank user with the same name."""
    rows = (await db.execute(select(ContactORM, UserORM).join(
        UserORM, ContactORM.phone == UserORM.phone
    ).where(ContactORM.user_id == user_id, UserORM.user_id != user_id,
            UserORM.status == "active").order_by(ContactORM.name, ContactORM.contact_id))).all()
    result = []
    for contact, user in rows:
        if contact.name != user.name:
            continue
        result.append({"contact_id": contact.contact_id, "user_id": user.user_id,
                       "name": contact.name, "nickname": contact.nickname,
                       "label": contact.nickname or contact.name})
    return result


async def collection_accounts(db, user_id: str) -> list[dict]:
    """Owner's active savings/checking accounts that can receive a payment request."""
    rows = (await db.scalars(select(AccountORM).where(
        AccountORM.user_id == user_id, AccountORM.status == "active",
        AccountORM.account_type.in_(["savings", "checking"]),
        AccountORM.account_id.not_in(isolated_account_ids())
    ).order_by(AccountORM.account_type.desc(), AccountORM.account_id))).all()
    return [{"account_id": row.account_id, "card_masked": row.card_number[-4:],
             "account_type": row.account_type} for row in rows]
