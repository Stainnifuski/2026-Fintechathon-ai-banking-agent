"""Server-owned lock decisions. Callers own successful business transactions.

Rejected attempts must roll back business work BEFORE persisting security state;
record_failure then commits only counters/audit (and an optional SMS attempt).
All final debit gates hold the same subject row until their ledger commit.
"""
import hashlib
import hmac
import math
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.security_state import SecurityStateORM
from ..models.audit_log import AuditLogORM
from ..services.bank.transfer_service import BankError


class SecurityError(BankError):
    """A deliberate security denial, never an unknown bank result."""



def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def setting(name: str, default: int) -> int:
    value = int(os.getenv("SECURITY_" + name, str(default)))
    if value < 1:
        raise ValueError("SECURITY_" + name + " must be positive")
    return value


# Independent scopes prevent unauthenticated password guessing from stopping payments.
def user_key(user_id: str) -> str:
    return "trade:" + user_id


def login_key(phone: str) -> str:
    secret = os.getenv("JWT_SECRET", "dev-secret-key-change-in-production").encode()
    return "login:" + hmac.new(secret, phone.encode(), hashlib.sha256).hexdigest()


def agent_key(user_id: str, session_id: str) -> str:
    return "agent:" + hashlib.sha256(f"{user_id}:{session_id}".encode()).hexdigest()


def audit(db: AsyncSession, row: SecurityStateORM, action: str, **facts) -> None:
    db.add(AuditLogORM(audit_id="SEC" + uuid4().hex, session_id="security",
        user_id=row.user_id, intent="security", permission_level="red", action=action,
        result="blocked" if action != "security_recovered" else "success",
        params={"scope": row.scope, "generation": row.generation, **facts},
        decision_chain=["server_identity", "persistent_subject_serialization", action]))


async def locked_row(db: AsyncSession, key: str, user_id: str, scope: str) -> SecurityStateORM:
    # INSERT serializes first use too. SQLite ignores SELECT FOR UPDATE, but this
    # write obtains its cross-connection writer lock before counters are read.
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
    # A repeated gate in one transaction must not refresh away its own recovery
    # or success counter update. Flush only this module's already-dirty rows.
    for pending in list(db.dirty):
        if isinstance(pending, SecurityStateORM) and pending.subject_key == key:
            await db.flush([pending])
    with db.no_autoflush:
        await db.execute(insert(SecurityStateORM).values(subject_key=key, user_id=user_id,
            scope=scope, counters={}, generation=0).on_conflict_do_nothing(index_elements=["subject_key"]))
        if db.get_bind().dialect.name == "sqlite":
            from sqlalchemy import update
            await db.execute(update(SecurityStateORM).where(SecurityStateORM.subject_key == key)
                .values(generation=SecurityStateORM.generation))
        row = (await db.scalars(select(SecurityStateORM).where(SecurityStateORM.subject_key == key)
            .with_for_update().execution_options(populate_existing=True))).one()
    if row.locked_until and aware(row.locked_until) <= now_utc():
        row.locked_until, row.counters = None, {}
        audit(db, row, "security_recovered", reason_code=row.reason_code)
    return row


def lock_detail(row: SecurityStateORM) -> dict:
    until = aware(row.locked_until)
    return {"scope": row.scope, "reason_code": row.reason_code,
            "locked_until": until.isoformat(),
            "retry_after": max(1, math.ceil((until - now_utc()).total_seconds())),
            "generation": row.generation}


def raise_locked(row: SecurityStateORM) -> None:
    detail = lock_detail(row)
    code = {"trade": "SECURITY_LOCKED", "login": "LOGIN_LOCKED", "agent": "AGENT_SESSION_LOCKED"}[row.scope]
    label = {"trade": "交易操作已安全锁定", "login": "登录暂时受限", "agent": "本会话智能操作已暂停"}[row.scope]
    error = SecurityError(f"{label}，请约{math.ceil(detail['retry_after'] / 60)}分钟后重试；"
                      "已受理交易仍可查询。", code, 429)
    error.security_detail = detail
    raise error


def ensure_unlocked(row: SecurityStateORM) -> None:
    if row.locked_until and aware(row.locked_until) > now_utc():
        raise_locked(row)


async def require_trade(db: AsyncSession, user_id: str, *, issued_at: datetime | str | None = None,
                        authorization: bool = False) -> SecurityStateORM:
    row = await locked_row(db, user_key(user_id), user_id, "trade")
    ensure_unlocked(row)
    check_authorization(row, issued_at, authorization)
    return row


