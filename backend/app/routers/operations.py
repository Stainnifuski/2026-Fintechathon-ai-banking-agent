"""用户可见的应用操作查询接口。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..middleware.auth import get_current_user
from ..models.workflow import TransferOperationORM, TransferTaskORM
from ..schemas.tasks import OperationView
from ..domain import fen_to_yuan

router = APIRouter(prefix="/api/v1/operations", tags=["transfer-operations"])


@router.get("/{operation_id}", response_model=OperationView)
async def get_operation(
    operation_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OperationView:
    row = (
        await db.execute(
            select(TransferOperationORM, TransferTaskORM)
            .join(
                TransferTaskORM,
                TransferTaskORM.task_id == TransferOperationORM.task_id,
            )
            .where(TransferOperationORM.operation_id == operation_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="操作不存在")
    operation, task = row
    if task.user_id != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="无权查询该操作")
    return OperationView(
        operation_id=operation.operation_id,
        status=operation.status,
        amount=fen_to_yuan(operation.amount_fen),
        amount_fen=operation.amount_fen,
        currency=operation.currency,
        from_account=operation.from_account,
        to_account=operation.to_account,
        bank_status=operation.bank_status,
        bank_tx_id=operation.bank_tx_id,
        failure_code=operation.failure_code,
    )
