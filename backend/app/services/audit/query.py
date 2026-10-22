"""Owner-scoped query, legacy projection, bounded export and signed cursors."""
import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from sqlalchemy import and_, exists, or_, select
from ...models.audit_event import AuditEventORM as Event, AuditEventObjectORM as Object
from ...models.audit_log import AuditLogORM as Legacy
from .privacy import clean, references
from .writer import health
from .policy import action_domain


class AuditQueryError(ValueError):
    pass


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _key():
    from ...middleware.auth import JWT_SECRET
    return hmac.new(JWT_SECRET.encode(), b"audit-cursor-v1", hashlib.sha256).digest()


def _encode(data):
    raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(_key(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig + raw).decode().rstrip("=")


def _decode(token, owner, fingerprint):
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        sig, body = raw[:32], raw[32:]
        if not hmac.compare_digest(sig, hmac.new(_key(), body, hashlib.sha256).digest()):
            raise ValueError()
        data = json.loads(body)
        if data["owner"] != owner or data["filters"] != fingerprint:
            raise ValueError()
        return utc(datetime.fromisoformat(data["at"])), data["id"]
    except (ValueError, KeyError, TypeError):
        raise AuditQueryError("分页位置无效，请重新查询") from None


def legacy_domain(action):
    return action_domain(action)


def project_legacy(row):
    domain = legacy_domain(row.action)
    return dict(event_id="legacy:" + row.audit_id, schema_version=0, trace_id=None,
        request_id=None, span_id=None, parent_span_id=None, subject_user_id=row.user_id,
        actor_id=None, actor_type="unknown", domain=domain, action=row.action,
        phase="legacy", outcome=row.result, permission_level=row.permission_level,
        error_code=None, source_module="legacy.audit_logs", source_ref="audit_logs:" + row.audit_id,
        business_request_id=None, occurred_at=utc(row.timestamp).isoformat(),
        recorded_at=utc(row.timestamp).isoformat(), duration_ms=None, sequence=0,
        facts=clean({"params": row.params, "user_confirmed": row.user_confirmed,
                     "mfa_method": row.mfa_method, "decision_chain": row.decision_chain,
                     "legacy": True, "incomplete": True}),
        objects=references(row.params, domain), legacy=True)


async def _project(db, rows):
    ids = [r.event_id for r in rows]
    refs = (await db.scalars(select(Object).where(Object.event_id.in_(ids)))).all() if ids else []
    grouped = {}
    for obj in refs:
        grouped.setdefault(obj.event_id, []).append(dict(domain=obj.domain, object_type=obj.object_type,
            object_id=obj.object_id, version=obj.version))
    result = []
    for row in rows:
        data = {c.name: getattr(row, c.name) for c in Event.__table__.columns}
        for key in ("occurred_at", "recorded_at"):
            data[key] = utc(data[key]).isoformat()
        data.update(objects=grouped.get(row.event_id, []), legacy=False)
        result.append(data)
    return result


async def query_events(db, owner, *, since=None, until=None, domain=None, action=None,
                       outcome=None, permission_level=None, trace_id=None,
                       object_type=None, object_id=None, cursor=None, limit=50, view="all"):
    if not 1 <= limit <= 200:
        raise AuditQueryError("每页条数须在1至200之间")
    if since is not None:
        since = utc(since)
    if until is not None:
        until = utc(until)
    if since and until and since > until:
        raise AuditQueryError("开始时间不能晚于结束时间")
    identity = dict(since=since.isoformat() if since else None, until=until.isoformat() if until else None,
        domain=domain, action=action, outcome=outcome, permission_level=permission_level,
        trace_id=trace_id, object_type=object_type, object_id=object_id, view=view)
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    position = _decode(cursor, owner, fingerprint) if cursor else None
    stmt = select(Event).where(Event.subject_user_id == owner)
    legacy = select(Legacy).where(Legacy.user_id == owner, ~exists(select(Event.event_id).where(
        Event.source_ref == "audit_logs:" + Legacy.audit_id)))
    if view == "business":
        stmt = stmt.where(Event.phase.not_in(["start", "request", "transport"]), Event.domain != "audit")
    elif view != "all":
        raise AuditQueryError("显示范围无效")
    if since:
        stmt, legacy = stmt.where(Event.occurred_at >= since), legacy.where(Legacy.timestamp >= since)
    if until:
        stmt, legacy = stmt.where(Event.occurred_at <= until), legacy.where(Legacy.timestamp <= until)
    for value, column, old_column in (
        (action, Event.action, Legacy.action), (outcome, Event.outcome, Legacy.result),
        (permission_level, Event.permission_level, Legacy.permission_level),
    ):
        if value:
            stmt, legacy = stmt.where(column == value), legacy.where(old_column == value)
    if domain:
        stmt = stmt.where(Event.domain == domain)
    if trace_id:
        stmt = stmt.where(Event.trace_id == trace_id)
        legacy = legacy.where(False)
    if object_type and object_id:
        obj_filter = [Object.object_type == object_type, Object.object_id == object_id]
        if domain:
            obj_filter.append(Object.domain == domain)
        stmt = stmt.where(exists(select(Object.event_id).where(Object.event_id == Event.event_id, *obj_filter)))
        keys = {
            "task": "task_id", "operation": "operation_id", "plan": "plan_id",
            "authorization": "authorization_id", "transaction": "tx_id", "card": "card_id",
            "subscription": "subscription_id", "payment_request": "payment_request_id",
        }
        key = keys.get(object_type)
        legacy = legacy.where(Legacy.params[key].as_string() == object_id) if key else legacy.where(False)
    if position:
        at, key = position
        stmt = stmt.where(or_(Event.occurred_at < at, and_(Event.occurred_at == at, Event.event_id < key)))
        legacy = legacy.where(or_(Legacy.timestamp < at, and_(Legacy.timestamp == at, ("legacy:" + Legacy.audit_id) < key)))
    rows = (await db.scalars(stmt.order_by(Event.occurred_at.desc(), Event.event_id.desc()).limit(limit + 1))).all()
    # Domain is derived from the historical action; express it in SQL to keep
    # paging bounded and correct even for a large legacy-only database.
    if domain:
        from sqlalchemy import case
        old_domain = case(
            (Legacy.action.startswith("investment_"), "wealth"), (Legacy.action.startswith("wealth_"), "wealth"),
            (Legacy.action.startswith("card_"), "cards"), (Legacy.action.startswith("subscription_"), "subscriptions"),
            (Legacy.action.startswith("security_"), "identity"),
            (Legacy.action.startswith("bank_transfer"), "transfer"), (Legacy.action.startswith("transfer_"), "transfer"),
            (Legacy.action.startswith("payment_request_"), "payments"), (Legacy.action.startswith("merchant_refund"), "payments"),
            else_="bank")
        legacy = legacy.where(old_domain == domain)
    old_rows = (await db.scalars(legacy.order_by(Legacy.timestamp.desc(), Legacy.audit_id.desc()).limit(limit + 1))).all()
    items = await _project(db, rows)
    items.extend(project_legacy(r) for r in old_rows)
    items.sort(key=lambda r: (utc(datetime.fromisoformat(r["occurred_at"])), r["event_id"]), reverse=True)
    more = len(items) > limit
    items = items[:limit]
    next_cursor = _encode(dict(owner=owner, filters=fingerprint, at=items[-1]["occurred_at"], id=items[-1]["event_id"])) if more else None
    return dict(items=items, next_cursor=next_cursor, audit_health=health())


async def object_timeline(db, owner, object_type, object_id, domain=None):
    conditions = [Event.subject_user_id == owner, Object.object_type == object_type, Object.object_id == object_id]
    if domain:
        conditions.append(Object.domain == domain)
    ids = set(await db.scalars(select(Object.event_id).join(Event, Event.event_id == Object.event_id).where(*conditions).limit(501)))
    # Follow task -> plan/operation/authorization edges using only owned events.
    # Two bounded rounds cover the normal task/authorization/bank fact chain.
    truncated = len(ids) > 500
    for _ in range(2):
        refs = (await db.scalars(select(Object).where(Object.event_id.in_(ids)).limit(2000))).all() if ids else []
        keys = sorted({(o.domain, o.object_type, o.object_id) for o in refs
                       if o.object_type in {"task", "plan", "operation", "authorization", "node", "batch"}})
        truncated = truncated or len(keys) > 200 or len(refs) >= 2000
        links = [and_(Object.domain == d, Object.object_type == t, Object.object_id == i) for d, t, i in keys[:200]]
        if not links:
            break
        ids.update(await db.scalars(select(Object.event_id).join(Event, Event.event_id == Object.event_id).where(
            Event.subject_user_id == owner, or_(*links)).limit(501)))
    rows = (await db.scalars(select(Event).where(Event.subject_user_id == owner, Event.event_id.in_(ids))
        .order_by(Event.occurred_at, Event.event_id).limit(501))).all() if ids else []
    items = await _project(db, rows[:500])
    old = await query_events(db, owner, object_type=object_type, object_id=object_id, domain=domain, limit=200)
    items.extend(r for r in old["items"] if r["legacy"])
    items.sort(key=lambda r: (r["occurred_at"], r["event_id"]))
    return dict(items=items, truncated=truncated or len(rows) > 500 or old["next_cursor"] is not None,
                audit_health=health())


async def export_events(db, owner, *, since, until, **filters):
    if utc(until) - utc(since) > timedelta(days=31):
        raise AuditQueryError("每次最多导出31天的操作记录")
    items, cursor = [], None
    while True:
        page = await query_events(db, owner, since=since, until=until, cursor=cursor, limit=200, **filters)
        items.extend(page["items"])
        if len(items) > 5000:
            raise AuditQueryError("记录超过5000条，请缩小导出范围")
        cursor = page["next_cursor"]
        if not cursor:
            return dict(schema_version=1, exported_at=datetime.now(timezone.utc).isoformat(),
                        since=utc(since).isoformat(), until=utc(until).isoformat(), items=items)
