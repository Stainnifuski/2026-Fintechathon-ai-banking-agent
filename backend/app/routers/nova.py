"""登录用户的 NOVA 对话入口，不提供银行执行权限。"""
import asyncio
import json
import logging
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


@router.post("/chat")
async def chat(request: ChatRequest, current_user: dict = Depends(get_current_user),
               db: AsyncSession = Depends(get_db)) -> dict:
    try:
        return await nova.chat(request.message, [m.model_dump() for m in request.history],
                               context=ToolContext(current_user["user_id"], db, request.page_context))
    except LLMError as error:
        raise HTTPException(status_code=503, detail=_error_detail(error)) from None


def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, current_user: dict = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    """与 /chat 同一套工具与安全规则，以 SSE 推送进度：tool、delta、reset，最后 done 或 error。

    认证与参数错误仍在开始推送前以 HTTP 状态码返回；done 的内容与 /chat 的响应体相同。
    """
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    context = ToolContext(current_user["user_id"], db, request.page_context)
    history = [m.model_dump() for m in request.history]

    async def run() -> None:
        try:
            result = await nova.chat(request.message, history, context=context, on_event=queue.put)
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
