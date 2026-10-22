"""Transaction-level coverage, including bulk mutations and direct service calls.

Committed snapshots share the business transaction. Observations (a rejected
verification, a proposed plan) survive rollback without claiming a mutation.
"""
import hashlib
from sqlalchemy import event, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from .context import current
from .policy import TABLE_DOMAINS, OBJECT_TYPES, action_domain
from .privacy import clean, references
from .writer import add_to_session, event_data, persist_after_transaction, AuditUnavailable


@event.listens_for(Engine, "handle_error")
def audit_storage_error(context):
    statement = (context.statement or "").lstrip().lower()
    if any(statement.startswith("insert into " + name) for name in
           ("audit_events", "audit_event_objects", "audit_logs")):
        raise AuditUnavailable("Audit persistence unavailable") from context.original_exception


def _owners(session, table, values, depth=0):
    if depth > 5:
        return []
    for key in ("user_id", "subject_user_id", "customer_user_id", "requester_user_id"):
        if values.get(key):
            # A payment is visible to its requester and its payer, through two
            # independently owned event views, never an unfiltered shared trace.
            return list(dict.fromkeys([values[key], *([values["payer_user_id"]] if values.get("payer_user_id") else [])]))
    from ...models.base import Base
    for key, target, pk in (
        ("task_id", "wealth_tasks" if table.startswith("wealth_") else "transfer_tasks", "task_id"),
        ("operation_id", "wealth_orders" if table.startswith("wealth_") else "transfer_operations", "operation_id"),
        ("authorization_id", "wealth_authorizations", "authorization_id"),
        ("payment_request_id", "payment_requests", "payment_request_id"),
        ("from_account", "accounts", "account_id"), ("account_id", "accounts", "account_id"),
        ("customer_account_id", "accounts", "account_id"),
        ("tx_id", "transactions", "tx_id"), ("stream_id", "bill_recurring_streams", "stream_id"),
        ("merchant_id", "bill_merchants", "merchant_id"),
    ):
        value = values.get(key)
        if not value or target == table:
            continue
        for obj in list(session.new) + list(session.identity_map.values()):
            if getattr(obj, "__tablename__", None) == target and obj.__dict__.get(pk) == value:
                found = _owners(session, target, obj.__dict__, depth + 1)
                if found:
                    return found
        target_table = Base.metadata.tables.get(target)
        if target_table is not None:
            row = session.connection().execute(select(target_table).where(target_table.c[pk] == value)).mappings().first()
            if row:
                found = _owners(session, target, dict(row), depth + 1)
                if found:
                    return found
    ctx = current.get()
    return [ctx.subject_user_id] if ctx and ctx.subject_user_id else [None]


def _snapshot(values):
    # Presence flags reveal that credentials changed, never the credentials.
    result = clean(dict(values))
    result.pop("_sa_instance_state", None)
    return result


def _mutation(session, table, before, after, mutation):
    domain = TABLE_DOMAINS[table.name]
    values = before if mutation == "deleted" else after
    identity = ":".join(str(values.get(c.name, "")) for c in table.primary_key.columns)
    refs = references(values, domain)
    if identity:
        refs.append(dict(domain=domain, object_type=OBJECT_TYPES.get(table.name, table.name),
                         object_id=identity[:128], version=str(values.get("version", values.get("state_version", ""))) or None))
    changed = [k for k in set(before) | set(after) if k != "_sa_instance_state" and before.get(k) != after.get(k)]
    for owner in _owners(session, table.name, values):
        data = event_data(action=table.name + "." + mutation, phase="mutation", outcome="committed",
            domain=domain, subject_user_id=owner, source_module="database." + table.name,
            objects=refs, facts={"table": table.name, "mutation": mutation,
                "before": _snapshot(before), "after": _snapshot(after), "fields": changed})
        add_to_session(session, data)


@event.listens_for(Session, "before_flush")
def before_flush(session, flush_context, instances):
    if session.info.get("audit_sink"):
        return
    from ...models.audit_event import AuditEventORM, AuditEventObjectORM
    from ...models.audit_log import AuditLogORM
    from ...models.workflow import StateEventORM
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(obj, (AuditEventORM, AuditEventObjectORM)):
            if obj in session.dirty or obj in session.deleted:
                raise ValueError("Audit events are append-only")
            continue
        if isinstance(obj, AuditLogORM):
            if obj in session.new:
                ref = "audit_logs:" + obj.audit_id
                data = event_data(event_id=hashlib.sha256(ref.encode()).hexdigest(), source_ref=ref,
                    domain=action_domain(obj.action),
                    action=obj.action, phase="decision", outcome=obj.result,
                    subject_user_id=obj.user_id, permission_level=obj.permission_level,
                    source_module="legacy.audit_logs", facts={"params": obj.params,
                        "decision_chain": obj.decision_chain, "user_confirmed": obj.user_confirmed,
                        "mfa_method": obj.mfa_method})
                add_to_session(session, data)
            continue
        if isinstance(obj, StateEventORM) and obj in session.new:
            domain = "transfer"
            ref = "transfer_state_events:" + obj.event_id
            owner = _owners(session, obj.__tablename__, obj.__dict__)[0]
            add_to_session(session, event_data(event_id=hashlib.sha256(ref.encode()).hexdigest(), source_ref=ref,
                domain=domain, action=obj.event_type, phase="state", outcome="committed",
                subject_user_id=owner, source_module="orchestration.transfer", business_request_id=obj.request_id,
                facts={"task_id": obj.task_id, "operation_id": obj.operation_id,
                       "before": obj.before_state, "after": obj.after_state}))
            continue
        table = getattr(obj, "__table__", None)
        if table is None or table.name not in TABLE_DOMAINS:
            continue
        if obj in session.dirty and not session.is_modified(obj, include_collections=False):
            continue
        values = {c.key: obj.__dict__.get(c.key) for c in inspect(obj).mapper.column_attrs}
        if obj in session.new:
            for column in table.columns:
                if column.key not in obj.__dict__ and column.default is not None and column.default.is_scalar:
                    values[column.key] = column.default.arg
            _mutation(session, table, {}, values, "created")
        elif obj in session.deleted:
            _mutation(session, table, values, {}, "deleted")
        else:
            previous = dict(values)
            if any(a.history.added and not a.history.deleted for a in inspect(obj).attrs):
                stored = session.connection().execute(select(table).where(
                    *[c == values[c.name] for c in table.primary_key.columns])).mappings().first()
                if stored:
                    previous.update(dict(stored))
            for attr in inspect(obj).attrs:
                if attr.history.deleted:
                    previous[attr.key] = attr.history.deleted[0]
            _mutation(session, table, previous, values, "updated")


