"""Keep a task graph in step with the task cards its turn produced.

Nodes are linked to the cards created in their turn by the catalog domain the model gave
the node, checked against the server's own card types. Node status is then derived from
the cards' live state each time the graph is read, so a card cancelled from the chat or
from the card itself shows up in the graph without another planning call.
"""
from .nova_tools import ToolContext

WAITING, DONE, CANCELLED, FAILED = "waiting_user", "done", "cancelled", "failed"
CARD_DOMAINS = {"execute": "transfer", "collection": "collection", "payment_request": "collection",
                "card": "cards", "subscription": "subscriptions", "calendar": "calendar",
                "shop": "shops", "wealth": "wealth", "scheduled": "scheduled_tasks", "bill": "bills"}
CARD_KINDS = {"prepare", "await_user"}


def _record_state(record: dict) -> str:
    kind, status, completed = record["type"], record["status"], record["completed"]
    if kind == "collection":
        return CANCELLED if status == "CANCELLED" else WAITING if status == "DRAFT" else DONE
    if kind == "card":
        if status == "COMPLETED":
            return DONE
        if status == "FAILED":
            return FAILED
        return CANCELLED if completed else WAITING
    if kind == "subscription":
        return DONE if status == "CANCELLED" else CANCELLED if completed else WAITING
    if kind == "calendar":
        return DONE if status == "COMPLETED" else CANCELLED if completed else WAITING
    if kind == "shop":
        if status in {"COMPLETED", "PARTIALLY_REFUNDED"}:
            return DONE
        return CANCELLED if completed else WAITING
    return DONE if completed else WAITING


async def card_states(context: ToolContext) -> dict[str, dict]:
    """Every task card of the conversation, in creation order within its type."""
    from . import nova_runtime as runtime
    from .transfer_manager import ManagerError, get_session
    states: dict[str, dict] = {}
    try:
        transfers = (await get_session(context.db, context.user_id, context.session_id))["tasks"]
    except ManagerError as error:
        if error.code != "SESSION_NOT_FOUND":
            raise
        transfers = []
    for task in transfers:
        status = task["status"]
        state = (CANCELLED if status == "CANCELLED" else FAILED if status == "FAILED"
                 else DONE if status == "SUCCEEDED" or runtime._transfer_chat_completed(task) else WAITING)
        states[task["chat_task_id"]] = {"type": "execute", "state": state}
    for records in (runtime._collection_records, runtime._card_records, runtime._subscription_records,
                    runtime._calendar_records, runtime._shop_records, runtime._payment_request_records,
                    runtime._wealth_records):
        for record in await records(context):
            states[record["id"]] = {"type": record["type"], "state": _record_state(record)}
    states.update(await _scheduled_states(context))
    states.update(await _bill_states(context))
    return states


async def _scheduled_states(context: ToolContext) -> dict[str, dict]:
    from ...models.nova_task import NovaSessionORM
    from ..scheduled_tasks import owned_view
    row = await context.db.get(NovaSessionORM, context.session_id)
    if row is None or row.user_id != context.user_id:
        return {}
    states = {}
    for entry in (row.state or {}).get("scheduled_tasks", []):
        task = await owned_view(context.db, context.user_id, entry["task_id"]) if entry.get("task_id") else None
        if entry.get("closed") or (entry.get("task_id") and task is None):
            state = CANCELLED
        elif task is None:
            state = WAITING
        else:
            # Saving the task fulfils the goal of setting it up; cancelling it later undoes that.
            state = CANCELLED if task["status"] == "CANCELLED" else DONE
        states[entry["entry_id"]] = {"type": "scheduled", "state": state}
    return states


async def _bill_states(context: ToolContext) -> dict[str, dict]:
    from ..bank import nova_bills
    from ..bank.nova_conversations import ConversationError
    try:
        cards = await nova_bills.cards(context.db, context.user_id, context.session_id)
    except ConversationError:
        return {}
    return {card["action_id"]: {"type": "bill", "state": {"pending": WAITING, "completed": DONE}.get(
        card["status"], CANCELLED)} for card in cards}


