"""受认证保护的银行转账及交易查询接口。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..middleware.auth import get_current_user
from ..models.transaction import TransactionSchema
from ..services.transaction_service import get_transaction_detail, get_transaction_history

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])


@router.get("/history", response_model=list[TransactionSchema])
async def query_history(limit: int = Query(50, ge=1, le=500), current_user: dict = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)) -> list[TransactionSchema]:
    return [TransactionSchema(**record) for record in await get_transaction_history(db, current_user["user_id"], limit)]


@router.get("/{tx_id}", response_model=TransactionSchema)
async def query_transaction(tx_id: str, current_user: dict = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)) -> TransactionSchema:
    result = await get_transaction_detail(db, current_user["user_id"], tx_id)
    if result is None:
        raise HTTPException(status_code=404, detail="未找到该交易记录")
    return TransactionSchema(**result)
