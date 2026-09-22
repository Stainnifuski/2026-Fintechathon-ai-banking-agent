"""持久化对话入口；移除旧内存会话与假成功逻辑。"""
from backend.app.services.ai.conversation import process_message, read_conversation

__all__ = ["process_message", "read_conversation"]
