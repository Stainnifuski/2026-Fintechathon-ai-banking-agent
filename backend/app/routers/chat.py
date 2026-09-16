"""聊天对话路由 - 核心接口"""

from datetime import datetime
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


# ──────────────────── 请求/响应 Schema ────────────────────


class ActionRequired(BaseModel):
    """操作确认/验证要求。"""

    type: Literal["confirm", "mfa", "select"]
    message: str
    options: list[str] | None = None


class ChatMessageRequest(BaseModel):
    """聊天消息请求。"""

    session_id: str | None = None
    user_id: str
    message: str
    channel: str = "web"
    device_id: str | None = None


class ChatMessageResponse(BaseModel):
    """聊天消息响应。"""

    session_id: str
    message_id: str
    type: Literal["text", "confirmation", "mfa_required", "chart", "error"]
    content: str
    action_required: ActionRequired | None = None
    timestamp: datetime


class ConfirmRequest(BaseModel):
    """用户确认/验证请求。"""

    session_id: str
    message_id: str
    action: Literal["confirm", "cancel"]
    mfa_code: str | None = None


class ConfirmResponse(BaseModel):
    """确认操作响应。"""

    session_id: str
    message_id: str
    status: str
    content: str
    timestamp: datetime


class ChatHistoryItem(BaseModel):
    """历史消息条目。"""

    message_id: str
    role: str
    content: str
    type: str
    timestamp: datetime


# ──────────────────── 路由 ────────────────────


@router.post("/message", response_model=ChatMessageResponse)
async def send_message(request: ChatMessageRequest) -> ChatMessageResponse:
    """接收用户聊天消息，返回 Agent 响应。"""

    # TODO: 调用 Agent 核心层处理用户消息
    # TODO: 意图识别 → 任务规划 → 权限判定 → 工具调用 → 生成回复
    # TODO: 支持 SSE 流式返回

    session_id = request.session_id or str(uuid4())
    message_id = str(uuid4())

    return ChatMessageResponse(
        session_id=session_id,
        message_id=message_id,
        type="text",
        content=f"收到您的消息：{request.message}（Agent 处理功能开发中）",
        action_required=None,
        timestamp=datetime.now(),
    )


@router.post("/confirm", response_model=ConfirmResponse)
async def confirm_action(request: ConfirmRequest) -> ConfirmResponse:
    """用户确认或取消操作（黄色/红色权限级别）。"""

    # TODO: 根据 session_id 和 message_id 找到待确认的操作
    # TODO: 如果 action == "confirm"，执行操作
    # TODO: 如果 action == "cancel"，取消操作
    # TODO: 如果需要 MFA，验证 mfa_code

    return ConfirmResponse(
        session_id=request.session_id,
        message_id=request.message_id,
        status="confirmed" if request.action == "confirm" else "cancelled",
        content="操作已确认" if request.action == "confirm" else "操作已取消",
        timestamp=datetime.now(),
    )


@router.get("/history", response_model=list[ChatHistoryItem])
async def get_chat_history(session_id: str, limit: int = 50) -> list[ChatHistoryItem]:
    """获取会话历史消息。"""

    # TODO: 从数据库/Redis 中查询会话历史
    # TODO: 按时间排序，返回最近 limit 条
    # TODO: 支持分页

    return []
