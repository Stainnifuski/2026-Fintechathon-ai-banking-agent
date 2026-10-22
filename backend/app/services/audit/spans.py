"""Explicit service spans for decisions, denied attempts, tools and workers."""
import asyncio
import functools
import inspect
import json
from dataclasses import replace, is_dataclass, asdict
from time import monotonic
from uuid import uuid4
from sqlalchemy.ext.asyncio import async_sessionmaker
from .context import AuditContext, current
from .privacy import clean, references
from .routes import result_outcome
from .writer import emit, persist_pending, add_to_session


def summary(value, depth=0):
    if depth > 12:
        return {"truncated": True}
    if hasattr(value, "model_dump"):
        return clean(value.model_dump(mode="json"))
    if is_dataclass(value):
        return clean(asdict(value))
    if isinstance(value, dict):
        return clean(value)
    if isinstance(value, (list, tuple)):
        return [summary(v, depth + 1) for v in value[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return clean(value)
    return {}


def audited(domain, *, action=None, skip_empty=False):
    def decorate(fn):
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapped(*args, **kwargs):
            bound = signature.bind_partial(*args, **kwargs).arguments
            db = bound.get("db", bound.get("session"))
            tool_context = bound.get("context")
            if db is None and hasattr(tool_context, "db"):
                db = tool_context.db
            user = bound.get("user_id", bound.get("subject_user_id", bound.get("owner_id", bound.get("user"))))
            if not isinstance(user, str):
                user = getattr(tool_context, "user_id", getattr(tool_context, "subject_user_id", None))
            parent = current.get()
            factory = fn.__globals__.get("AsyncSessionLocal")
            if parent is None and db is None and factory is None:
                return await fn(*args, **kwargs)
            params = {k: summary(v) for k, v in bound.items()
                      if k not in {"self", "db", "bank", "context", "client", "model", "proof", "password", "code", "phone", "card_number", "message", "text", "history"}}
            refs = references(params, domain)
            call = bound.get("call")
            if isinstance(call, dict) and isinstance(call.get("function"), dict):
                function = call["function"]
                params["tool"] = clean(function.get("name", "unknown"))
                try:
                    params["arguments"] = clean(json.loads(function.get("arguments", "{}")))
                except (TypeError, ValueError):
                    params["arguments"] = {"error_code": "INVALID_ARGUMENTS"}
            ctx = replace(parent, span_id=uuid4().hex, parent_span_id=parent.span_id,
                          objects=[*parent.objects, *refs]) if parent else AuditContext(
                              actor_type="service", actor_id="local-executor", subject_user_id=user, objects=refs)
            if ctx.subject_user_id is None and user:
                ctx.subject_user_id = user
            token = current.set(ctx)
            start = monotonic()
            name = action or fn.__name__
            source = fn.__module__.removeprefix("backend.app.")
            start_event = emit(action=name, phase="start", outcome="started", domain=domain, source_module=source,
                 facts={"params": params}, business_request_id=bound.get("request_id"))
            try:
                result = await fn(*args, **kwargs)
                if skip_empty and result in (None, 0) and any(d["event_id"] == start_event["event_id"] for d in ctx.pending):
                    ctx.pending[:] = [d for d in ctx.pending if d["event_id"] != start_event["event_id"]]
                    return result
                data = summary(result)
                result_error = data.get("error") if isinstance(data, dict) else None
                emit(action=name, phase="result", outcome=result_outcome(data), domain=domain,
                     source_module=source, facts={"result": data},
                     error_code=clean(result_error) if isinstance(result_error, str) else None,
                     duration_ms=int((monotonic() - start) * 1000), objects=references(data, domain))
                return result
            except BaseException as error:
                code = getattr(error, "code", "EXECUTION_INTERRUPTED" if isinstance(error, asyncio.CancelledError) else "EXECUTION_FAILED")
                unknown = type(error).__name__ in {"BankResponseUnknown", "BankResponseLost"}
                emit(action=name, phase="result", outcome="unknown" if unknown else "interrupted" if isinstance(error, asyncio.CancelledError) else "failed",
                     domain=domain, source_module=source, error_code=code,
                     facts={"error_code": code}, duration_ms=int((monotonic() - start) * 1000))
                raise
            finally:
                try:
                    if parent is None and ctx.pending:
                        if db is not None and db.in_transaction():
                            for data in list(ctx.pending):
                                add_to_session(db, data)
                                db.sync_session.info.setdefault("audit_observations", []).append(data)
                            ctx.pending.clear()
                        elif db is not None:
                            await persist_pending(ctx, factory=async_sessionmaker(db.bind, expire_on_commit=False))
                        elif factory is not None:
                            await persist_pending(ctx, factory=factory)
                finally:
                    current.reset(token)
        return wrapped
    return decorate
