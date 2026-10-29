"""登录用户的 NOVA 对话入口，不提供银行执行权限。"""
import asyncio
import json
import logging
from datetime import datetime
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse, Response, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..middleware.auth import get_current_user
from ..database.connection import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from ..schemas.nova import PageContext
from ..services.ai.nova_tools import ToolContext
from ..services.ai import nova
from ..services.ai import debug_trace
from ..services.ai.llm import LLMError
from ..services.ai.nlu import UnderstandingError
from ..services.ai.transfer_manager import (
    ManagerError, get_session, change_task_form, cancel_chat_task,
)
from ..services.ai.collection_manager import CollectionError, refresh_collection, submit_collection
from ..services.ai.calendar_tools import update_chat_calendar_task
from ..services.ai.calendar_mutation import confirm_calendar_task, ManipulateCalendarArguments
from ..services.ai.shop_tools import update_chat_shop_task
from ..services.ai.scheduled_task_tools import update_chat_scheduled_task
from ..services.ai.card_subscription_tools import (
    CardTaskError, dismiss_chat_card_task, link_chat_card_operation, update_chat_subscription_task,
)
from ..integrations.card_authorization import CardAuthorizationError
from ..models.nova_task import NovaSessionORM
from ..integrations.bank_api import BankApiError, get_bank_api
from ..services.orchestration import TaskError
from ..services.bank.transfer_service import BankError
from ..services.bank import nova_conversations as bank_conversations
from ..services.bank import nova_reference_context

router = APIRouter(prefix="/api/v1/nova", tags=["nova"])
logger = logging.getLogger(__name__)


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    page_context: PageContext = Field(default_factory=PageContext)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=12)
    request_id: str | None = Field(default=None, min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    session_id: str | None = Field(default=None, min_length=8, max_length=64)
    session_version: int = Field(default=0, ge=0)
    focus_task_id: str | None = Field(default=None, max_length=64)
    candidate_id: str | None = Field(default=None, max_length=64)
    wealth_task_id: str | None = Field(default=None,max_length=64)
    wealth_stage: str | None = Field(default=None,max_length=64,pattern=r'^[a-z_]+$')
    task_switch_token: str | None = Field(default=None,min_length=32,max_length=32)
    accept_task_switch: bool | None = None
    debug: bool = False

    @field_validator("message")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("消息不能为空")
        return value.strip()


class TaskFormRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=64)
    session_version: int = Field(ge=0)
    recipient: str = Field(default="", max_length=100)
    amount_yuan: str = Field(default="", max_length=30)
    phone: str | None = Field(default=None, max_length=32)
    card: str | None = Field(default=None, max_length=32)
    clear_phone: bool = False
    clear_card: bool = False
    payer_account_id: str = Field(default="", max_length=64)
    note: str = Field(default="", max_length=256)
    execution_mode: Literal["instant", "scheduled"] | None = None
    scheduled_at: datetime | None = None

    @field_validator("scheduled_at")
    @classmethod
    def schedule_has_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("定时执行时间须包含时区")
        return value


class TaskCardAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=64)
    session_version: int = Field(ge=0)


class CollectionSubmit(BaseModel):
    session_version: int = Field(ge=0)
    payer_user_ids: list[str] = Field(min_length=1, max_length=20)
    receive_account_id: str = Field(min_length=1, max_length=32)
    amount: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    split_equally: bool = False
    note: str = Field(default="", max_length=256)


class CollectionRefresh(BaseModel):
    session_version: int = Field(ge=0)


class SubscriptionTaskSelect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscription_id: str = Field(min_length=1, max_length=40)


class ScheduledCardLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1, max_length=40)


class ShopTaskLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shop: Literal["flower", "cake"]
    order_id: str = Field(min_length=1, max_length=40)


class CalendarTaskLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=40)


class CalendarTaskConfirm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    changes: dict[str, str] = Field(default_factory=dict)

    @field_validator('changes')
    @classmethod
    def valid_changes(cls, value):
        allowed = {'title', 'start_date', 'start_time', 'end_date', 'end_time', 'location', 'description'}
        if set(value) - allowed:
            raise ValueError('包含不支持的日程字段')
        ManipulateCalendarArguments(action='add', **value)
        return value


class CardTaskLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=8, max_length=64)


def _error_detail(error: LLMError) -> dict:
    message = "NOVA 暂时无法完成查询，请稍后重试。"
    if error.code in {"AGENT_NO_PROGRESS", "AGENT_CONSECUTIVE_FAILURES"}:
        message = "工具调用连续失败或没有进展，本轮处理已停止。请核对需求后重试；已受理交易可继续查询。"
    if error.code == "MODEL_OUTPUT_INCOMPLETE":
        message = ("模型回答超出输出长度限制，未能完整生成。请尝试分项查询。"
                   if error.finish_reason == "length"
                   else "模型回答被中断，未能完整生成，请稍后重试。")
    return {"code": error.code, "message": message}


async def _prepare(
    request: ChatRequest, user_id: str, db: AsyncSession,
) -> tuple[ChatRequest, list[dict], list[dict]]:
    request = request.model_copy(update={'message':bank_conversations.redact_proof(request.message)})
    if request.session_id:
        conversation = await bank_conversations.get(db, user_id, request.session_id)
    else:
        created = await bank_conversations.create(db, user_id)
        conversation = {**created, "messages": []}
    full_history = [
        {"role": item["role"], "content": item["content"]}
        for item in conversation["messages"]
    ]
    history = full_history[-12:]
    if request.wealth_task_id:
        from ..services.ai.wealth_agent import cards
        owned=await cards(db,user_id,conversation['session_id'])
        if request.wealth_task_id not in {c['task_id'] for c in owned}:
            raise bank_conversations.ConversationError('理财补充对象不属于此对话',404)
    # 已开始的自定义定时任务首条消息所在对话，可支用该任务锁定的资金。
    from ..services.ai.scheduled_funding import bind_dispatch_session
    await bind_dispatch_session(db, user_id, request.request_id, conversation["session_id"])
    return request.model_copy(update={"session_id": conversation["session_id"],
                                      "request_id": request.request_id or uuid4().hex}), history, full_history


async def _finish(request: ChatRequest, user_id: str, db: AsyncSession, reply: dict) -> dict:
    if await db.get(NovaSessionORM,request.session_id):
        reply={**reply,**await get_session(db,user_id,request.session_id)}
    from ..services.ai import execution_queue
    queue=await execution_queue.load(ToolContext(user_id,db,request.page_context,session_id=request.session_id))
    if queue:reply={**reply,'execution_queue':execution_queue.public(queue)}
    from ..services.bank import nova_reference_context
    await nova_reference_context.save(db,user_id,request.session_id,reply.get('tool_calls',[]))
    from ..services.ai.wealth_agent import cards
    wealth_cards = await cards(db, user_id, request.session_id)
    if wealth_cards:
        reply = {**reply, "wealth_cards": wealth_cards}
    from ..services.bank import nova_bill_results
    reply = {**reply, "bill_result_cards": await nova_bill_results.save(
        db, user_id, request.session_id, request.request_id, reply)}
    await bank_conversations.append_turn(db, user_id, request.session_id,
                                         request.request_id, request.message, reply["reply"])
    return {**reply, "session_id": request.session_id}


def _transfer_error(error: Exception) -> HTTPException:
    return HTTPException(status_code=getattr(error, "status_code", 409),
                         detail={"code": getattr(error, "code", "TRANSFER_TASK_ERROR"),
                                 "message": getattr(error, "message", str(error))})


