"""User-authored custom scheduled tasks, shared by the scheduled-tasks page and NOVA.

A task may lock cash in a current account when it is set up; the lock stays until the task
ends, and only payments from the task's NOVA conversation may spend it once started.
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.time import as_utc
from ..models.scheduled_task import ScheduledTaskFundingORM, ScheduledTaskORM
from ..models.workflow import TransferPlanORM, TransferTaskORM
from .bank import nova_conversations as chats
from .bank import scheduled_holds
from .bank.transfer_service import BankError

ENDED = {"FINISHED", "CANCELLED"}


class ScheduledTaskError(Exception):
    def __init__(self, message: str, code: str = "SCHEDULED_TASK_ERROR", status_code: int = 409):
        self.message, self.code, self.status_code = message, code, status_code
        super().__init__(message)


def custom_view(task: ScheduledTaskORM, now: datetime, lock: dict | None = None) -> dict:
    """无锁定资金的任务启动即完成；有锁定资金的任务启动后进行中，本人完成或取消后结束并退回剩余资金。"""
    scheduled_at = as_utc(task.scheduled_at, legacy_utc=True)
    running = task.status == "STARTED" and lock is not None
    status = ("READY" if scheduled_at <= now else "SCHEDULED") if task.status == "SCHEDULED" else \
        "RUNNING" if running else task.status
    return {"task_id": task.task_id, "task_type": "custom", "description": chats._decrypt(task.description_ciphertext),
            "scheduled_at": scheduled_at, "status": status,
            "can_start": task.status == "SCHEDULED" and scheduled_at <= now,
            "can_finish": running, "can_cancel": task.status == "SCHEDULED" or running,
            "completed": task.status in ENDED or (task.status == "STARTED" and lock is None),
            "dispatch_request_id": task.dispatch_request_id if task.status in {"STARTED", "FINISHED"} else None,
            "started_at": as_utc(task.started_at, legacy_utc=True) if task.started_at else None,
            "lock": lock}


def lock_view(holds: list[dict] | None) -> dict | None:
    if not holds:
        return None
    hold = holds[-1]
    return {key: hold[key] for key in ("account_id", "amount_fen", "used_fen", "remaining_fen", "returned_fen", "status")}


async def task_lock(db: AsyncSession, user_id: str, task_id: str) -> dict | None:
    holds = await scheduled_holds.holds_for(db, subject_user_id=user_id, purpose="custom_task", reference_ids=[task_id])
    return lock_view(holds.get(task_id))


async def owned_view(db: AsyncSession, user_id: str, task_id: str) -> dict | None:
    row = await db.get(ScheduledTaskORM, task_id, populate_existing=True)
    if row is None or row.user_id != user_id:
        return None
    return custom_view(row, datetime.now(timezone.utc), await task_lock(db, user_id, task_id))


async def create_custom_task(db: AsyncSession, *, user_id: str, description: str, scheduled_at: datetime,
                             lock_amount_fen: int | None = None, lock_account_id: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    description = description.strip()
    if not description:
        raise ScheduledTaskError("请填写任务描述", "DESCRIPTION_REQUIRED", 422)
    if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None:
        raise ScheduledTaskError("执行时间须包含时区", "SCHEDULE_TIMEZONE_REQUIRED", 422)
    scheduled_at = scheduled_at.astimezone(timezone.utc)
    if scheduled_at <= now:
        raise ScheduledTaskError("执行时间必须晚于当前时间", "SCHEDULE_TIME_IN_PAST", 422)
    if (lock_amount_fen is None) != (lock_account_id is None):
        raise ScheduledTaskError("锁定金额和锁定账户需同时填写", "LOCK_INCOMPLETE", 422)
    task = ScheduledTaskORM(task_id="SCT" + uuid4().hex[:32], user_id=user_id,
                            description_ciphertext=chats._encrypt(description), scheduled_at=scheduled_at,
                            status="SCHEDULED", dispatch_request_id=str(uuid4()))
    db.add(task)
    if lock_amount_fen is None:
        await db.commit()
        return custom_view(task, now)
    await db.flush()
    hold_id = "SCH" + task.task_id
    db.add(ScheduledTaskFundingORM(task_id=task.task_id, hold_id=hold_id, account_id=lock_account_id,
                                   amount_fen=lock_amount_fen))
    try:
        # 锁定与任务一起提交；可自由支配余额不足时任务不会保存。
        hold = await scheduled_holds.place_hold(db, hold_id=hold_id, subject_user_id=user_id,
                                                account_id=lock_account_id, amount_fen=lock_amount_fen,
                                                purpose="custom_task", reference_id=task.task_id)
    except BankError as error:
        await db.rollback()
        message = ("活期账户可自由支配的余额不足，定时任务设定失败" if error.code == "INSUFFICIENT_FUNDS"
                   else error.message)
        raise ScheduledTaskError(message, error.code, error.status_code) from error
    return custom_view(task, now, lock_view([hold]))


async def start_custom_task(db: AsyncSession, *, user_id: str, task_id: str) -> dict:
    now = datetime.now(timezone.utc)
    result = await db.execute(update(ScheduledTaskORM).where(
        ScheduledTaskORM.task_id == task_id, ScheduledTaskORM.user_id == user_id,
        ScheduledTaskORM.status == "SCHEDULED", ScheduledTaskORM.scheduled_at <= now,
    ).values(status="STARTED", started_at=now))
    if result.rowcount != 1:
        row = await db.get(ScheduledTaskORM, task_id, populate_existing=True)
        if row is None or row.user_id != user_id:
            raise ScheduledTaskError("定时任务不存在", "SCHEDULED_TASK_NOT_FOUND", 404)
        if row.status != "STARTED":
            raise ScheduledTaskError("尚未到执行时间", "SCHEDULE_NOT_DUE", 409)
    await db.commit()
    return await owned_view(db, user_id, task_id)


async def end_custom_task(db: AsyncSession, *, user_id: str, task_id: str, finished: bool) -> dict:
    now = datetime.now(timezone.utc)
    allowed = {"STARTED"} if finished else {"SCHEDULED", "STARTED"}
    row = await db.get(ScheduledTaskORM, task_id, with_for_update=True, populate_existing=True)
    if row is None or row.user_id != user_id:
        raise ScheduledTaskError("定时任务不存在", "SCHEDULED_TASK_NOT_FOUND", 404)
    funding = await db.get(ScheduledTaskFundingORM, task_id)
    if row.status in ENDED:
        return custom_view(row, now, await task_lock(db, user_id, task_id))
    if row.status not in allowed or (row.status == "STARTED" and funding is None):
        raise ScheduledTaskError("当前任务状态不能执行此操作", "SCHEDULED_TASK_STATE", 409)
    row.status = "FINISHED" if finished else "CANCELLED"
    # 结束任务即退回锁定资金中未用完的部分。
    released = await scheduled_holds.release_holds(db, subject_user_id=user_id, purpose="custom_task",
                                                   reference_id=task_id)
    return custom_view(row, now, await task_lock(db, user_id, task_id) or lock_view(released))


def transfer_view(task: TransferTaskORM, plan: TransferPlanORM) -> dict:
    params = plan.params or {}
    amount = params.get("amount_fen")
    recipient = params.get("recipient_name") or "收款账户"
    return {"task_id": task.task_id, "task_type": "transfer",
            "description": f"向{recipient}定时转账" + (f" ¥{amount / 100:,.2f}" if isinstance(amount, int) else ""),
            "scheduled_at": datetime.fromisoformat(params["scheduled_at"]),
            "status": task.phase if task.phase != "FINISHED" else task.outcome,
            "completed": task.phase == "FINISHED",
            "can_start": False, "can_finish": False, "can_cancel": False,
            "dispatch_request_id": None, "started_at": None}


async def list_all(db: AsyncSession, user_id: str) -> dict:
    now = datetime.now(timezone.utc)
    custom = (await db.scalars(select(ScheduledTaskORM).where(ScheduledTaskORM.user_id == user_id))).all()
    transfers = (await db.execute(select(TransferTaskORM, TransferPlanORM).join(
        TransferPlanORM, TransferPlanORM.task_id == TransferTaskORM.task_id).where(
        TransferTaskORM.user_id == user_id,
        TransferPlanORM.plan_version == TransferTaskORM.current_plan_version,
        TransferPlanORM.is_current.is_(True)))).all()
    custom_locks = await scheduled_holds.holds_for(db, subject_user_id=user_id, purpose="custom_task",
                                                   reference_ids=[task.task_id for task in custom])
    scheduled = [(task, plan) for task, plan in transfers if (plan.params or {}).get("scheduled_at")]
    transfer_locks = await scheduled_holds.holds_for(db, subject_user_id=user_id, purpose="scheduled_transfer",
                                                     reference_ids=[task.task_id for task, _ in scheduled])
    items = [custom_view(task, now, lock_view(custom_locks.get(task.task_id))) for task in custom]
    items.extend({**transfer_view(task, plan), "lock": lock_view(transfer_locks.get(task.task_id))}
                 for task, plan in scheduled)
    items.sort(key=lambda item: (item["completed"], item["scheduled_at"], item["task_id"]))
    return {"items": items, "as_of": now}
