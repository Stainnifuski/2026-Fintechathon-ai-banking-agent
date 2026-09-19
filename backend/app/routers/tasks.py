"""5号任务状态机 API：所有用户转账均从这里进入。"""

import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..integrations.authorization import AuthorizationError, CapabilityNotImplemented
from ..middleware.auth import get_current_user
from ..models.account import AccountORM
from ..models.user import UserORM
from ..schemas.tasks import (
    ChatTaskCreate,
    ConfirmTaskRequest,
    HumanActionRequest,
    ModifyTaskRequest,
    StateEventView,
    TaskActionRequest,
    TaskSnapshot,
    TransferTaskCreate,
    VerificationRequest,
)
from ..services.orchestration import (
    TaskError,
    accept_task,
    cancel_task,
    claim_task,
    confirm_task,
    create_transfer_task,
    modify_task,
    reconcile_task,
    return_task,
    task_events,
    task_snapshot,
    verify_task,
)

router = APIRouter(prefix="/api/v1/tasks", tags=["transfer-tasks"])
_transfer_pattern = re.compile(
    r"(?:转给|转账给)\s*([^\s，,]+)\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块)?"
)


def _raise_task_error(error: TaskError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


@router.post("/transfers", response_model=TaskSnapshot, status_code=status.HTTP_201_CREATED)
async def create_transfer(
    request: TransferTaskCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskSnapshot:
    try:
        result = await create_transfer_task(
            db, user_id=current_user["user_id"], **request.model_dump()
        )
        return TaskSnapshot(**result)
    except TaskError as error:
        await db.rollback()
        _raise_task_error(error)
    except AuthorizationError as error:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail={"code": error.code, "message": error.message}
        ) from error


@router.post("/messages", response_model=TaskSnapshot, status_code=status.HTTP_201_CREATED)
async def create_from_message(
    request: ChatTaskCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskSnapshot:
    match = _transfer_pattern.search(request.message.strip())
    if not match:
        raise HTTPException(
            status_code=422,
            detail={"code": "MESSAGE_NOT_UNDERSTOOD", "message": "当前支持“转给 李四 100元”"},
        )
    recipient_name, amount_text = match.groups()
    recipients = (
        await db.execute(
            select(UserORM, AccountORM)
            .join(AccountORM, AccountORM.user_id == UserORM.user_id)
            .where(
                UserORM.name == recipient_name,
                UserORM.user_id != current_user["user_id"],
                UserORM.status == "active",
                AccountORM.status == "active",
            )
        )
    ).all()
    if len(recipients) != 1:
        raise HTTPException(
            status_code=409,
            detail={"code": "RECIPIENT_AMBIGUOUS", "message": "未找到唯一收款人"},
        )
    sender = (
        await db.execute(
            select(AccountORM)
            .where(
                AccountORM.user_id == current_user["user_id"],
                AccountORM.status == "active",
            )
            .order_by(AccountORM.account_id)
        )
    ).scalars().first()
    if sender is None:
        raise HTTPException(status_code=404, detail="未找到可用付款账户")
    _, receiver = recipients[0]
    try:
        result = await create_transfer_task(
            db,
            user_id=current_user["user_id"],
            request_id=request.request_id,
            operation_id=request.operation_id,
            from_account=sender.account_id,
            to_account=receiver.account_id,
            amount=float(amount_text),
        )
        return TaskSnapshot(**result)
    except TaskError as error:
        await db.rollback()
        _raise_task_error(error)
    except AuthorizationError as error:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail={"code": error.code, "message": error.message}
        ) from error


@router.get("/{task_id}", response_model=TaskSnapshot)
async def get_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskSnapshot:
    try:
        return TaskSnapshot(
            **await task_snapshot(db, task_id, current_user["user_id"])
        )
    except TaskError as error:
        _raise_task_error(error)


@router.get("/{task_id}/events", response_model=list[StateEventView])
async def get_events(
    task_id: str,
    since_event_id: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[StateEventView]:
    try:
        return [
            StateEventView(**item)
            for item in await task_events(
                db, task_id, current_user["user_id"], since_event_id
            )
        ]
    except TaskError as error:
        _raise_task_error(error)


@router.post("/{task_id}/confirm", response_model=TaskSnapshot)
async def confirm(
    task_id: str,
    request: ConfirmTaskRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskSnapshot:
    try:
        return TaskSnapshot(
            **await confirm_task(
                db, task_id=task_id, user_id=current_user["user_id"], **request.model_dump()
            )
        )
    except (TaskError, AuthorizationError) as error:
        await db.rollback()
        if isinstance(error, TaskError):
            _raise_task_error(error)
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/{task_id}/accept", response_model=TaskSnapshot)
async def accept(
    task_id: str,
    request: TaskActionRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskSnapshot:
    try:
        return TaskSnapshot(
            **await accept_task(
                db, task_id=task_id, user_id=current_user["user_id"], **request.model_dump()
            )
        )
    except TaskError as error:
        await db.rollback()
        _raise_task_error(error)
    except AuthorizationError as error:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail={"code": error.code, "message": error.message}
        ) from error


@router.post("/{task_id}/verify", response_model=TaskSnapshot)
async def verify(
    task_id: str,
    request: VerificationRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskSnapshot:
    try:
        return TaskSnapshot(
            **await verify_task(
                db,
                task_id=task_id,
                user_id=current_user["user_id"],
                **request.model_dump(),
            )
        )
    except CapabilityNotImplemented as error:
        raise HTTPException(
            status_code=501,
            detail={"code": "NOT_IMPLEMENTED", "capability": error.capability},
        ) from error


@router.post("/{task_id}/modify", response_model=TaskSnapshot)
async def modify(
    task_id: str,
    request: ModifyTaskRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskSnapshot:
    try:
        return TaskSnapshot(
            **await modify_task(
                db, task_id=task_id, user_id=current_user["user_id"], **request.model_dump()
            )
        )
    except TaskError as error:
        await db.rollback()
        _raise_task_error(error)


@router.post("/{task_id}/cancel", response_model=TaskSnapshot)
async def cancel(
    task_id: str,
    request: TaskActionRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskSnapshot:
    try:
        return TaskSnapshot(
            **await cancel_task(
                db, task_id=task_id, user_id=current_user["user_id"], **request.model_dump()
            )
        )
    except TaskError as error:
        await db.rollback()
        _raise_task_error(error)


@router.post("/{task_id}/reconcile", response_model=TaskSnapshot)
async def reconcile(
    task_id: str,
    request: TaskActionRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskSnapshot:
    try:
        return TaskSnapshot(
            **await reconcile_task(
                db, task_id=task_id, user_id=current_user["user_id"], **request.model_dump()
            )
        )
    except TaskError as error:
        await db.rollback()
        _raise_task_error(error)


def _require_operator(current_user: dict) -> None:
    if current_user.get("role") not in {"operator", "admin"}:
        raise HTTPException(status_code=403, detail="需要人工处理员权限")


@router.post("/{task_id}/human/claim", response_model=TaskSnapshot)
async def claim(
    task_id: str,
    request: HumanActionRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskSnapshot:
    _require_operator(current_user)
    try:
        return TaskSnapshot(
            **await claim_task(db, task_id=task_id, **request.model_dump())
        )
    except TaskError as error:
        await db.rollback()
        _raise_task_error(error)


@router.post("/{task_id}/human/return", response_model=TaskSnapshot)
async def return_to_queue(
    task_id: str,
    request: HumanActionRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskSnapshot:
    _require_operator(current_user)
    try:
        return TaskSnapshot(
            **await return_task(
                db,
                task_id=task_id,
                operator_id=request.operator_id,
                request_id=request.request_id,
            )
        )
    except TaskError as error:
        await db.rollback()
        _raise_task_error(error)