@router.get("/task-sessions/{session_id}")
async def task_session(session_id: str, current_user: dict = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await bank_conversations.get(db, current_user["user_id"], session_id)
        if await db.get(NovaSessionORM, session_id) is None:
            return {"session_id": session_id, "session_version": 0, "focus_task_id": None,
                    "tasks": [], "collection_cards": [], "card_operations": [], "subscription_tasks": [], "calendar_tasks": [], "shop_tasks": [], "payment_request_tasks": [], "scheduled_tasks": []}
        return await get_session(db, current_user["user_id"], session_id)
    except (ManagerError, bank_conversations.ConversationError) as error:
        raise _transfer_error(error) from error


@router.post("/task-sessions/{session_id}/collections/{collection_id}/refresh")
async def refresh_collection_card(session_id: str, collection_id: str, request: CollectionRefresh,
                                  current_user: dict = Depends(get_current_user),
                                  db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await bank_conversations.get(db, current_user["user_id"], session_id)
        return await refresh_collection(db, user_id=current_user["user_id"],
                                        session_id=session_id, collection_id=collection_id,
                                        session_version=request.session_version)
    except (CollectionError, bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error


@router.post("/task-sessions/{session_id}/collections/{collection_id}/send")
async def send_collection_card(session_id: str, collection_id: str, request: CollectionSubmit,
                               current_user: dict = Depends(get_current_user),
                               db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await bank_conversations.get(db, current_user["user_id"], session_id)
        return await submit_collection(db, user_id=current_user["user_id"],
                                       session_id=session_id, collection_id=collection_id,
                                       **request.model_dump())
    except (CollectionError, BankError, bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error


@router.post("/task-sessions/{session_id}/card-tasks/{entry_id}/operation")
async def link_card_task(session_id: str, entry_id: str, request: CardTaskLink,
                         current_user: dict = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)) -> dict:
    """把本人在聊天表单中生成的卡片预览挂到对应任务卡；确认仍走卡片授权接口。"""
    try:
        await bank_conversations.get(db, current_user["user_id"], session_id)
        return {"card_operations": await link_chat_card_operation(
            db, user_id=current_user["user_id"], session_id=session_id,
            entry_id=entry_id, operation_id=request.operation_id)}
    except (CardTaskError, CardAuthorizationError, bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error


@router.post("/task-sessions/{session_id}/card-tasks/{entry_id}/dismiss")
async def dismiss_card_task(session_id: str, entry_id: str,
                            current_user: dict = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await bank_conversations.get(db, current_user["user_id"], session_id)
        return {"card_operations": await dismiss_chat_card_task(
            db, user_id=current_user["user_id"], session_id=session_id, entry_id=entry_id)}
    except (CardTaskError, CardAuthorizationError, bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error


@router.post("/task-sessions/{session_id}/subscription-tasks/{entry_id}/select")
async def select_subscription_task(session_id: str, entry_id: str, request: SubscriptionTaskSelect,
                                   current_user: dict = Depends(get_current_user),
                                   db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await bank_conversations.get(db, current_user["user_id"], session_id)
        return {"subscription_tasks": await update_chat_subscription_task(
            db, user_id=current_user["user_id"], session_id=session_id, entry_id=entry_id,
            subscription_id=request.subscription_id)}
    except (CardTaskError, bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error


@router.post("/task-sessions/{session_id}/subscription-tasks/{entry_id}/dismiss")
async def dismiss_subscription_task(session_id: str, entry_id: str,
                                    current_user: dict = Depends(get_current_user),
                                    db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await bank_conversations.get(db, current_user["user_id"], session_id)
        return {"subscription_tasks": await update_chat_subscription_task(
            db, user_id=current_user["user_id"], session_id=session_id, entry_id=entry_id, close=True)}
    except (CardTaskError, bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error


@router.post("/task-sessions/{session_id}/calendar-tasks/{entry_id}/event")
async def link_calendar_task(session_id: str, entry_id: str, request: CalendarTaskLink,
                             current_user: dict = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)) -> dict:
    """本人在聊天日程表单中创建日程后，把它挂到对应任务卡。"""
    try:
        await bank_conversations.get(db, current_user["user_id"], session_id)
        return {"calendar_tasks": await update_chat_calendar_task(
            db, user_id=current_user["user_id"], session_id=session_id, entry_id=entry_id,
            event_id=request.event_id)}
    except (CardTaskError, bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error


@router.post("/task-sessions/{session_id}/calendar-tasks/{entry_id}/dismiss")
async def dismiss_calendar_task(session_id: str, entry_id: str,
                                current_user: dict = Depends(get_current_user),
                                db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await bank_conversations.get(db, current_user["user_id"], session_id)
        return {"calendar_tasks": await update_chat_calendar_task(
            db, user_id=current_user["user_id"], session_id=session_id, entry_id=entry_id, close=True)}
    except (CardTaskError, bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error


@router.post('/task-sessions/{session_id}/calendar-tasks/{entry_id}/confirm')
async def confirm_calendar_mutation(session_id: str, entry_id: str, request: CalendarTaskConfirm,
                                    current_user: dict = Depends(get_current_user),
                                    db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await bank_conversations.get(db, current_user['user_id'], session_id)
        return {'calendar_tasks': await confirm_calendar_task(db, user_id=current_user['user_id'],
                session_id=session_id, entry_id=entry_id, changes=request.changes)}
    except (CardTaskError, bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error


@router.post("/task-sessions/{session_id}/shop-tasks/{entry_id}/order")
async def link_shop_task(session_id: str, entry_id: str, request: ShopTaskLink,
                         current_user: dict = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)) -> dict:
    """本人在聊天订单表单中下单后挂到任务卡；付款仍走银行付款申请与任务确认接口。"""
    try:
        await bank_conversations.get(db, current_user["user_id"], session_id)
        return {"shop_tasks": await update_chat_shop_task(
            db, user_id=current_user["user_id"], session_id=session_id, entry_id=entry_id,
            shop=request.shop, order_id=request.order_id)}
    except (CardTaskError, bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error


@router.post("/task-sessions/{session_id}/shop-tasks/{entry_id}/dismiss")
async def dismiss_shop_task(session_id: str, entry_id: str,
                            current_user: dict = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await bank_conversations.get(db, current_user["user_id"], session_id)
        return {"shop_tasks": await update_chat_shop_task(
            db, user_id=current_user["user_id"], session_id=session_id, entry_id=entry_id, close=True)}
    except (CardTaskError, bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error


class PresentationAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(min_length=3, max_length=110)
    mode: Literal['manual', 'cancel']
    request_id: str = Field(min_length=8, max_length=64)
    draft: dict = Field(default_factory=dict)


@router.get('/presentation/{session_id}')
async def presentation_state(session_id: str, current_user: dict = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)):
    from ..services.ai.task_presentation import inventory
    try:
        return JSONResponse(await inventory(ToolContext(current_user['user_id'], db, PageContext(), session_id=session_id)),headers={'Cache-Control':'no-store'})
    except bank_conversations.ConversationError as error:
        raise _transfer_error(error) from error


class PresentationPosition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(min_length=3,max_length=110)
    request_id: str = Field(min_length=8,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    existing: bool = False


@router.post('/presentation/{session_id}/position')
async def presentation_position(session_id: str, request: PresentationPosition,
                                current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from ..services.ai.task_presentation import position
    try:
        return await position(ToolContext(current_user['user_id'],db,PageContext(),session_id=session_id),
                              request.key,request.request_id,request.existing)
    except bank_conversations.ConversationError as error:
        await db.rollback()
        raise _transfer_error(error) from error


class PresentationDraft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    key: str=Field(min_length=3,max_length=110)
    draft: dict=Field(default_factory=dict)


@router.post('/presentation/{session_id}/draft')
async def presentation_draft(session_id:str,request:PresentationDraft,
                             current_user:dict=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    from ..services.ai.task_presentation import save_draft
    try:
        return await save_draft(ToolContext(current_user['user_id'],db,PageContext(),session_id=session_id),request.key,request.draft)
    except bank_conversations.ConversationError as error:
        await db.rollback()
        raise _transfer_error(error) from error


class ManualCandidate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task_id: str = Field(min_length=1,max_length=64)
    candidate_id: str = Field(min_length=1,max_length=64)


@router.post('/presentation/{session_id}/candidate')
async def manual_candidate(session_id: str, request: ManualCandidate,
                           current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from ..services.ai.task_presentation import released
    from ..services.ai.transfer_manager import process_message
    from ..models.nova_task import NovaSessionORM
    from types import SimpleNamespace
    ctx=ToolContext(current_user['user_id'],db,PageContext(),session_id=session_id)
    receipt=(await released(ctx)).get('transfer:'+request.task_id)
    if not receipt or receipt['mode']!='manual':
        raise HTTPException(409,detail={'message':'请先人工接管这笔任务。'})
    row=await db.get(NovaSessionORM,session_id,populate_existing=True)
    task=next((t for t in row.state.get('tasks',[]) if t['chat_task_id']==request.task_id),None)
    if not task or not any(c['candidate_id']==request.candidate_id for c in task.get('candidate_map',[])):
        raise HTTPException(409,detail={'message':'候选已变化，请重新核对。'})
    try:
        return await process_message(db,get_bank_api(),SimpleNamespace(mode='offline'),user_id=current_user['user_id'],
            message='手动选择收款账户',request_id=uuid4().hex,session_id=session_id,
            session_version=row.version,focus_task_id=request.task_id,candidate_id=request.candidate_id)
    except ManagerError as error:
        await db.rollback()
        raise _transfer_error(error) from error


@router.post('/presentation/{session_id}/release')
async def presentation_release(session_id: str, request: PresentationAction,
                               current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from ..services.ai.task_presentation import release
    try:
        return await release(ToolContext(current_user['user_id'], db, PageContext(), session_id=session_id),
                             request.key, request.mode, request.request_id, request.draft)
    except Exception as error:
        await db.rollback()
        if isinstance(error, bank_conversations.ConversationError):
            raise _transfer_error(error) from error
        raise HTTPException(409, detail={'message': '当前任务无法安全接管或结束，请先核对原卡片状态。'}) from error


@router.post("/task-sessions/{session_id}/scheduled-tasks/{entry_id}/task")
async def link_scheduled_card(session_id: str, entry_id: str, request: ScheduledCardLink,
                              current_user: dict = Depends(get_current_user),
                              db: AsyncSession = Depends(get_db)) -> dict:
    """本人在聊天定时任务表单中保存任务后，挂到对应任务卡。"""
    try:
        await bank_conversations.get(db, current_user["user_id"], session_id)
        return {"scheduled_tasks": await update_chat_scheduled_task(
            db, user_id=current_user["user_id"], session_id=session_id, entry_id=entry_id, task_id=request.task_id)}
    except (CardTaskError, bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error


@router.post("/task-sessions/{session_id}/scheduled-tasks/{entry_id}/dismiss")
async def dismiss_scheduled_card(session_id: str, entry_id: str,
                                 current_user: dict = Depends(get_current_user),
                                 db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await bank_conversations.get(db, current_user["user_id"], session_id)
        return {"scheduled_tasks": await update_chat_scheduled_task(
            db, user_id=current_user["user_id"], session_id=session_id, entry_id=entry_id, close=True)}
    except (CardTaskError, bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error


@router.get('/task-flow/{session_id}')
async def task_flow_state(session_id: str, current_user: dict = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    from ..services.ai.task_flow import public
    try:
        return await public(ToolContext(current_user['user_id'],db,PageContext(),session_id=session_id))
    except bank_conversations.ConversationError as error:
        raise _transfer_error(error) from error


@router.post("/task-sessions/{session_id}/tasks/{task_id}/form")
async def save_task_form(session_id: str, task_id: str, request: TaskFormRequest,
                         current_user: dict = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await bank_conversations.get(db, current_user["user_id"], session_id)
        return await change_task_form(db, get_bank_api(), user_id=current_user["user_id"],
                                      session_id=session_id, chat_task_id=task_id, **request.model_dump())
    except (ManagerError, TaskError, BankApiError, BankError, bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error


@router.post("/task-sessions/{session_id}/tasks/{task_id}/cancel")
async def cancel_task_card(session_id: str, task_id: str, request: TaskCardAction,
                           current_user: dict = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await bank_conversations.get(db, current_user["user_id"], session_id)
        return await cancel_chat_task(db, get_bank_api(), user_id=current_user["user_id"],
                                      session_id=session_id, chat_task_id=task_id, **request.model_dump())
    except (ManagerError, TaskError, BankApiError, BankError, bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error


@router.post("/chat")
async def chat(request: ChatRequest, current_user: dict = Depends(get_current_user),
               db: AsyncSession = Depends(get_db)) -> dict:
    try:
        request, history, full_history = await _prepare(request, current_user["user_id"], db)
        with debug_trace.capture(current_user['user_id'], request.request_id, request.debug, request.session_id):
            debug_trace.record('user_request', {'message': request.message, 'page_context': request.page_context.model_dump(), 'history': history})
            reply = await nova.chat(bank_conversations.redact(request.message), history,
                                context=ToolContext(
                                    current_user["user_id"], db, request.page_context,
                                    original_message=request.message,
                                    request_id=request.request_id,
                                    session_id=request.session_id,
                                    session_version=request.session_version,
                                    focus_task_id=request.focus_task_id,
                                    candidate_id=request.candidate_id,
                                    wealth_task_id=request.wealth_task_id,wealth_stage=request.wealth_stage,
                                    task_switch_token=request.task_switch_token,accept_task_switch=request.accept_task_switch,
                                    reference_results=await nova_reference_context.load(db,current_user['user_id'],request.session_id),
                                    bank_api=get_bank_api(),
                                    conversation_history=full_history,
                                ))
            reply = await _finish(request, current_user["user_id"], db, reply)
            debug_trace.record('final_response', reply)
            return reply
    except LLMError as error:
        raise HTTPException(status_code=503, detail=_error_detail(error)) from None
    except (ManagerError, UnderstandingError, TaskError, BankApiError, BankError,
            bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error


def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, current_user: dict = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    """与 /chat 同一套工具与事实规则；先tool进度，核验后delta正文，最后done/error。

    认证与参数错误仍在开始推送前以 HTTP 状态码返回；done 的内容与 /chat 的响应体相同。
    """
    try:
        request, history, full_history = await _prepare(request, current_user["user_id"], db)
    except (ManagerError, UnderstandingError, TaskError, BankApiError, BankError,
            bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    context = ToolContext(
        current_user["user_id"], db, request.page_context,
        original_message=request.message,
        request_id=request.request_id,
        session_id=request.session_id,
        session_version=request.session_version,
        focus_task_id=request.focus_task_id,
        candidate_id=request.candidate_id,
        wealth_task_id=request.wealth_task_id,wealth_stage=request.wealth_stage,
        task_switch_token=request.task_switch_token,accept_task_switch=request.accept_task_switch,
        reference_results=await nova_reference_context.load(db,current_user['user_id'],request.session_id),
        bank_api=get_bank_api(),
        conversation_history=full_history,
    )

    async def run() -> None:
        try:
            with debug_trace.capture(current_user['user_id'], request.request_id, request.debug, request.session_id):
                debug_trace.record('user_request', {'message': request.message, 'page_context': request.page_context.model_dump(), 'history': history})
                result = await nova.chat(bank_conversations.redact(request.message), history, context=context, on_event=queue.put)
                result = await _finish(request, current_user["user_id"], db, result)
                debug_trace.record('final_response', result)
            await queue.put({"type": "done", **jsonable_encoder(result)})
        except LLMError as error:
            await queue.put({"type": "error", **_error_detail(error)})
        except Exception:
            logger.exception("NOVA 流式对话失败")
            await queue.put({"type": "error", "code": "NOVA_STREAM_FAILED",
                             "message": "NOVA 暂时无法完成查询，请稍后重试。"})
        finally:
            await queue.put(None)

    async def events():
        task = asyncio.create_task(run())
        try:
            while (event := await queue.get()) is not None:
                yield _sse(event)
        finally:
            # 客户端断开时停止后续模型调用和工具执行。
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get('/conversations/{session_id}/task-graphs')
async def task_graphs(session_id: str, current_user: dict = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)) -> list[dict]:
    from ..services.bank import nova_task_graphs
    from ..services.ai import task_graph_status
    try:
        graphs = await nova_task_graphs.graphs(db, current_user['user_id'], session_id)
        if not graphs:
            return []
        # Status follows the cards' live state, including cancellations made on the cards.
        states = await task_graph_status.card_states(ToolContext(current_user['user_id'], db, PageContext(),
                                                                 session_id=session_id))
        return [task_graph_status.derive(graph, states) for graph in graphs]
    except bank_conversations.ConversationError as error:
        raise HTTPException(error.status_code, detail={'code': 'CONVERSATION_NOT_FOUND', 'message': error.message}) from error


@router.get('/debug/{request_id}')
async def download_debug(request_id: str, current_user: dict = Depends(get_current_user)) -> Response:
    try:
        content = debug_trace.read(current_user['user_id'], request_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, detail={'code': 'DEBUG_TRACE_NOT_FOUND', 'message': '调试记录不存在'}) from None
    return Response(content, media_type='application/json', headers={
        'Content-Disposition': f'attachment; filename="nova-debug-{request_id}.json"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/debug/{request_id}/view')
async def view_debug(request_id: str, current_user: dict = Depends(get_current_user)) -> Response:
    try:
        content = debug_trace.read(current_user['user_id'], request_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, detail={'code': 'DEBUG_TRACE_NOT_FOUND', 'message': '调试记录不存在'}) from None
    return Response(content, media_type='application/json', headers={
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/debug/conversations/{session_id}')
async def list_debug_for_conversation(session_id: str,
                                      current_user: dict = Depends(get_current_user),
                                      db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await bank_conversations.get(db, current_user['user_id'], session_id)
    except bank_conversations.ConversationError as error:
        raise _transfer_error(error) from error
    return {'traces': debug_trace.list_recent(current_user['user_id'], session_id)}


class WealthCardState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1)
    execution_plan_id: str | None = Field(default=None, min_length=1, max_length=64)
    closed: bool = False
    paused: bool | None = None
    reset_draft: bool = False
    backtrack_stage: Literal['match','cash_management','stable','growth','candidates','selected','budget','amounts','purchase_review','allocation','details','redemption_input','trade'] | None = None
    backtrack_token: str | None = Field(default=None,min_length=1,max_length=64)
    accept_backtrack: bool = False


class ExecutionAction(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    version:int=Field(ge=1)
    debug:bool=False
    debug_request_id:str|None=Field(default=None,min_length=8,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')


@router.get('/execution-queues/{session_id}')
async def execution_status(session_id:str,current_user:dict=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    from ..services.ai import execution_queue as queue
    try:
        row=await queue.load(ToolContext(current_user['user_id'],db,PageContext(),session_id=session_id))
        return queue.public(row)
    except bank_conversations.ConversationError as error:raise _transfer_error(error) from error


@router.post('/execution-queues/{session_id}/advance')
async def execution_advance(session_id:str,request:ExecutionAction,current_user:dict=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    from ..services.ai import execution_queue as queue
    try:
        context=ToolContext(current_user['user_id'],db,PageContext(),session_id=session_id,bank_api=get_bank_api())
        row=await queue.load(context)
        if not row or row.version!=request.version:raise bank_conversations.ConversationError('队列已更新，请刷新进度。',409)
        with debug_trace.capture(current_user['user_id'], request.debug_request_id or uuid4().hex,
                                 request.debug, session_id):
            debug_trace.record('queue_advance', {'session_id': session_id, 'version': request.version, 'queue': queue.public(row)})
            reply=await queue.advance(context)
            reply.pop('_execution_grounded',None)
            from ..services.ai.wealth_agent import cards
            reply['wealth_cards']=await cards(db,current_user['user_id'],session_id)
            if await db.get(NovaSessionORM,session_id):reply.update(await get_session(db,current_user['user_id'],session_id))
            from ..services.bank import nova_bill_results
            root_request = (reply.get('execution_queue') or {}).get('request_id')
            if root_request:
                reply['bill_result_cards'] = await nova_bill_results.save(db,current_user['user_id'],session_id,root_request,reply)
                await db.commit()
            debug_trace.record('final_response',reply)
            return reply
    except bank_conversations.ConversationError as error:raise _transfer_error(error) from error


@router.post('/execution-queues/{session_id}/stop')
async def execution_stop(session_id:str,request:ExecutionAction,current_user:dict=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    from ..services.ai import execution_queue as queue
    try:return await queue.stop(ToolContext(current_user['user_id'],db,PageContext(),session_id=session_id),request.version)
    except bank_conversations.ConversationError as error:raise _transfer_error(error) from error


@router.get("/wealth-sessions/{session_id}")
async def wealth_session(session_id: str, current_user: dict = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    from ..services.ai.wealth_agent import cards
    from ..integrations.wealth_authorization import WealthPolicyError
    try:
        return await cards(db, current_user['user_id'], session_id)
    except WealthPolicyError as error:
        raise _transfer_error(error) from error


@router.post("/wealth-sessions/{session_id}/cards/{task_id}")
async def wealth_card_state(session_id: str, task_id: str, request: WealthCardState,
                            current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from ..services.ai.wealth_agent import save_state
    from ..integrations.wealth_authorization import WealthPolicyError
    try:
        return await save_state(db, current_user['user_id'], session_id, task_id, request.version, request)
    except WealthPolicyError as error:
        await db.rollback()
        raise _transfer_error(error) from error


class WealthViewProduct(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    product_id: str = Field(min_length=1,max_length=120)
    product_version: int = Field(ge=1)
    name: str = Field(max_length=120)
    bucket: Literal['cash_management','stable','growth'] | None = None


class WealthViewContext(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: int = Field(ge=1)
    selection_kind: Literal['positions','products'] | None = None
    workflow: Literal['planning','target'] | None = None
    workflow_request_id: str | None = Field(default=None,max_length=128)
    search_mode: Literal['name','category'] | None = None
    name_query: str = Field(default='',max_length=120)
    search_category: str = Field(default='',max_length=120)
    search_request_id: str | None = Field(default=None,max_length=128)
    risk_answers: dict[str,int] = Field(default_factory=dict,max_length=9)
    visible_products: list[WealthViewProduct] = Field(default_factory=list,max_length=60)
    selected_products: list[WealthViewProduct] = Field(default_factory=list,max_length=160)
    cash_field: Literal['reserve','investment','total'] | None = None
    cash_yuan: str = Field(default='',max_length=24,pattern=r'^(?:\d+(?:\.\d{0,2})?)?$')
    amounts_yuan: dict[str,str] = Field(default_factory=dict,max_length=160)
    completed_stages: list[Literal['match','cash_management','stable','growth','candidates','selected','budget','amounts','purchase_review','allocation','details','redemption_input','trade']] = Field(default_factory=list,max_length=16)

    @field_validator('amounts_yuan')
    @classmethod
    def valid_amounts(cls,value):
        import re
        if any(len(k)>120 or not re.fullmatch(r'(?:\d{1,12}(?:\.\d{0,2})?)?',v) for k,v in value.items()):
            raise ValueError('金额输入格式不合法')
        return value

    @field_validator('risk_answers')
    @classmethod
    def valid_risk_answers(cls,value):
        from ..domain.wealth_risk import QUESTIONS
        ids={question[0] for question in QUESTIONS}
        if any(key not in ids or type(answer) is not int or not 0<=answer<=3
               for key,answer in value.items()):
            raise ValueError('风险问卷答案不合法')
        return value


@router.post('/wealth-sessions/{session_id}/cards/{task_id}/view')
async def wealth_card_view(session_id:str,task_id:str,request:WealthViewContext,
        current_user:dict=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    from sqlalchemy import update
    from ..models.wealth import WealthConversationORM
    from ..services.ai.wealth_agent import require_card
    from ..integrations.wealth_authorization import WealthPolicyError, lock_profile
    try:
        await lock_profile(db,current_user['user_id'])
        row=await require_card(db,current_user['user_id'],task_id)
        if row.data['nova_session_id']!=session_id or row.version!=request.version:
            raise WealthPolicyError('理财卡片已变化，请重新读取','STALE_WEALTH_CARD',409)
        # Non-authorizing visible context only; all product identities are re-read
        # from the bank if the submodel subsequently resolves an ordinal.
        saved=await db.execute(update(WealthConversationORM).where(WealthConversationORM.session_id==task_id,
            WealthConversationORM.version==request.version).values(data={**row.data,'view_context':request.model_dump(exclude={'version'})}))
        if saved.rowcount!=1:
            raise WealthPolicyError('理财卡片已变化，请重新读取','STALE_WEALTH_CARD',409)
        await db.commit()
        return {'saved':True}
    except WealthPolicyError as error:
        await db.rollback()
        raise _transfer_error(error) from error
