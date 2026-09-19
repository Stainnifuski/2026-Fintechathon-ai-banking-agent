"""受认证保护的银行转账及交易查询接口。"""

from fastapi import APIRouter, Depends, HTTPException, Query

from ..middleware.auth import get_current_user
from ..models.transaction import TransactionSchema
from ..services.bank import get_transaction, list_transactions

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])


@router.get("/history", response_model=list[TransactionSchema])
async def query_history(
    limit: int = Query(50, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
) -> list[TransactionSchema]:
    records = await list_transactions(current_user["user_id"], limit)
    return [TransactionSchema(**record) for record in records]


@router.get("/{tx_id}", response_model=TransactionSchema)
async def query_transaction(
    tx_id: str, current_user: dict = Depends(get_current_user)
) -> TransactionSchema:
    result = await get_transaction(current_user["user_id"], tx_id)
    if result is None:
        raise HTTPException(status_code=404, detail="未找到该交易记录")
    return TransactionSchema(**result)
