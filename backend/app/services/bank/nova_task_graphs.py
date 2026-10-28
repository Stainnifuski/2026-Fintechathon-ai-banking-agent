"""Encrypted per-request snapshots of NOVA task graphs for restoring a conversation."""
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select

from ...models.nova_task_graph import NovaTaskGraphORM as Snapshot
from . import nova_conversations as conversations
from .nova_bill_results import owned


def _redacted(graph: dict) -> dict:
    return {**graph, "rationale": conversations.redact(graph.get("rationale", "")),
            "nodes": [{**node, "goal": conversations.redact(node["goal"]),
                       "done_when": conversations.redact(node["done_when"])} for node in graph["nodes"]]}


async def save(db, user_id: str, session_id: str, request_id: str | None, graph: dict | None) -> dict | None:
    if not (request_id and graph):
        return None
    await owned(db, user_id, session_id)
    key = "TG-" + hashlib.sha256(f"{user_id}:{session_id}:{request_id}".encode()).hexdigest()[:32]
    graph = {**_redacted(graph), "request_id": request_id}
    stored = await db.get(Snapshot, key)
    if stored is None:
        db.add(Snapshot(snapshot_id=key, user_id=user_id, session_id=session_id, request_id=request_id,
                        graph_id=graph["graph_id"], version=graph["version"], created_at=datetime.now(timezone.utc),
                        payload_ciphertext=conversations._encrypt(json.dumps(graph, ensure_ascii=False))))
    else:
        stored.payload_ciphertext = conversations._encrypt(json.dumps(graph, ensure_ascii=False))
    await db.flush()
    return graph


async def graphs(db, user_id: str, session_id: str) -> list[dict]:
    """Complex graphs only; simple chains are kept just as context for the next revision."""
    await owned(db, user_id, session_id)
    rows = (await db.scalars(select(Snapshot).where(Snapshot.user_id == user_id, Snapshot.session_id == session_id)
                             .order_by(Snapshot.created_at, Snapshot.snapshot_id))).all()
    stored = [json.loads(conversations._decrypt(row.payload_ciphertext)) for row in rows]
    return [graph for graph in stored if graph.get("complex")]


async def latest(db, user_id: str, session_id: str | None) -> dict | None:
    if not session_id:
        return None
    row = await db.scalar(select(Snapshot).where(Snapshot.user_id == user_id, Snapshot.session_id == session_id)
                          .order_by(Snapshot.created_at.desc(), Snapshot.snapshot_id.desc()).limit(1))
    return json.loads(conversations._decrypt(row.payload_ciphertext)) if row else None
