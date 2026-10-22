"""Minimal in-process scheduler for one-time scheduled transfers."""
from ..audit.spans import audited

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...domain import TaskPhase
from ...integrations.bank_api import BankApi
from ...models.workflow import TransferPlanORM, TransferTaskORM
from .task_service import TaskError, accept_task

logger = logging.getLogger(__name__)


def _scheduled_time(plan: TransferPlanORM) -> datetime | None:
    raw_value = plan.params.get("scheduled_at")
    if not raw_value:
        return None
    try:
        value = datetime.fromisoformat(raw_value)
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


@audited("transfer", skip_empty=True)
async def execute_due_scheduled_transfers(
    db: AsyncSession,
    bank: BankApi,
    *,
    now: datetime | None = None,
) -> int:
    """Execute due tasks through the existing acceptance and bank pipeline."""
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    executed = 0

    task_ids = (
        await db.execute(
            select(TransferTaskORM.task_id).where(
                TransferTaskORM.phase == TaskPhase.WAITING_EXECUTION,
                TransferTaskORM.cancel_requested.is_(False),
            )
        )
    ).scalars().all()

    for task_id in task_ids:
        # A denied task rolls back its session. Load each subsequent task afresh
        # rather than implicitly reading expired ORM objects after that rollback.
        task = await db.get(TransferTaskORM, task_id, populate_existing=True)
        if task is None or task.phase != TaskPhase.WAITING_EXECUTION or task.cancel_requested:
            continue
        plan = (
            await db.execute(
                select(TransferPlanORM).where(
                    TransferPlanORM.task_id == task.task_id,
                    TransferPlanORM.plan_version == task.current_plan_version,
                    TransferPlanORM.is_current.is_(True),
                )
            )
        ).scalar_one_or_none()
        if task.error_code in {"SECURITY_LOCKED", "SECURITY_RECONFIRM_REQUIRED"}:
            continue
        execute_at = _scheduled_time(plan) if plan else None
        if execute_at is None or execute_at > current_time:
            continue

        try:
            await accept_task(
                db,
                bank,
                task_id=task.task_id,
                user_id=task.user_id,
                request_id=f"scheduled-{task.task_id}",
                state_version=task.state_version,
                execution_time=current_time,
            )
            executed += 1
        except TaskError as error:
            await db.rollback()
            if error.code in {"SECURITY_LOCKED", "SECURITY_RECONFIRM_REQUIRED"}:
                blocked = await db.get(TransferTaskORM, task_id)
                blocked.error_code = error.code
                await db.commit()
            logger.warning(
                "Scheduled transfer %s was not executed: %s (%s)",
                task_id,
                error.message,
                error.code,
            )
        except Exception:
            await db.rollback()
            logger.exception("Scheduled transfer %s failed unexpectedly", task_id)

    return executed


async def scheduled_transfer_loop(
    session_factory: async_sessionmaker[AsyncSession],
    bank: BankApi,
    *,
    interval_seconds: float = 2.0,
) -> None:
    """Scan due transfers while the FastAPI process is running."""
    while True:
        async with session_factory() as db:
            await execute_due_scheduled_transfers(db, bank)
        await asyncio.sleep(interval_seconds)
