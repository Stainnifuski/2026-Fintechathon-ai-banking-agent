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
from ..services.ai.nlu import get_nlu, UnderstandingError
from ..services.ai.transfer_manager import (
    ManagerError, MODIFY, STATUS_QUERY, get_session, process_message, wants_transfer,
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

    @field_validator("message")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("消息不能为空")
        return value.strip()


def _error_detail(error: LLMError) -> dict:
    message = "NOVA 暂时无法完成查询，请稍后重试。"
    if error.code == "MODEL_OUTPUT_INCOMPLETE":
        message = ("模型回答超出输出长度限制，未能完整生成。请尝试分项查询。"
                   if error.finish_reason == "length"
                   else "模型回答被中断，未能完整生成，请稍后重试。")
    return {"code": error.code, "message": message}


async def _transfer_reply(request: ChatRequest, user_id: str, db: AsyncSession) -> dict | None:
    row = await db.get(NovaSessionORM, request.session_id) if request.session_id else None
    if row is not None and row.user_id != user_id:
        raise ManagerError("聊天任务会话不存在", "SESSION_NOT_FOUND", 404)
    pending = bool(row and any(task.get("status") in {"COLLECTING", "NEEDS_SELECTION"}
                               for task in row.state.get("tasks", [])))
    has_tasks = bool(row and row.state.get("tasks"))
    explicit_followup = has_tasks and (MODIFY.search(request.message) or request.message.strip() in
                                       {"确认", "执行", "确认转账", "取消", "算了", "状态", "进度"} or
                                       STATUS_QUERY.search(request.message))
    if not request.candidate_id and not wants_transfer(request.message, pending) and not explicit_followup:
        return None
    if not request.request_id:
        raise ManagerError("转账消息需要 request_id 以防重复生成任务", "REQUEST_ID_REQUIRED", 422)
    return await process_message(db, get_bank_api(), get_nlu(), user_id=user_id,
                                 message=request.message, request_id=request.request_id,
                                 session_id=request.session_id,
                                 session_version=request.session_version,
                                 focus_task_id=request.focus_task_id,
                                 candidate_id=request.candidate_id, create_session=True)


async def _prepare(request: ChatRequest, user_id: str, db: AsyncSession) -> tuple[ChatRequest, list[dict]]:
    if request.session_id:
        conversation = await bank_conversations.get(db, user_id, request.session_id)
    else:
        created = await bank_conversations.create(db, user_id)
        conversation = {**created, "messages": []}
    return request.model_copy(update={"session_id": conversation["session_id"],
                                      "request_id": request.request_id or uuid4().hex}), conversation["messages"][-12:]


async def _finish(request: ChatRequest, user_id: str, db: AsyncSession, reply: dict) -> dict:
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


@router.post("/chat")
async def chat(request: ChatRequest, current_user: dict = Depends(get_current_user),
               db: AsyncSession = Depends(get_db)) -> dict:
    try:
        request, history = await _prepare(request, current_user["user_id"], db)
        transfer = await _transfer_reply(request, current_user["user_id"], db)
        if transfer is not None:
            return await _finish(request, current_user["user_id"], db, transfer)
        reply = await nova.chat(bank_conversations.redact(request.message), history,
                                context=ToolContext(current_user["user_id"], db, request.page_context))
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
    """与 /chat 同一套工具与安全规则，以 SSE 推送进度：tool、delta、reset，最后 done 或 error。

    认证与参数错误仍在开始推送前以 HTTP 状态码返回；done 的内容与 /chat 的响应体相同。
    """
    try:
        request, history = await _prepare(request, current_user["user_id"], db)
        transfer = await _transfer_reply(request, current_user["user_id"], db)
        if transfer is not None:
            transfer = await _finish(request, current_user["user_id"], db, transfer)
    except (ManagerError, UnderstandingError, TaskError, BankApiError, BankError,
            bank_conversations.ConversationError) as error:
        await db.rollback()
        raise _transfer_error(error) from error
    if transfer is not None:
        async def one_event():
            yield _sse({"type": "done", **jsonable_encoder(transfer)})
        return StreamingResponse(one_event(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    context = ToolContext(current_user["user_id"], db, request.page_context)

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
