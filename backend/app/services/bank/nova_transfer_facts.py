"""Bank-owned recipient/history reads for NOVA; detached facts, no ORM leakage."""
from dataclasses import dataclass
from sqlalchemy import select
from ...models.account import AccountORM
from ...models.contact import ContactORM
from ...models.transaction import TransactionORM
from ...models.user import UserORM
from ...models.transfer_recipient_history import TransferRecipientHistoryCutoffORM
from .contacts import _transfer_methods
from .account_scope import isolated_account_ids
from .transfer_cards import transfer_card_number


@dataclass(frozen=True)
class OutgoingTransferFact:
    tx_id: str
    amount_fen: int
    counterparty: str


@dataclass(frozen=True)
class RecipientFact:
    account_id: str
    name: str


async def owned_outgoing_transfer(db, user_id, tx_id):
    row = await db.scalar(select(TransactionORM).join(AccountORM,
        TransactionORM.account_id == AccountORM.account_id).where(
        TransactionORM.tx_id == tx_id, TransactionORM.type == 'transfer_out',
        TransactionORM.status == 'completed', AccountORM.user_id == user_id))
    return OutgoingTransferFact(row.tx_id, row.amount_fen, row.counterparty) if row else None


async def historical_recipient(db, user_id, account_id):
    account = await db.get(AccountORM, account_id)
    owner = await db.get(UserORM, account.user_id) if account else None
    if not account or not owner or account.status != 'active' or owner.status != 'active' or account.user_id == user_id:
        return None
    return RecipientFact(account.account_id, owner.name)


async def known_recipients(db, user_id, name):
    contacts = (await db.scalars(select(ContactORM).where(ContactORM.user_id == user_id,
        (ContactORM.name == name) | (ContactORM.nickname == name)))).all()
    found = [{'name': row.name, 'phone': row.phone if row.phone_known else None,
        'card': row.account_number if row.card_known else None, 'account_hint': None,
        'source': 'saved_contact'} for row in contacts]
    if found:
        return found
    cutoff = await db.get(TransferRecipientHistoryCutoffORM, user_id)
    history = await _transfer_methods(db, user_id, cutoff.cleared_at if cutoff else None)
    for row in history.values():
        if row['name'] == name:
            found.append({'name': row['name'], 'phone': row['phone_number'], 'card': row['card_number'],
                'account_hint': row['card_account_id'] or row['phone_account_id'], 'source': 'transfer_history'})
    if found:
        return found
    # Older completed transfers may predate operation metadata. Their ledger
    # still proves the destination account, which must be rechecked before use.
    statement = (select(TransactionORM.counterparty).join(AccountORM,
        TransactionORM.account_id == AccountORM.account_id).where(
        AccountORM.user_id == user_id, TransactionORM.type == 'transfer_out',
        TransactionORM.status == 'completed'))
    if cutoff is not None:
        statement = statement.where(TransactionORM.timestamp > cutoff)
    seen = set()
    for account_id in (await db.scalars(statement)).all():
        if account_id in seen:
            continue
        seen.add(account_id)
        recipient = await historical_recipient(db, user_id, account_id)
        if recipient and recipient.name == name:
            found.append({'name': recipient.name, 'phone': None,
                'card': transfer_card_number(recipient.account_id),
                'account_hint': recipient.account_id, 'source': 'transfer_history'})
    return found


async def card_matches(db, user_id, card, name):
    accounts = (await db.scalars(select(AccountORM).where(AccountORM.status == 'active',
        AccountORM.account_id.not_in(isolated_account_ids())))).all()
    result = []
    for account in accounts:
        if account.user_id == user_id or transfer_card_number(account.account_id) != card:
            continue
        owner = await db.get(UserORM, account.user_id)
        if owner and owner.status == 'active' and (name is None or owner.name == name):
            result.append({'account_id': account.account_id, 'recipient_name': owner.name,
                'label': f'{owner.name} · 账户尾号{card[-4:]}'})
    return result
