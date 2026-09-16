"""审计日志中间件"""

import logging
from datetime import datetime
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..services.transaction_service import create_audit_log

logger = logging.getLogger(__name__)


class AuditMiddleware(BaseHTTPMiddleware):
    """HTTP 请求审计中间件，记录所有 API 调用。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        """拦截请求，记录审计信息。"""
        # 仅对业务 API 路径记录审计
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        start_time = datetime.now()
        response = await call_next(request)
        duration_ms = (datetime.now() - start_time).total_seconds() * 1000

        # 异步记录审计日志（不阻塞响应）
        try:
            logger.info(
                "API调用: %s %s -> %s (%.1fms)",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
        except Exception as e:
            logger.error("审计日志记录失败: %s", str(e))

        return response


async def log_operation(
    session_id: str,
    user_id: str,
    intent: str,
    permission_level: str,
    action: str,
    params: dict,
    result: str,
    decision_chain: list[str],
) -> None:
    """记录业务操作审计日志。

    所有黄色和红色操作必须调用此函数。

    Args:
        session_id: 会话ID。
        user_id: 用户ID。
        intent: 识别到的意图。
        permission_level: 权限级别 (green/yellow/red)。
        action: 执行的操作名。
        params: 操作参数。
        result: 操作结果 (success/failed/cancelled/blocked)。
        decision_chain: 完整决策链路。
    """
    try:
        audit_data = {
            "session_id": session_id,
            "user_id": user_id,
            "intent": intent,
            "permission_level": permission_level,
            "action": action,
            "params": params,
            "result": result,
            "decision_chain": decision_chain,
        }
        audit_id = await create_audit_log(audit_data)
        logger.info("审计日志已记录: %s", audit_id)
    except Exception as e:
        logger.error("审计日志写入失败: %s", str(e))
