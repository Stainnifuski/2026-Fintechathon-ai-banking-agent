"""Authenticated bank API for NOVA conversation storage and retrieval."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..middleware.auth import get_current_user
from ..services.bank import nova_conversations as bank

router = APIRouter(prefix="/api/v1/bank/nova/conversations", tags=["bank-conversations"])


def _error(error: bank.ConversationError) -> HTTPException:
    return HTTPException(error.status_code, detail={"code": "CONVERSATION_ERROR", "message": error.message})


@router.post("")
async def create_conversation(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    return await bank.create(db, user["user_id"])


@router.get("")
async def list_conversations(response: Response, user: dict = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)) -> list[dict]:
    response.headers["Cache-Control"] = "no-store"
    return await bank.list_conversations(db, user["user_id"])


@router.get("/{session_id}")
async def get_conversation(session_id: str, response: Response, user: dict = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await bank.get(db, user["user_id"], session_id)
    except bank.ConversationError as error:
        raise _error(error) from error
