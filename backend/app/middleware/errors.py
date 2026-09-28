"""统一HTTP错误外形，保留业务码及认证响应头，不回显请求凭据。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


def install_error_handlers(app: FastAPI) -> None:
    """注册稳定的detail.code/message及无输入值的校验明细。"""
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        default_code = {400: "BAD_REQUEST", 401: "AUTH_REQUIRED", 403: "FORBIDDEN",
                        404: "NOT_FOUND", 409: "CONFLICT", 422: "VALIDATION_ERROR",
                        503: "SERVICE_UNAVAILABLE"}.get(error.status_code, "HTTP_ERROR")
        detail = error.detail if isinstance(error.detail, dict) else {"message": str(error.detail)}
        return JSONResponse(status_code=error.status_code, headers=error.headers, content={"detail": {
            "code": detail.get("code", default_code), "message": detail.get("message", "请求失败"),
            "issues": detail.get("issues", []),
        }})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": {
            "code": "VALIDATION_ERROR", "message": "请求参数无效",
            "issues": [{"loc": list(item["loc"]), "type": item["type"], "message": item["msg"]}
                       for item in error.errors()],
        }})
