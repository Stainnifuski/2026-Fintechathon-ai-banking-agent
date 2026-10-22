"""Streaming-safe ASGI observations; business facts come from their owners."""
import asyncio
from time import monotonic
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.requests import ClientDisconnect
from ..services.audit.context import execution, bind_user
from ..services.audit.writer import AuditUnavailable, emit, persist_pending, health
from ..services.audit.policy import route_domain


class AuditMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        with execution(actor_type="anonymous") as ctx:
            from .auth import verify_token
            header = Headers(scope=scope).get("authorization", "")
            if header.startswith("Bearer "):
                payload = verify_token(header[7:])
                if payload and isinstance(payload.get("sub"), str):
                    bind_user(payload["sub"])
            start = monotonic()
            status, disconnected, complete = 500, False, False
            emit(action="http.request", phase="start", outcome="started", domain=route_domain(scope["path"]) or "system",
                 source_module="http.access", facts={"method": scope["method"]})
            try:
                await persist_pending(ctx)
            except AuditUnavailable:
                return await JSONResponse(status_code=503, content={"detail": {
                    "code": "AUDIT_UNAVAILABLE", "message": "操作记录暂不可用，请稍后重试"}})(scope, receive, send)

            async def audited_receive():
                nonlocal disconnected
                message = await receive()
                if message["type"] == "http.disconnect":
                    disconnected = True
                return message

            async def audited_send(message):
                nonlocal status, complete
                if message["type"] == "http.response.start":
                    status = message["status"]
                    headers = MutableHeaders(scope=message)
                    headers["X-Request-ID"] = ctx.request_id
                    headers["X-Audit-Status"] = "degraded" if ctx.degraded else health()["status"]
                    if scope["path"].startswith("/api/v1/audit"):
                        headers["Cache-Control"] = "no-store"
                if message["type"] == "http.response.body" and not message.get("more_body", False):
                    complete = True
                await send(message)

            try:
                await self.app(scope, audited_receive, audited_send)
            except asyncio.CancelledError:
                disconnected = True
                raise
            except (ClientDisconnect, OSError):
                disconnected = True
                ctx.error_code = "CLIENT_DISCONNECTED"
                raise
            except Exception as error:
                ctx.error_code = getattr(error, "code", "UNHANDLED_ERROR")
                raise
            finally:
                route = getattr(scope.get("route"), "path", "/api/unmatched")
                outcome = "disconnected" if disconnected else "completed" if complete else "interrupted"
                emit(action="http.response", phase="transport", outcome=outcome,
                     domain=route_domain(scope["path"]) or "system", source_module="http.access",
                     error_code=ctx.error_code, duration_ms=int((monotonic() - start) * 1000),
                     facts={"method": scope["method"], "route": route,
                            "http_status": status, "transport_status": outcome})
                # Dependencies have exited; never hold a SQLite business lock
                # while an independent audit connection tries to write.
                try:
                    await asyncio.shield(persist_pending(ctx))
                except AuditUnavailable:
                    # A transaction/response may already have committed. Health
                    # reports the outage without falsifying the business result.
                    pass


async def log_operation(session_id, user_id, intent, permission_level, action, params, result, decision_chain):
    """Compatibility entry point. Persistence failure reaches the caller."""
    from ..services.transaction_service import create_audit_log
    await create_audit_log(dict(session_id=session_id, user_id=user_id, intent=intent,
        permission_level=permission_level, action=action, params=params, result=result,
        decision_chain=decision_chain))
