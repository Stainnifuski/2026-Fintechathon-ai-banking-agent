"""Owned bill metadata management. These endpoints never move money."""
from contextlib import asynccontextmanager
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from ..database.connection import get_db
from ..middleware.auth import get_current_user
from ..schemas.bill_features import MergeApply
from ..schemas.bill_foundation import AnnotationUpdate, MerchantMerge, MerchantAliasCreate, RecurringUpdate
from ..schemas.bill_insights import BillTransaction
from ..services.bank import bill_foundation as service
from ..services.bank.bill_recurring import list_recurring, set_enabled
from ..services.bank.bill_service import transaction_view, EXCLUDED_WEALTH_TYPES
from ..services.bank.transfer_service import BankError

router = APIRouter(prefix='/api/v1/bills', tags=['bill metadata'])


@asynccontextmanager
async def guarded(db, *, write=False):
    try:
        yield
        if write:
            await db.commit()
    except BankError as error:
        await db.rollback()
        raise HTTPException(error.status_code, detail={'code':error.code, 'message':error.message}) from error


@router.get('/foundations')
async def catalog(user=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    async with guarded(db):
        return await service.catalog(db, user['user_id'])


@router.post('/foundations/refresh')
async def refresh(user=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    async with guarded(db, write=True):
        return (await service.sync_users(db, [user['user_id']]))[user['user_id']]


@router.get('/tags')
async def tags(user=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    async with guarded(db):
        return await service.list_tags(db, user['user_id'])


@router.put('/transactions/{tx_id}/annotations')
async def annotate(tx_id: str, body: AnnotationUpdate, user=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    async with guarded(db, write=True):
        return await service.annotate(db, user['user_id'], tx_id, body)


@router.get('/transactions/{tx_id}', response_model=BillTransaction)
async def linked_detail(tx_id: str, user=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    async with guarded(db):
        tx = await service.owned_transaction(db, user['user_id'], tx_id)
        if tx.type in EXCLUDED_WEALTH_TYPES:
            raise BankError('此记录请在资金流水中查看', 'EXCLUDED_TRANSACTION', 404)
        metadata = await service.metadata_views(db, user['user_id'], [tx_id])
        return transaction_view(tx, metadata.get(tx_id))


@router.post('/merchants/{merchant_id}/aliases')
async def alias(merchant_id: str, body: MerchantAliasCreate, user=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    async with guarded(db, write=True):
        return await service.add_alias(db, user['user_id'], merchant_id, body.name)


@router.post('/merchants/{merchant_id}/merge')
async def merge(merchant_id: str, body: MergeApply, user=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    async with guarded(db, write=True):
        return await service.merge_merchants(db, user['user_id'], merchant_id, body.source_merchant_id, body.preview_digest)


@router.get('/recurring')
async def recurring(user=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    async with guarded(db):
        return await list_recurring(db, user['user_id'])


@router.patch('/recurring/{stream_id}')
async def recurring_update(stream_id: str, body: RecurringUpdate, user=Depends(get_current_user), db: AsyncSession=Depends(get_db)):
    async with guarded(db, write=True):
        return await set_enabled(db, user['user_id'], stream_id, body)
