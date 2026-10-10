"""登录用户的 NOVA 对话入口，不提供银行执行权限。"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..middleware.auth import get_current_user
from ..database.connection import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from ..schemas.nova import PageContext
from ..services.ai.nova_tools import ToolContext
from ..services.ai import nova
from ..services.ai.llm import LLMError

router = APIRouter(prefix="/api/v1/nova", tags=["nova"])


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


@router.post("/chat")
async def chat(request: ChatRequest, current_user: dict = Depends(get_current_user),
               db: AsyncSession = Depends(get_db)) -> dict:
    try:
        return await nova.chat(request.message, [m.model_dump() for m in request.history],
                               context=ToolContext(current_user["user_id"], db, request.page_context))
    except LLMError as error:
        message = "NOVA 暂时无法完成查询，请稍后重试。"
        if error.code == "MODEL_OUTPUT_INCOMPLETE":
            message = ("模型回答超出输出长度限制，未能完整生成。请尝试分项查询。"
                       if error.finish_reason == "length"
                       else "模型回答被中断，未能完整生成，请稍后重试。")
        raise HTTPException(status_code=503, detail={
            "code": error.code, "message": message}) from None
