"""Idempotent bank-owned billing index upgrade for existing databases."""
from ...models.transaction import TransactionORM

async def upgrade_indexes(connection):
    for index in TransactionORM.__table__.indexes:
        if index.name.startswith('ix_bill_'):
            await connection.run_sync(lambda sync_connection,ix=index:ix.create(sync_connection,checkfirst=True))
