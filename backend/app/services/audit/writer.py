"""Transactional events and bounded, locked recovery journal for observations."""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import insert, select
from .context import AuditContext, current
from .privacy import clean, references, IDENTIFIER

logger = logging.getLogger(__name__)
_health = {"status": "ok", "fallback_count": 0}


class AuditUnavailable(RuntimeError):
    code = "AUDIT_UNAVAILABLE"


def health() -> dict:
    return dict(_health)


def default_factory():
    from ...database.connection import AsyncSessionLocal
    return AsyncSessionLocal


def event_data(*, action, phase, outcome, domain="system", source_module="application",
               subject_user_id=None, facts=None, permission_level="unknown", error_code=None,
               source_ref=None, event_id=None, business_request_id=None, duration_ms=None,
               objects=(), context=None):
    ctx = context or current.get() or AuditContext()
    ctx.sequence += 1
    now = datetime.now(timezone.utc)
    safe = clean(facts or {})
    refs = [*ctx.objects, *objects, *references(safe, domain)]
    unique = {}
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        obj_id = ref.get("object_id", "")
        if not isinstance(obj_id, str) or not IDENTIFIER.fullmatch(obj_id):
            continue
        key = (ref.get("domain", domain), ref.get("object_type", "object"), obj_id)
        unique[key] = dict(domain=key[0], object_type=key[1], object_id=key[2],
                           version=str(ref["version"])[:40] if ref.get("version") is not None else None)
    return dict(
        event_id=event_id or uuid4().hex, schema_version=1,
        trace_id=ctx.trace_id, request_id=ctx.request_id,
        span_id=ctx.span_id, parent_span_id=ctx.parent_span_id,
        subject_user_id=subject_user_id or ctx.subject_user_id,
        actor_id=ctx.actor_id, actor_type=ctx.actor_type,
        domain=domain[:32], action=action[:128], phase=phase[:32], outcome=outcome[:32],
        permission_level=permission_level[:16], error_code=clean(error_code)[:96] if error_code else None,
        source_module=source_module[:128], source_ref=source_ref,
        business_request_id=clean(business_request_id)[:128] if isinstance(business_request_id, str) else None,
        occurred_at=now, recorded_at=now,
        duration_ms=duration_ms, sequence=ctx.sequence, facts=safe, objects=list(unique.values()),
    )


def add_to_session(session, data):
    from ...models.audit_event import AuditEventORM, AuditEventObjectORM
    row = AuditEventORM(**{k: v for k, v in data.items() if k != "objects"})
    row.objects = [AuditEventObjectORM(event_id=data["event_id"], **obj) for obj in data["objects"]]
    session.add(row)
    return row


def emit(**kwargs):
    """Observe a fact; commits stage observations in the business transaction."""
    data = event_data(**kwargs)
    ctx = current.get()
    if ctx is not None:
        ctx.pending.append(data)
    return data


def observe(db, **kwargs):
    """An observation survives rollback; unlike a committed mutation event."""
    data = event_data(**kwargs)
    sync = getattr(db, "sync_session", db)
    sync.info.setdefault("audit_observations", []).append(data)
    add_to_session(sync, data)
    return data


def _journal_dir():
    from pathlib import Path
    path = Path(os.getenv("AUDIT_FALLBACK_DIR", "var/audit-recovery")).absolute()
    if path.is_symlink():
        raise AuditUnavailable("Unsafe audit journal directory")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _lock(fd):
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock(fd):
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def _private_open(path):
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    os.chmod(path, 0o600)
    if os.name == "nt" and path.name.endswith(".lock") and not os.fstat(fd).st_size:
        os.write(fd, b"\0")
    return fd