def _ancestors(nodes: list[dict], node_id: str) -> set[str]:
    by_id = {node["id"]: node for node in nodes}
    seen, stack = set(), list(by_id[node_id]["depends_on"])
    while stack:
        current = stack.pop()
        if current not in seen:
            seen.add(current)
            stack.extend(by_id[current]["depends_on"])
    return seen


def link(graph: dict, before: set[str], after: dict[str, dict]) -> dict:
    """Attach the cards created in this turn to the nodes whose domain produced them."""
    created: dict[str, list[str]] = {}
    for card_id, card in after.items():
        if card_id not in before and card["type"] in CARD_DOMAINS:
            created.setdefault(CARD_DOMAINS[card["type"]], []).append(card_id)
    nodes = [{**node, "task_ids": list(node.get("task_ids", []))} for node in graph["nodes"]]
    for domain, card_ids in created.items():
        owners = ([n for n in nodes if n["kind"] == "prepare" and domain in n["domains"]]
                  or [n for n in nodes if n["kind"] == "await_user" and domain in n["domains"]])
        if len(owners) == len(card_ids):
            for owner, card_id in zip(owners, card_ids):
                owner["task_ids"].append(card_id)
        else:
            for owner in owners:
                owner["task_ids"].extend(card_ids)
    by_id = {node["id"]: node for node in nodes}
    for node in nodes:
        if node["kind"] == "await_user" and not node["task_ids"]:
            # Confirming is the user's step on the card an earlier node prepared.
            node["task_ids"] = list(dict.fromkeys(card for ancestor in sorted(_ancestors(nodes, node["id"]))
                                                  for card in by_id[ancestor]["task_ids"]))
    return {**graph, "nodes": nodes}


def derive(graph: dict, states: dict[str, dict]) -> dict:
    """Node and graph status from live card state; the stored plan itself is unchanged."""
    if graph.get("outcome") == "planned":
        return {**graph, "state": "planned"}
    turn_ok = graph.get("outcome") != "failed"
    nodes = sorted(graph["nodes"], key=lambda node: node["level"])
    status: dict[str, str] = {}
    for node in nodes:
        task_ids = node.get("task_ids") or []
        if task_ids:
            cards = [states.get(card_id, {}).get("state", CANCELLED) for card_id in task_ids]
            confirmed_later = node["kind"] == "prepare" and any(
                other["kind"] == "await_user" and set(task_ids) & set(other.get("task_ids") or [])
                and node["id"] in _ancestors(graph["nodes"], other["id"]) for other in graph["nodes"])
            status[node["id"]] = (CANCELLED if all(card == CANCELLED for card in cards)
                                  else FAILED if FAILED in cards
                                  else DONE if confirmed_later
                                  else WAITING if WAITING in cards else DONE)
            continue
        deps = [status[dep] for dep in node["depends_on"]]
        if any(dep in {CANCELLED, "skipped"} for dep in deps):
            status[node["id"]] = "skipped"
        elif any(dep in {WAITING, "blocked"} for dep in deps):
            status[node["id"]] = "blocked"
        elif any(dep in {FAILED, "not_started"} for dep in deps) or node["kind"] in CARD_KINDS or not turn_ok:
            status[node["id"]] = "not_started"
        else:
            status[node["id"]] = DONE
    values = set(status.values())
    linked = [status[node["id"]] for node in graph["nodes"] if node.get("task_ids")]
    state = ("failed" if FAILED in values else WAITING if values & {WAITING, "blocked"}
             else ("cancelled" if all(value == CANCELLED for value in linked) else "partially_cancelled")
             if CANCELLED in values
             else "incomplete" if "not_started" in values else "completed")
    return {**graph, "state": state,
            "nodes": [{**node, "status": status[node["id"]]} for node in graph["nodes"]]}
