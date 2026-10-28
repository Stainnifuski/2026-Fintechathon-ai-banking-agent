"""Scheduled transfers and user-authored NOVA tasks; rules live in services.scheduled_tasks."""
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..middleware.auth import get_current_user
from ..services import scheduled_tasks as tasks
from ..services.scheduled_tasks import ScheduledTaskError

router = APIRouter(prefix="/api/v1/scheduled-tasks", tags=["scheduled-tasks"])


class CreateCustomTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=2000)
    scheduled_at: datetime
    # 可选：设定时即从该活期账户锁定这笔资金，任务结束前不能被其他付款花掉。
    lock_amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    lock_account_id: str | None = Field(default=None, min_length=1, max_length=32)

    @model_validator(mode="after")
    def lock_needs_account(self):
        if (self.lock_amount is None) != (self.lock_account_id is None):
            raise ValueError("锁定金额和锁定账户需同时填写")
        return self

    @field_validator("description")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请填写任务描述")
        return value

    @field_validator("scheduled_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("执行时间须包含时区")
        return value.astimezone(timezone.utc)


@router.get("")
async def list_scheduled_tasks(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await tasks.list_all(db, current_user["user_id"])


def _http(error: ScheduledTaskError) -> HTTPException:
    return HTTPException(error.status_code, detail={"code": error.code, "message": error.message})


@router.post("", status_code=201)
async def create_custom_task(body: CreateCustomTask, current_user: dict = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)):
    try:
        return await tasks.create_custom_task(
            db, user_id=current_user["user_id"], description=body.description, scheduled_at=body.scheduled_at,
            lock_amount_fen=int(body.lock_amount * 100) if body.lock_amount is not None else None,
            lock_account_id=body.lock_account_id)
    except ScheduledTaskError as error:
        raise _http(error) from error


@router.post("/{task_id}/finish")
async def finish_custom_task(task_id: str, current_user: dict = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)):
    try:
        return await tasks.end_custom_task(db, user_id=current_user["user_id"], task_id=task_id, finished=True)
    except ScheduledTaskError as error:
        raise _http(error) from error


@router.post("/{task_id}/cancel")
async def cancel_custom_task(task_id: str, current_user: dict = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)):
    try:
        return await tasks.end_custom_task(db, user_id=current_user["user_id"], task_id=task_id, finished=False)
    except ScheduledTaskError as error:
        raise _http(error) from error


@router.post("/{task_id}/start")
async def start_custom_task(task_id: str, current_user: dict = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)):
    try:
        return await tasks.start_custom_task(db, user_id=current_user["user_id"], task_id=task_id)
    except ScheduledTaskError as error:
        raise _http(error) from error
