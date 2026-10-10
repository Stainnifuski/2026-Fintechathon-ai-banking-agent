"""登录用户的 NOVA 对话入口，不提供银行执行权限。"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..middleware.auth import get_current_user
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
    history: list[HistoryMessage] = Field(default_factory=list, max_length=12)

    @field_validator("message")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("消息不能为空")
        return value.strip()


@router.post("/chat")
async def chat(request: ChatRequest, current_user: dict = Depends(get_current_user)) -> dict:
    try:
        return await nova.chat(request.message, [m.model_dump() for m in request.history])
    except LLMError as error:
        raise HTTPException(status_code=503, detail={
            "code": error.code, "message": "NOVA 暂时无法完成查询，请稍后重试。"}) from None
