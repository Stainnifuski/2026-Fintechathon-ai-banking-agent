"""Unified read view for scheduled transfers and user-authored NOVA tasks."""
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..middleware.auth import get_current_user
from ..models.scheduled_task import ScheduledTaskORM
from ..models.workflow import TransferPlanORM, TransferTaskORM
from ..domain.time import as_utc
from ..services.bank import nova_conversations as chats

router = APIRouter(prefix="/api/v1/scheduled-tasks", tags=["scheduled-tasks"])


class CreateCustomTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=2000)
    scheduled_at: datetime

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


def custom_view(task: ScheduledTaskORM, now: datetime) -> dict:
    scheduled_at = as_utc(task.scheduled_at, legacy_utc=True)
    return {"task_id": task.task_id, "task_type": "custom", "description": chats._decrypt(task.description_ciphertext),
            "scheduled_at": scheduled_at, "status": task.status if task.status != "SCHEDULED" else
            "READY" if scheduled_at <= now else "SCHEDULED", "can_start": task.status == "SCHEDULED" and scheduled_at <= now,
            "completed": task.status == "STARTED",
            "dispatch_request_id": task.dispatch_request_id if task.status == "STARTED" else None,
            "started_at": as_utc(task.started_at, legacy_utc=True) if task.started_at else None}


def transfer_view(task: TransferTaskORM, plan: TransferPlanORM) -> dict:
    params = plan.params or {}
    amount = params.get("amount_fen")
    recipient = params.get("recipient_name") or "收款账户"
    return {"task_id": task.task_id, "task_type": "transfer",
            "description": f"向{recipient}定时转账" + (f" ¥{amount / 100:,.2f}" if isinstance(amount, int) else ""),
            "scheduled_at": datetime.fromisoformat(params["scheduled_at"]),
            "status": task.phase if task.phase != "FINISHED" else task.outcome,
            "completed": task.phase == "FINISHED",
            "can_start": False, "dispatch_request_id": None, "started_at": None}


@router.get("")
async def list_scheduled_tasks(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user_id = current_user["user_id"]
    now = datetime.now(timezone.utc)
    custom = (await db.scalars(select(ScheduledTaskORM).where(ScheduledTaskORM.user_id == user_id))).all()
    transfers = (await db.execute(select(TransferTaskORM, TransferPlanORM).join(
        TransferPlanORM, TransferPlanORM.task_id == TransferTaskORM.task_id).where(
        TransferTaskORM.user_id == user_id,
        TransferPlanORM.plan_version == TransferTaskORM.current_plan_version,
        TransferPlanORM.is_current.is_(True)))).all()
    items = [custom_view(task, now) for task in custom]
    items.extend(transfer_view(task, plan) for task, plan in transfers if (plan.params or {}).get("scheduled_at"))
    items.sort(key=lambda item: (item["completed"], item["scheduled_at"], item["task_id"]))
    return {"items": items, "as_of": now}


@router.post("", status_code=201)
async def create_custom_task(body: CreateCustomTask, current_user: dict = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    if body.scheduled_at <= now:
        raise HTTPException(422, detail={"message": "执行时间必须晚于当前时间"})
    task = ScheduledTaskORM(task_id="SCT" + uuid4().hex[:32], user_id=current_user["user_id"],
                            description_ciphertext=chats._encrypt(body.description), scheduled_at=body.scheduled_at,
                            status="SCHEDULED", dispatch_request_id=str(uuid4()))
    db.add(task)
    await db.commit()
    return custom_view(task, now)


@router.post("/{task_id}/start")
async def start_custom_task(task_id: str, current_user: dict = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    result = await db.execute(update(ScheduledTaskORM).where(
        ScheduledTaskORM.task_id == task_id, ScheduledTaskORM.user_id == current_user["user_id"],
        ScheduledTaskORM.status == "SCHEDULED", ScheduledTaskORM.scheduled_at <= now,
    ).values(status="STARTED", started_at=now))
    if result.rowcount != 1:
        row = await db.get(ScheduledTaskORM, task_id, populate_existing=True)
        if row is None or row.user_id != current_user["user_id"]:
            raise HTTPException(404, detail={"message": "定时任务不存在"})
        if row.status != "STARTED":
            raise HTTPException(409, detail={"message": "尚未到执行时间"})
    await db.commit()
    row = await db.get(ScheduledTaskORM, task_id, populate_existing=True)
    return custom_view(row, now)