@event.listens_for(Session, "do_orm_execute", retval=True)
def bulk_mutation(state):
    if not (state.is_update or state.is_delete or state.is_insert):
        return state.invoke_statement()
    table = getattr(state.statement, "table", None)
    if table is None:
        return state.invoke_statement()
    if table.name in {"audit_events", "audit_event_objects"} and not state.session.info.get("audit_sink"):
        raise ValueError("Audit events are append-only")
    if table.name not in TABLE_DOMAINS:
        return state.invoke_statement()
    if state.is_insert:
        # Core INSERT/ON CONFLICT bypasses before_flush (profile/security first
        # use). Compare by the supplied/returned primary key without consuming
        # the caller's result or logging a skipped ON CONFLICT as a mutation.
        fixed = {getattr(k, "key", k): getattr(v, "value", None)
                 for k, v in (getattr(state.statement, "_values", None) or {}).items()}
        supplied = state.parameters if isinstance(state.parameters, list) else [state.parameters or {}]
        candidates = [{**fixed, **p} for p in supplied]
        previous = []
        for values in candidates:
            if all(values.get(c.name) is not None for c in table.primary_key.columns):
                row = state.session.connection().execute(select(table).where(
                    *[c == values[c.name] for c in table.primary_key.columns])).mappings().first()
                previous.append(dict(row) if row else None)
            else:
                previous.append(None)
        result = state.invoke_statement()
        if getattr(result, "rowcount", None) == 0:
            return result
        if len(candidates) == 1 and not all(candidates[0].get(c.name) is not None for c in table.primary_key.columns):
            try:
                candidates[0].update(zip([c.name for c in table.primary_key.columns], result.inserted_primary_key))
            except Exception:
                # Inserts with explicit RETURNING may not expose this property.
                # Current business inserts supply their stable primary key.
                pass
        for values, before in zip(candidates, previous):
            if not all(values.get(c.name) is not None for c in table.primary_key.columns):
                raise ValueError("Audited bulk inserts require a stable primary key")
            after = state.session.connection().execute(select(table).where(
                *[c == values[c.name] for c in table.primary_key.columns])).mappings().first()
            if after and dict(after) != before:
                _mutation(state.session, table, before or {}, dict(after), "created" if before is None else "updated")
        return result
    # Take snapshots before compaction/deletion. No secret or text fields are
    # copied into the resulting event. This uses the same connection/transaction.
    params = state.parameters if isinstance(state.parameters, dict) else {}
    rows = list(state.session.connection().execute(
        select(table).where(*state.statement._where_criteria), params).mappings())
    result = state.invoke_statement()
    if getattr(result, "rowcount", 0) != 0:
        for row in rows:
            before = dict(row)
            if state.is_delete:
                _mutation(state.session, table, before, {}, "deleted")
            else:
                condition = [c == before[c.name] for c in table.primary_key.columns]
                after = state.session.connection().execute(select(table).where(*condition)).mappings().first()
                if after and dict(after) != before:
                    _mutation(state.session, table, before, dict(after), "updated")
    return result


@event.listens_for(Session, "before_commit")
def stage_observations(session):
    if session.info.get("audit_sink"):
        return
    ctx = current.get()
    staged = session.info.setdefault("audit_staged_ids", set())
    observations = session.info.setdefault("audit_observations", [])
    for data in list(ctx.pending) if ctx else []:
        if data["event_id"] not in staged:
            add_to_session(session, data)
            observations.append(data)
            staged.add(data["event_id"])
    # A savepoint rollback removes its ORM rows. Decisions/attempts must still
    # survive if the outer business transaction later commits successfully.
    from ...models.audit_event import AuditEventORM
    pending_ids = {obj.event_id for obj in session.new if isinstance(obj, AuditEventORM)}
    candidates = {d["event_id"]: d for d in observations if d["event_id"] not in pending_ids}
    if candidates:
        present = set(session.connection().scalars(select(AuditEventORM.event_id).where(
            AuditEventORM.event_id.in_(candidates))))
        for key, data in candidates.items():
            if key not in present:
                add_to_session(session, data)


@event.listens_for(Session, "after_commit")
def committed(session):
    if not session.in_nested_transaction():
        session.info["audit_committed"] = True


@event.listens_for(Session, "after_transaction_end")
def transaction_ended(session, transaction):
    if transaction.parent is not None or session.info.get("audit_sink"):
        return
    observations = session.info.pop("audit_observations", [])
    done = session.info.pop("audit_committed", False)
    session.info.pop("audit_staged_ids", None)
    if observations:
        if not done:
            persist_after_transaction(session, observations)
        ctx = current.get()
        if ctx:
            ids = {d["event_id"] for d in observations}
            ctx.pending[:] = [d for d in ctx.pending if d["event_id"] not in ids]
