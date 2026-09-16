"""交易路由"""

from fastapi import APIRouter, HTTPException, Query

from ..models.transaction import (
    TransactionSchema,
    TransferRequestSchema,
    TransferResponseSchema,
)
from ..services.transaction_service import (
    execute_transfer,
    get_transaction_detail,
    get_transaction_history,
)

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])


@router.post("/transfer", response_model=TransferResponseSchema)
async def transfer(request: TransferRequestSchema) -> TransferResponseSchema:
    """执行转账操作。"""

    result = await execute_transfer(
        from_account=request.from_account,
        to_contact=request.to_contact,
        amount=request.amount,
        note=request.note,
        channel="agent",
    )

    return TransferResponseSchema(**result)


@router.get("/{user_id}/history", response_model=list[TransactionSchema])
async def query_history(
    user_id: str,
    start_date: str | None = Query(None, description="起始日期 YYYY-MM-DD"),
    end_date: str | None = Query(None, description="结束日期 YYYY-MM-DD"),
    limit: int = Query(50, ge=1, le=500, description="返回记录数量"),
) -> list[TransactionSchema]:
    """查询用户交易记录。"""

    records = await get_transaction_history(
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )

    return [TransactionSchema(**r) for r in records]


@router.get("/{tx_id}", response_model=TransactionSchema)
async def query_transaction(tx_id: str) -> TransactionSchema:
    """查询单笔交易详情。"""

    result = await get_transaction_detail(tx_id)
    if result is None:
        raise HTTPException(status_code=404, detail="未找到该交易记录")

    return TransactionSchema(**result)
