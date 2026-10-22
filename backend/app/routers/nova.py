"""登录用户的 NOVA 对话入口，不提供银行执行权限。"""
import asyncio
import json
import logging
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..middleware.auth import get_current_user
from ..database.connection import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from ..schemas.nova import PageContext
from ..services.ai.nova_tools import ToolContext
from ..services.ai import nova
from ..services.ai.llm import LLMError
from ..services.ai.nlu import UnderstandingError
from ..services.ai.transfer_manager import (
    ManagerError, get_session, change_task_form, cancel_chat_task,
)
from ..models.nova_task import NovaSessionORM
from ..integrations.bank_api import BankApiError, get_bank_api
from ..services.orchestration import TaskError
from ..services.bank.transfer_service import BankError
from ..services.bank import nova_conversations as bank_conversations

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
    request_id: str | None = Field(default=None, min_length=8, max_length=64)
    session_id: str | None = Field(default=None, min_length=8, max_length=64)
    session_version: int = Field(default=0, ge=0)
    focus_task_id: str | None = Field(default=None, max_length=64)
    candidate_id: str | None = Field(default=None, max_length=64)
    wealth_task_id: str | None = Field(default=None,max_length=64)
    wealth_stage: str | None = Field(default=None,max_length=64,pattern=r'^[a-z_]+$')

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


class TaskCardAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=64)
    session_version: int = Field(ge=0)


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
    return request.model_copy(update={"session_id": conversation["session_id"],
                                      "request_id": request.request_id or uuid4().hex}), history, full_history


async def _finish(request: ChatRequest, user_id: str, db: AsyncSession, reply: dict) -> dict:
    from ..services.ai.wealth_agent import cards
    wealth_cards = await cards(db, user_id, request.session_id)
    if wealth_cards:
        reply = {**reply, "wealth_cards": wealth_cards}
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
            return {"session_id": session_id, "session_version": 0, "focus_task_id": None, "tasks": []}
        return await get_session(db, current_user["user_id"], session_id)
    except (ManagerError, bank_conversations.ConversationError) as error:
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
                                    bank_api=get_bank_api(),
                                    conversation_history=full_history,
                                ))
        return await _finish(request, current_user["user_id"], db, reply)
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
        bank_api=get_bank_api(),
        conversation_history=full_history,
    )

    async def run() -> None:
        try:
            result = await nova.chat(bank_conversations.redact(request.message), history, context=context, on_event=queue.put)
            result = await _finish(request, current_user["user_id"], db, result)
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
