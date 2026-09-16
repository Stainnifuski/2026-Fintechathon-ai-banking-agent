"""对话管理器：多轮上下文 + 会话状态机。

本模块负责维护用户会话的完整生命周期，包括会话创建、
状态流转、槽位收集、确认处理和 MFA 验证等。
"""

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class DialogState(Enum):
    """对话状态枚举，表示会话状态机中的各个阶段。"""

    IDLE = "idle"                              # 空闲，等待用户输入
    COLLECTING_SLOTS = "collecting_slots"      # 收集槽位信息中
    WAITING_CONFIRMATION = "waiting_confirmation"  # 等待用户确认
    WAITING_MFA = "waiting_mfa"                # 等待 MFA 验证
    EXECUTING = "executing"                    # 执行操作中
    COMPLETED = "completed"                    # 已完成
    ERROR = "error"                            # 异常状态


class SessionContext(BaseModel):
    """会话上下文数据模型，保存单次会话的完整状态。"""

    session_id: str = Field(description="会话唯一标识")
    user_id: str = Field(description="用户 ID")
    state: DialogState = Field(default=DialogState.IDLE, description="当前对话状态")
    current_intent: str | None = Field(default=None, description="当前识别到的意图")
    slots: dict = Field(default_factory=dict, description="已收集的槽位信息")
    history: list[dict] = Field(default_factory=list, description="对话历史记录")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="会话创建时间",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="最近更新时间",
    )


# 各意图所需的必填槽位
_REQUIRED_SLOTS: dict[str, list[str]] = {
    "transfer": ["recipient", "amount"],
    "query_balance": [],
    "query_transactions": [],
}


class DialogManager:
    """对话管理器。

    维护多个并发会话，驱动对话状态机流转，
    协调 NLU 引擎和任务规划器完成用户请求。
    """

    def __init__(self) -> None:
        """初始化对话管理器，创建会话存储。"""
        self._sessions: dict[str, SessionContext] = {}

    async def create_session(self, user_id: str) -> SessionContext:
        """创建新的会话上下文。

        Args:
            user_id: 用户唯一标识。

        Returns:
            SessionContext: 新建的会话上下文对象。
        """
        session_id = f"S-{uuid.uuid4().hex[:12]}"
        session = SessionContext(session_id=session_id, user_id=user_id)
        self._sessions[session_id] = session
        return session

    async def get_session(self, session_id: str) -> SessionContext | None:
        """根据会话 ID 获取会话上下文。

        Args:
            session_id: 会话唯一标识。

        Returns:
            SessionContext | None: 会话上下文，不存在则返回 None。
        """
        return self._sessions.get(session_id)

    async def process_message(self, session_id: str, message: str) -> dict:
        """处理用户消息 — 对话管理的核心入口。

        根据当前会话状态，依次完成意图识别、槽位收集、
        权限判定、执行操作等流程，并返回响应。

        Args:
            session_id: 会话唯一标识。
            message: 用户发送的自然语言消息。

        Returns:
            dict: 响应字典，包含 type / content / action_required 等字段。
        """
        session = self._sessions.get(session_id)
        if session is None:
            return {"type": "error", "content": "会话不存在，请重新开始。"}

        # 记录用户消息到历史
        session.history.append({"role": "user", "content": message})
        session.updated_at = datetime.now(timezone.utc)

        # TODO: 实现具体逻辑
        # 1. 调用 NLUEngine 识别意图
        # 2. 根据意图和状态驱动状态机
        # 3. 如果槽位不完整 → 进入 COLLECTING_SLOTS 状态并追问
        # 4. 如果槽位完整 → 进入确认/MFA 流程
        # 5. 返回响应

        response = {
            "type": "text",
            "content": "您好，我是银行AI助手，请问有什么可以帮您？",
        }
        session.history.append({"role": "assistant", "content": response["content"]})
        return response

    async def update_state(
        self,
        session_id: str,
        new_state: DialogState,
    ) -> None:
        """更新指定会话的对话状态。

        Args:
            session_id: 会话唯一标识。
            new_state: 目标对话状态。
        """
        session = self._sessions.get(session_id)
        if session is not None:
            session.state = new_state
            session.updated_at = datetime.now(timezone.utc)

    async def handle_confirmation(
        self,
        session_id: str,
        confirmed: bool,
    ) -> dict:
        """处理用户对操作的确认或取消。

        Args:
            session_id: 会话唯一标识。
            confirmed: True 表示用户确认执行，False 表示用户取消。

        Returns:
            dict: 响应字典，包含确认后的执行结果或取消提示。
        """
        session = self._sessions.get(session_id)
        if session is None:
            return {"type": "error", "content": "会话不存在。"}

        # TODO: 实现具体逻辑 — 确认后调用 TaskPlanner 执行 DAG
        if confirmed:
            session.state = DialogState.EXECUTING
            session.updated_at = datetime.now(timezone.utc)
            # TODO: 执行操作
            session.state = DialogState.COMPLETED
            return {"type": "text", "content": "操作已执行成功。"}
        else:
            session.state = DialogState.IDLE
            session.slots = {}
            session.current_intent = None
            session.updated_at = datetime.now(timezone.utc)
            return {"type": "text", "content": "操作已取消。"}

    async def handle_mfa(
        self,
        session_id: str,
        mfa_code: str,
    ) -> dict:
        """处理 MFA 多因子验证。

        Args:
            session_id: 会话唯一标识。
            mfa_code: 用户输入的验证码。

        Returns:
            dict: 响应字典，包含验证结果和后续操作状态。
        """
        session = self._sessions.get(session_id)
        if session is None:
            return {"type": "error", "content": "会话不存在。"}

        # TODO: 实现具体逻辑 — 验证 MFA code 后执行操作
        session.updated_at = datetime.now(timezone.utc)
        session.state = DialogState.EXECUTING
        # TODO: 调用后端验证 MFA code
        session.state = DialogState.COMPLETED
        return {"type": "text", "content": "验证通过，操作已执行成功。"}

    def _check_missing_slots(self, intent: str, slots: dict) -> list[str]:
        """检查当前意图下缺失的必填槽位。

        Args:
            intent: 当前意图类型。
            slots: 已收集到的槽位键值对。

        Returns:
            list[str]: 缺失的槽位名称列表，为空表示槽位已完整。
        """
        required = _REQUIRED_SLOTS.get(intent, [])
        return [s for s in required if not slots.get(s)]
