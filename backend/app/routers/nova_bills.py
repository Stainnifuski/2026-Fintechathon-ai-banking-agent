"""Trusted user-only confirmation route. This router is never an agent tool."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from ..database.connection import get_db
from ..middleware.auth import get_current_user
from ..schemas.nova_bills import BillCardDecision
from ..services.bank import nova_bills
from .bill_foundations import guarded

router = APIRouter(prefix='/api/v1/nova/conversations', tags=['nova bill preferences'])


@router.get('/{session_id}/bill-actions')
async def cards(session_id:str, user=Depends(get_current_user), db:AsyncSession=Depends(get_db)):
    async with guarded(db):
        return await nova_bills.cards(db, user['user_id'], session_id)


@router.post('/{session_id}/bill-actions/{action_id}/decision')
async def decide(session_id:str, action_id:str, body:BillCardDecision,
                 user=Depends(get_current_user), db:AsyncSession=Depends(get_db)):
    async with guarded(db, write=True):
        return await nova_bills.decide(db, user['user_id'], session_id, action_id, body.decision)


@router.get('/{session_id}/bill-results')
async def result_cards(session_id:str, user=Depends(get_current_user), db:AsyncSession=Depends(get_db)):
    from ..services.bank import nova_bill_results
    from ..services.bank.nova_conversations import ConversationError
    from fastapi import HTTPException
    try:
        return await nova_bill_results.cards(db,user['user_id'],session_id)
    except ConversationError as error:
        raise HTTPException(error.status_code,detail={'code':'CONVERSATION_NOT_FOUND','message':error.message}) from error
