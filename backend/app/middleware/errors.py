"""统一HTTP错误外形，保留业务码及认证响应头，不回显请求凭据。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


def install_error_handlers(app: FastAPI) -> None:
    """注册稳定的detail.code/message及无输入值的校验明细。"""
    from ..security.circuit_breaker import SecurityError

    @app.exception_handler(SecurityError)
    async def security_denied(request: Request, error: SecurityError) -> JSONResponse:
        detail = {"code": error.code, "message": error.message, "issues": [],
                  **getattr(error, "security_detail", {})}
        headers = {"Retry-After": str(detail["retry_after"])} if "retry_after" in detail else None
        return JSONResponse(status_code=error.status_code, content={"detail": detail}, headers=headers)

    from ..services.audit.writer import AuditUnavailable

    @app.exception_handler(AuditUnavailable)
    async def audit_unavailable(request: Request, error: AuditUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": {
            "code": "AUDIT_UNAVAILABLE", "message": "操作记录暂不可用，本次未提交的变更已停止，请稍后重试。"}})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        default_code = {400: "BAD_REQUEST", 401: "AUTH_REQUIRED", 403: "FORBIDDEN",
                        404: "NOT_FOUND", 409: "CONFLICT", 422: "VALIDATION_ERROR",
                        503: "SERVICE_UNAVAILABLE"}.get(error.status_code, "HTTP_ERROR")
        detail = error.detail if isinstance(error.detail, dict) else {"message": str(error.detail)}
        headers = dict(error.headers or {})
        if "retry_after" in detail:
            headers["Retry-After"] = str(detail["retry_after"])
        return JSONResponse(status_code=error.status_code, headers=headers, content={"detail": {
            "code": detail.get("code", default_code), "message": detail.get("message", "请求失败"),
            "issues": detail.get("issues", []),
            **{k: detail[k] for k in ("scope", "reason_code", "locked_until", "retry_after", "generation") if k in detail},
        }})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": {
            "code": "VALIDATION_ERROR", "message": "请求参数无效",
            "issues": [{"loc": list(item["loc"]), "type": item["type"], "message": item["msg"]}
                       for item in error.errors()],
        }})
