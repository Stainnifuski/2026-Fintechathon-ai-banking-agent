"""Validated API observations without buffering streaming response bodies."""
import json
from time import monotonic
from fastapi.routing import APIRoute, request_response
from .context import current
from .policy import route_domain
from .privacy import clean, references
from .writer import emit


def result_outcome(data):
    if not isinstance(data, dict):
        return "request_completed"
    if isinstance(data.get("grounding"), dict) and data["grounding"].get("status") == "blocked":
        return "blocked"
    status = data.get("status", data.get("phase", ""))
    if data.get("error") or status in {"FAILED", "REJECTED"}:
        return "failed"
    if status in {"UNKNOWN", "RECONCILING", "SUBMITTING"}:
        return "unknown"
    if status in {"CANCELLED", "EXPIRED"}:
        return "cancelled"
    if status in {"WAITING_CONFIRMATION", "AWAITING_CONFIRMATION"}:
        return "pending_confirmation"
    if status in {"WAITING_VERIFICATION", "AWAITING_MFA"}:
        return "pending_verification"
    if status == "PARTIAL_COMPLETED":
        return "partial"
    if status in {"SUCCEEDED", "SETTLED", "COMPLETED"} or data.get("outcome") in {"SUCCESS", "SUCCEEDED"}:
        return "success"
    return "request_completed"


def api_routes(app):
    """FastAPI supports both eager routes and lazy included routers."""
    for route in app.routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from api_routes(route.original_router)


def install_routes(app):
    for route in api_routes(app):
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/") or getattr(route, "audit_installed", False):
            continue
        original_factory = route.get_route_handler
        domain = route_domain(route.path)
        if domain is None:
            raise ValueError("Unclassified audit route: " + route.path)

        def wrap(handler, api_route, audit_domain):
            async def audited(request):
                start = monotonic()
                emit(action=api_route.name, phase="request", outcome="started", domain=audit_domain,
                     source_module="http.api", facts={"method": request.method, "route": api_route.path})
                try:
                    response = await handler(request)
                except Exception as error:
                    detail = getattr(error, "detail", {})
                    code = detail.get("code") if isinstance(detail, dict) else None
                    code = code or getattr(error, "code", "VALIDATION_ERROR" if hasattr(error, "errors") else "REQUEST_FAILED")
                    ctx = current.get()
                    if ctx:
                        ctx.error_code = code
                    emit(action=api_route.name, phase="response", outcome="blocked" if getattr(error, "status_code", 0) in {401, 403, 429} else "failed",
                         domain=audit_domain, source_module="http.api", error_code=code,
                         objects=references(request.path_params, audit_domain),
                         facts={"route": api_route.path, "params": request.path_params,
                                "error_code": code, "http_status": getattr(error, "status_code", 500)},
                         duration_ms=int((monotonic() - start) * 1000))
                    raise
                ctx = current.get()
                body = getattr(request, "_json", None)
                if not isinstance(body, dict):
                    body = {}
                if ctx and ctx.actor_type == "service" and response.status_code < 400 and body.get("subject_user_id"):
                    ctx.subject_user_id = body["subject_user_id"]
                raw = getattr(response, "body", b"")
                data = None
                if raw and len(raw) <= 2 * 1024 * 1024 and "application/json" in response.headers.get("content-type", ""):
                    try:
                        data = json.loads(raw)
                    except ValueError:
                        pass
                facts = {"method": request.method, "route": api_route.path,
                         "params": clean({**request.path_params, **dict(request.query_params), **body}),
                         "http_status": response.status_code}
                if isinstance(data, (dict, list)):
                    # An audit query must not recursively copy its returned
                    # audit history into another audit event.
                    if audit_domain != "audit":
                        facts["result"] = clean(data)
                    facts["result_count"] = len(data) if isinstance(data, list) else len(data.get("items", [])) if isinstance(data.get("items"), list) else 1
                emit(action=api_route.name, phase="response", outcome=result_outcome(data), domain=audit_domain,
                     source_module="http.api", facts=facts,
                     objects=references(data, audit_domain) + references(request.path_params, audit_domain),
                     business_request_id=body.get("request_id") if isinstance(body.get("request_id"), str) else None,
                     duration_ms=int((monotonic() - start) * 1000))
                return response
            return audited
        # Lazy included routers build effective dependency/response handlers at
        # request time. Wrap the factory too, preserving that effective context.
        def factory(original=original_factory, api_route=route, audit_domain=domain):
            return wrap(original(), api_route, audit_domain)
        route.get_route_handler = factory
        route.app = request_response(factory())
        route.audit_installed = True
