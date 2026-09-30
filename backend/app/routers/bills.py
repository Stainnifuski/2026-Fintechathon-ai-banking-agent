"""Green/read-only billing APIs. User identity always comes from authentication."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..middleware.auth import get_current_user
from ..schemas.bills import BillAnalysis, BillMessage, BillPage, BillQuery, BillReply
from ..services.bank.bill_service import analyze, transactions
from ..services.bank.transfer_service import BankError
from ..services.ai.bills import Clarification, understand

router = APIRouter(prefix="/api/v1/bills", tags=["bills"])


def bank_error(error):
    return HTTPException(status_code=error.status_code, detail={"code": error.code, "message": error.message})


@router.get("/analysis", response_model=BillAnalysis)
async def analysis(query: BillQuery = Depends(), current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return await analyze(db, current_user["user_id"], query)
    except BankError as error:
        raise bank_error(error) from error


@router.get("/transactions", response_model=BillPage)
async def details(query: BillQuery = Depends(), page: int = Query(1, ge=1),
                  page_size: int = Query(20, ge=1, le=100),
                  tx_ids: str | None = Query(None, max_length=200), as_of: datetime | None = None,
                  current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return await transactions(db, current_user["user_id"], query, page=page, page_size=page_size,
                                  tx_ids=tx_ids.split(",") if tx_ids else None, as_of=as_of)
    except BankError as error:
        raise bank_error(error) from error


@router.post("/messages", response_model=BillReply)
async def messages(request: BillMessage, current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        query, mode = await understand(request.message, request.context)
        result = await analyze(db, current_user["user_id"], query)
        return dict(kind="analysis", mode=mode, message="\n".join(result["report"]), analysis=result)
    except Clarification as error:
        return dict(kind="clarification", mode="offline", message=str(error))
    except BankError as error:
        raise bank_error(error) from error
