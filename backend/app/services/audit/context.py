"""Server-owned execution context. IDs correlate evidence; they grant no authority."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class AuditContext:
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    request_id: str = field(default_factory=lambda: uuid4().hex)
    span_id: str = field(default_factory=lambda: uuid4().hex)
    parent_span_id: str | None = None
    actor_id: str | None = None
    actor_type: str = "system"
    subject_user_id: str | None = None
    sequence: int = 0
    pending: list[dict] = field(default_factory=list)
    objects: list[dict] = field(default_factory=list)
    error_code: str | None = None
    business_outcome: str | None = None
    degraded: bool = False


current: ContextVar[AuditContext | None] = ContextVar("audit_context", default=None)


@contextmanager
def execution(*, actor_id=None, actor_type="system", subject_user_id=None, objects=()):
    ctx = AuditContext(actor_id=actor_id, actor_type=actor_type,
                       subject_user_id=subject_user_id, objects=list(objects))
    token = current.set(ctx)
    try:
        yield ctx
    finally:
        current.reset(token)


def bind_user(user_id: str) -> None:
    ctx = current.get()
    if ctx is not None:
        ctx.actor_id = user_id
        ctx.actor_type = "user"
        ctx.subject_user_id = user_id


def bind_subject(user_id: str, *, system_actor=None) -> None:
    """A worker learns its owner from the loaded task, never from an external header."""
    ctx = current.get()
    if ctx is None:
        return
    ctx.subject_user_id = user_id
    if system_actor and ctx.actor_id == "local-executor":
        ctx.actor_type, ctx.actor_id = "system", system_actor
    for data in ctx.pending:
        if data["span_id"] == ctx.span_id and data["subject_user_id"] is None:
            data.update(subject_user_id=user_id, actor_type=ctx.actor_type, actor_id=ctx.actor_id)


def propagation_headers() -> dict[str, str]:
    ctx = current.get()
    return {} if ctx is None else {
        "X-Audit-Trace": ctx.trace_id, "X-Audit-Parent": ctx.span_id,
    }


def trust_internal(headers) -> None:
    """Call only after the internal credential has been authenticated."""
    ctx = current.get()
    if ctx is None:
        return
    for header, attr in (("x-audit-trace", "trace_id"), ("x-audit-parent", "parent_span_id")):
        value = headers.get(header, "")
        if len(value) == 32 and all(c in "0123456789abcdef" for c in value):
            setattr(ctx, attr, value)
    ctx.actor_type, ctx.actor_id = "service", "bank-executor"
