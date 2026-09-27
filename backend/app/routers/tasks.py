"""5号任务状态机 API：所有用户转账均从这里进入。"""


from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.connection import get_db
from ..integrations.authorization import AuthorizationError, CapabilityNotImplemented
from ..integrations.bank_api import BankApi, BankApiError, get_bank_api
from ..middleware.auth import get_current_user
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
from ..services.ai.nlu import get_nlu, NLUEngine
from ..services.ai.conversation import process_message, read_conversation
from ..schemas.tasks import ChatReply


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
    bank: BankApi = Depends(get_bank_api),
) -> TaskSnapshot:
    try:
        result = await create_transfer_task(
            db, bank, user_id=current_user["user_id"], **request.model_dump()
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


@router.post("/messages", response_model=ChatReply, status_code=201)
async def create_from_message(
    request: ChatTaskCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    bank: BankApi = Depends(get_bank_api),
    nlu: NLUEngine = Depends(get_nlu),
) -> ChatReply:
    try:
        return ChatReply(**await process_message(db, bank, nlu, user_id=current_user["user_id"], request=request))
    except (TaskError, BankApiError, AuthorizationError) as error:
        await db.rollback()
        raise HTTPException(status_code=getattr(error, "status_code", 409),
            detail={"code": error.code, "message": error.message}) from error


@router.get("/conversations/{session_id}", response_model=ChatReply)
async def get_conversation(session_id: str, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return await read_conversation(db, session_id, current_user["user_id"])
    except TaskError as error:
        _raise_task_error(error)


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
    bank: BankApi = Depends(get_bank_api),
) -> TaskSnapshot:
    try:
        return TaskSnapshot(
            **await accept_task(
                db, bank, task_id=task_id, user_id=current_user["user_id"], **request.model_dump()
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
    bank: BankApi = Depends(get_bank_api),
) -> TaskSnapshot:
    try:
        return TaskSnapshot(
            **await verify_task(
                db,
                bank,
                task_id=task_id,
                user_id=current_user["user_id"],
                **request.model_dump(),
            )
        )
    except TaskError as error:
        await db.rollback()
        _raise_task_error(error)
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
    bank: BankApi = Depends(get_bank_api),
) -> TaskSnapshot:
    try:
        return TaskSnapshot(
            **await modify_task(
                db, bank, task_id=task_id, user_id=current_user["user_id"], **request.model_dump()
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
    bank: BankApi = Depends(get_bank_api),
) -> TaskSnapshot:
    try:
        return TaskSnapshot(
            **await cancel_task(
                db, bank, task_id=task_id, user_id=current_user["user_id"], **request.model_dump()
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
    bank: BankApi = Depends(get_bank_api),
) -> TaskSnapshot:
    try:
        return TaskSnapshot(
            **await reconcile_task(
                db, bank, task_id=task_id, user_id=current_user["user_id"], **request.model_dump()
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