def check_authorization(row: SecurityStateORM, issued_at: datetime | str | None, authorization: bool) -> None:
    if authorization and row.revoked_before:
        if isinstance(issued_at, str):
            try:
                issued_at = datetime.fromisoformat(issued_at)
            except ValueError:
                issued_at = None
        if issued_at is None or aware(issued_at) <= aware(row.revoked_before):
            raise SecurityError("安全锁定前的授权已失效，请重新生成操作并本人确认、验证。",
                                "SECURITY_RECONFIRM_REQUIRED", 409)


async def check_trade(db: AsyncSession, user_id: str, *, issued_at: datetime | str | None = None, authorization: bool = False) -> None:
    """Read-only early guard: never hold a security lock across an HTTP bank call."""
    with db.no_autoflush:
        row = await db.get(SecurityStateORM, user_key(user_id), populate_existing=True)
    if row:
        ensure_unlocked(row)
        check_authorization(row, issued_at, authorization)



def success(row: SecurityStateORM, category: str) -> None:
    counters = dict(row.counters)
    current = dict(counters.get(category, {}))
    current["consecutive"] = 0
    if category == "login":
        current["failures"] = []
    counters[category] = current
    row.counters = counters


async def record_failure(db: AsyncSession, *, key: str, user_id: str, scope: str,
                         category: str, reason: str, reference_id: str | None = None, method: str | None = None,
                         on_record: Callable[[], Awaitable[None]] | None = None) -> SecurityStateORM:
    """Only call after rollback; never commit unrelated application mutations."""
    row = await locked_row(db, key, user_id, scope)
    if row.locked_until and aware(row.locked_until) > now_utc():
        # Requests already in flight cannot extend the cooldown indefinitely.
        return row
    row.user_id = user_id
    window = setting("VERIFICATION_WINDOW_SECONDS", 600) if category == "mfa" else (
        setting("LOGIN_WINDOW_SECONDS", 600) if category == "login" else setting("SUSPICIOUS_WINDOW_SECONDS", 300))
    threshold = setting("VERIFICATION_FAILURES", 5) if category == "mfa" else (
        setting("LOGIN_FAILURES", 5) if category == "login" else setting("SUSPICIOUS_FAILURES", 3))
    duration = setting("TRADE_LOCK_SECONDS", 900) if category == "mfa" else (
        setting("LOGIN_LOCK_SECONDS", 900) if category == "login" else (
        setting("AGENT_LOCK_SECONDS", 300) if scope == "agent" else setting("SUSPICIOUS_LOCK_SECONDS", 1800)))
    current_time = now_utc()
    counters = dict(row.counters)
    current = dict(counters.get(category, {}))
    failures = [t for t in current.get("failures", []) if t > current_time.timestamp() - window]
    consecutive = min(current.get("consecutive", 0), len(failures))
    failures.append(current_time.timestamp())
    consecutive += 1
    # MFA rolling total persists across successful proofs within the window.
    max_total = setting("VERIFICATION_TOTAL_FAILURES", 10) if category == "mfa" else threshold
    counters[category] = {"consecutive": consecutive, "failures": failures[-max_total:]}
    row.counters = counters
    audit(db, row, "security_attempt_rejected", reason_code=reason, category=category,
          consecutive=consecutive, window_failures=len(failures), reference_id=reference_id, method=method)
    if on_record is not None:
        await on_record()
    if consecutive >= threshold or len(failures) >= max_total:
        row.locked_until = current_time + timedelta(seconds=duration)
        row.revoked_before, row.reason_code = current_time, reason
        row.generation += 1
        audit(db, row, "security_locked", reason_code=reason,
              locked_until=row.locked_until.isoformat())
    await db.commit()
    return row


VIOLATIONS = frozenset({"PAYER_FORBIDDEN", "EXECUTION_CONTEXT_INVALID", "PARAM_DIGEST_MISMATCH", "DIGEST_MISMATCH"})


async def report_violation(db: AsyncSession, user_id: str, code: str) -> None:
    if code not in VIOLATIONS:
        return
    await db.rollback()
    row = await record_failure(db, key=user_key(user_id), user_id=user_id, scope="trade",
                               category="violation", reason=code)
    ensure_unlocked(row)


async def status(db: AsyncSession, user_id: str) -> dict:
    rows = (await db.scalars(select(SecurityStateORM).where(SecurityStateORM.user_id == user_id))).all()
    active = [lock_detail(r) for r in rows if r.locked_until and aware(r.locked_until) > now_utc()]
    trade_locked = any(r["scope"] == "trade" for r in active)
    message = ("查询、取消待执行任务和保护操作仍可使用；旧交易需重新确认验证。" if trade_locked else
               "部分登录或智能会话暂时受限，其他未受限业务仍可使用。" if active else "当前未锁定")
    return {"locks": active, "trade_locked": trade_locked, "message": message}
