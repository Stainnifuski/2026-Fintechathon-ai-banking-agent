"""4号理财内部HTTP边界；管理员能力不暴露给AI工具。"""

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db, DATABASE_URL
from ..domain.wealth import CALENDAR_VERSION
from ..integrations.wealth_authorization import WealthPolicyError
from ..schemas.wealth import StrictRequest, InvestmentCommand, InternalInvestmentSubmission
from ..services.bank import wealth_service as bank
from ..services.bank.transfer_service import BankError
from .bank_operations import require_internal_token

router = APIRouter(prefix="/api/v1/internal/bank/wealth", tags=["internal-wealth"],
                    dependencies=[Depends(require_internal_token)])


def fail(error: BankError | WealthPolicyError) -> None:
    """保留共享结构化领域码，不暴露输入或验证凭据。"""
    raise HTTPException(error.status_code, detail={"code": error.code, "message": error.message}) from error


class Subject(StrictRequest):
    subject_user_id: str = Field(max_length=32)


class Precheck(Subject):
    command: InvestmentCommand


class Verify(Subject):
    authorization_id: str = Field(max_length=64)
    proof: str = Field(pattern=r"^\d{6}$")


class Batch(Subject):
    submissions: list[InternalInvestmentSubmission] = Field(min_length=1, max_length=5)


class Advance(StrictRequest):
    business_at: datetime


def require_mock_admin() -> None:
    """业务时间和结算只可在显式启用的隔离模拟库使用。"""
    if (os.getenv("WEALTH_MOCK_ADMIN_ENABLED") != "true"
            or not any(marker in DATABASE_URL for marker in ("wealth_test_", "wealth-demo", "wealth_test"))):
        raise HTTPException(403, detail={"code": "MOCK_ADMIN_DISABLED", "message": "仅允许显式启用的隔离模拟库管理员"})


@router.get("/products")
async def products(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """内部客观产品读取。"""
    return await bank.products(db)


@router.post("/assets")
async def assets(request: Subject, db: AsyncSession = Depends(get_db)) -> dict:
    """内部本人银行资产事实。"""
    return await bank.assets(db, request.subject_user_id)


@router.post("/precheck")
async def precheck(request: Precheck, db: AsyncSession = Depends(get_db)) -> dict:
    """服务器重算估算和资格，不修改余额。"""
    try:
        return await bank.precheck(db, request.subject_user_id, request.command)
    except (BankError, WealthPolicyError) as error:
        fail(error)


@router.post("/verify")
async def verify(request: Verify, db: AsyncSession = Depends(get_db)) -> dict:
    """锁住本人验证码时间步，与逐笔强验证事实同事务提交。"""
    try:
        return await bank.verify_authorization(db, request.subject_user_id, request.authorization_id, request.proof)
    except (BankError, WealthPolicyError) as error:
        await db.rollback()
        fail(error)


@router.post("/orders")
async def submit(request: InternalInvestmentSubmission, db: AsyncSession = Depends(get_db)) -> dict:
    """银行独立核验强授权证据后受理。"""
    try:
        return (await bank.accept_many(db, request.subject_user_id, [(request.command, request.authorization_id)]))[0]
    except (BankError, WealthPolicyError) as error:
        await db.rollback()
        fail(error)


@router.post("/orders/batch")
async def batch(request: Batch, db: AsyncSession = Depends(get_db)) -> list[dict]:
    """同主体纯申购组合原子占用全部预算。"""
    if any(item.subject_user_id != request.subject_user_id or item.command.direction != "purchase" for item in request.submissions):
        raise HTTPException(422, detail={"code": "INVALID_PLAN", "message": "批量受理仅支持本人纯申购"})
    try:
        return await bank.accept_many(db, request.subject_user_id, [(item.command, item.authorization_id) for item in request.submissions])
    except (BankError, WealthPolicyError) as error:
        await db.rollback()
        fail(error)


@router.post("/orders/{operation_id}/query")
async def query(operation_id: str, request: Subject, db: AsyncSession = Depends(get_db)) -> dict:
    """原操作号查询，不因为404更换编号重发。"""
    try:
        return bank.order_view(await bank.owned_order(db, request.subject_user_id, operation_id))
    except BankError as error:
        fail(error)


@router.post("/orders/{operation_id}/cancel")
async def cancel(operation_id: str, request: Subject, db: AsyncSession = Depends(get_db)) -> dict:
    """窗口内明确撤单请求。"""
    try:
        return await bank.cancel(db, request.subject_user_id, operation_id)
    except BankError as error:
        await db.rollback()
        fail(error)


@router.post("/admin/clock", dependencies=[Depends(require_mock_admin)])
async def advance(request: Advance, db: AsyncSession = Depends(get_db)) -> dict:
    """受控业务时间只能向前，不延长安全有效期。"""
    try:
        return await bank.advance_clock(db, request.business_at)
    except BankError as error:
        await db.rollback()
        fail(error)


@router.post("/admin/settle/{operation_id}", dependencies=[Depends(require_mock_admin)])
async def settle(operation_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """隔离管理员结算，可重复执行且不追加重复账务。"""
    try:
        return await bank.settle(db, operation_id)
    except BankError as error:
        await db.rollback()
        fail(error)
