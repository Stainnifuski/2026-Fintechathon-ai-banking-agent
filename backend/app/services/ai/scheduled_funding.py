"""Tie payments made in a started custom task's NOVA conversation to that task's locked cash."""
from sqlalchemy import select

from ...models.scheduled_task import ScheduledTaskFundingORM, ScheduledTaskORM, ScheduledTransferFundingORM


async def bind_dispatch_session(db, user_id: str, request_id: str | None, session_id: str | None) -> None:
    """The started task's first NOVA message names the conversation that may spend its lock."""
    if not (request_id and session_id):
        return
    task = await db.scalar(select(ScheduledTaskORM).where(
        ScheduledTaskORM.user_id == user_id, ScheduledTaskORM.dispatch_request_id == request_id,
        ScheduledTaskORM.status == "STARTED"))
    if task is None:
        return
    funding = await db.get(ScheduledTaskFundingORM, task.task_id)
    if funding is not None and funding.session_id is None:
        funding.session_id = session_id
        await db.commit()


async def running_funding(db, user_id: str, session_id: str | None) -> ScheduledTaskFundingORM | None:
    if not session_id:
        return None
    return await db.scalar(select(ScheduledTaskFundingORM).join(
        ScheduledTaskORM, ScheduledTaskORM.task_id == ScheduledTaskFundingORM.task_id).where(
        ScheduledTaskFundingORM.session_id == session_id, ScheduledTaskORM.user_id == user_id,
        ScheduledTaskORM.status == "STARTED"))


async def fund_transfer_task(db, user_id: str, session_id: str | None, transfer_task_id: str) -> None:
    """Confirmation of this transfer will link it to the task's lock; caller commits."""
    funding = await running_funding(db, user_id, session_id)
    if funding is not None and await db.get(ScheduledTransferFundingORM, transfer_task_id) is None:
        db.add(ScheduledTransferFundingORM(transfer_task_id=transfer_task_id, custom_task_id=funding.task_id))


async def fund_payment_request(db, user_id: str, session_id: str | None, payment_request_id: str | None) -> None:
    """A shop order placed in the task's conversation is paid from the lock."""
    funding = await running_funding(db, user_id, session_id)
    if funding is None or not payment_request_id:
        return
    from ..bank.scheduled_holds import link_hold
    await link_hold(db, hold_id=funding.hold_id, subject_user_id=user_id,
                    link_key=f"payment_request:{payment_request_id}")