def journal(events):
    """Never overwrite an unreplayed segment. Fsync before acknowledging fallback."""
    path = _journal_dir()
    lock = _private_open(path / "journal.lock")
    try:
        _lock(lock)
        payloads = []
        for data in events:
            safe = dict(data, facts=clean(data["facts"]))
            raw = (json.dumps(safe, default=str, ensure_ascii=False) + "\n").encode()
            if len(raw) > 256 * 1024:
                raise AuditUnavailable("Audit event exceeds journal limit")
            payloads.append(raw)
        size = sum(p.stat().st_size for pattern in ("pending-*.jsonl", "incomplete-*.jsonl") for p in path.glob(pattern))
        budget = int(os.getenv("AUDIT_FALLBACK_MAX_BYTES", str(64 * 1024 * 1024)))
        if size + sum(map(len, payloads)) > budget:
            raise AuditUnavailable("Audit recovery capacity exhausted")
        segment_id = uuid4().hex
        partial = path / ("incomplete-" + segment_id + ".jsonl")
        target = path / ("pending-" + segment_id + ".jsonl")
        fd = _private_open(partial)
        try:
            with os.fdopen(fd, "wb") as stream:
                for raw in payloads:
                    stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(partial, target)
            if os.name != "nt":
                directory_fd = os.open(path, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except BaseException:
            # A partial segment is retained for inspection, never silently erased.
            raise
        _health.update(status="degraded", fallback_count=_health["fallback_count"] + len(events))
    finally:
        _unlock(lock)
        os.close(lock)


def _restore(data):
    data = dict(data)
    for key in ("occurred_at", "recorded_at"):
        if isinstance(data[key], str):
            data[key] = datetime.fromisoformat(data[key])
    data["recorded_at"] = datetime.now(timezone.utc)
    return data


def replay_sync(bind):
    """A locked replay is safe after a crash between DB commit and journal removal."""
    from ...models.audit_event import AuditEventORM
    path = _journal_dir()
    lock = _private_open(path / "journal.lock")
    count = 0
    try:
        _lock(lock)
        for segment in sorted(path.glob("pending-*.jsonl")):
            events = [_restore(json.loads(line)) for line in segment.read_text().splitlines() if line]
            with bind.begin() as conn:
                for data in events:
                    exists = conn.scalar(select(AuditEventORM.event_id).where(
                        AuditEventORM.event_id == data["event_id"]))
                    if not exists:
                        insert_sync(conn, data)
                        count += 1
            segment.unlink()
        _health.update(status="degraded" if any(path.glob("incomplete-*.jsonl")) else "ok", fallback_count=0)
    finally:
        _unlock(lock)
        os.close(lock)
    return count


def insert_sync(conn, data):
    from ...models.audit_event import AuditEventORM, AuditEventObjectORM
    conn.execute(insert(AuditEventORM), {k: v for k, v in data.items() if k != "objects"})
    if data["objects"]:
        conn.execute(insert(AuditEventObjectORM), [dict(event_id=data["event_id"], **o) for o in data["objects"]])


async def persist_pending(ctx=None, *, factory=None):
    ctx = ctx or current.get()
    if ctx is None or not ctx.pending:
        return
    if factory is None:
        factory = default_factory()
    events = list(ctx.pending)
    try:
        async with factory() as db:
            # Do not stage this context again in the commit hook.
            db.sync_session.info["audit_sink"] = True
            for data in events:
                add_to_session(db, data)
            await db.commit()
    except Exception:
        try:
            await asyncio.to_thread(journal, events)
            ctx.degraded = True
        except Exception:
            _health["status"] = "unavailable"
            logger.error("AUDIT_UNAVAILABLE: database and recovery journal unavailable")
            raise AuditUnavailable("Audit persistence unavailable") from None
    ids = {d["event_id"] for d in events}
    ctx.pending[:] = [d for d in ctx.pending if d["event_id"] not in ids]


def persist_after_transaction(session, events):
    """Called only once the outer transaction and its locks have ended."""
    try:
        with session.get_bind().begin() as conn:
            for data in events:
                insert_sync(conn, data)
    except Exception:
        try:
            journal(events)
        except Exception:
            _health["status"] = "unavailable"
            logger.error("AUDIT_UNAVAILABLE: rolled-back observation could not be retained")


async def recovery_loop(session_factory, *, interval_seconds=30):
    """Recover during normal operation too, with no repeated idle audit events."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with session_factory() as db:
                conn = await db.connection()
                await conn.run_sync(lambda c: replay_sync(c.engine))
        except asyncio.CancelledError:
            raise
        except Exception:
            _health["status"] = "degraded"
            logger.warning("Audit recovery deferred; retained segments will be retried")
