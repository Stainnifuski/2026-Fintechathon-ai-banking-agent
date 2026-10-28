"""NOVA plans a task graph of sub-goals before tool selection; execution is unchanged."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import Base
from backend.app.models.nova_conversation import NovaConversationORM
from backend.app.schemas.nova import PageContext
from backend.app.services.ai import nova, nova_runtime, task_graph, task_graph_status
from backend.app.services.ai.llm import LLMError
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.services.bank import nova_task_graphs
from backend.app.services.bank.nova_conversations import ConversationError

DOMAINS = {"bills", "planning", "scheduled_tasks"}


def node(node_id, goal, kind="query", domains=(), depends_on=()):
    return task_graph.TaskNode(id=node_id, goal=goal, kind=kind, domains=list(domains),
                               depends_on=list(depends_on), done_when=goal + "完成")


def draft(*nodes, revises=False):
    return task_graph.TaskGraphDraft(nodes=list(nodes), revises_previous=revises, rationale="先查后定")


BUDGET_PLAN = draft(
    node("n1", "汇总本月支出", domains=["bills"]),
    node("n2", "查看现有预算", domains=["planning"]),
    node("n3", "给出下月预算建议", kind="answer", depends_on=["n1", "n2"]),
    node("n4", "设定下周执行的定时任务并锁定资金", kind="prepare", domains=["scheduled_tasks", "astrology"],
         depends_on=["n3"]),
)


@pytest_asyncio.fixture
async def context(monkeypatch):
    monkeypatch.setenv("NOVA_TASK_GRAPH", "1")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        db.add(NovaConversationORM(conversation_id="graph-session", user_id="u1", title_ciphertext="test"))
        await db.commit()
        yield ToolContext("u1", db, PageContext(), original_message="规划", request_id="r1",
                          session_id="graph-session")
    await engine.dispose()


class GraphModel:
    def __init__(self, graphs, *, calls=()):
        self.graphs, self.calls = list(graphs), list(calls)
        self.graph_prompts = []

    async def generate_structured(self, **kwargs):
        schema = kwargs["schema"]
        if schema is nova_runtime.DependencyDecision:
            return SimpleNamespace(data=schema(depends_on_prior_task=False, reason="context"))
        if schema is task_graph.TaskGraphDraft:
            self.graph_prompts.append(kwargs["messages"][-1]["content"])
            value = self.graphs.pop(0)
            if isinstance(value, Exception):
                raise value
            return SimpleNamespace(data=value)
        if schema is nova_runtime.ToolSelection:
            return SimpleNamespace(data=schema(tool_names=[call.name for call in self.calls]))
        return SimpleNamespace(data=schema(calls=self.calls))

    async def generate_text(self, **kwargs):
        return SimpleNamespace(data="已按计划处理。")


def test_check_reports_structure_errors_and_drops_unknown_domains_per_node():
    nodes, errors = task_graph.check(BUDGET_PLAN, DOMAINS)
    assert errors == []
    assert nodes[3]["domains"] == ["scheduled_tasks"] and nodes[3]["dropped_domains"] == ["astrology"]
    assert task_graph.levels(nodes) == {"n1": 0, "n2": 0, "n3": 1, "n4": 2}
    assert task_graph.is_complex(task_graph.levels(nodes))

    # A chain, even with a shortcut edge, runs one step after another: a simple task.
    chain, _ = task_graph.check(draft(node("n1", "a"), node("n2", "b", depends_on=["n1"]),
                                      node("n3", "c", depends_on=["n1", "n2"])), DOMAINS)
    assert not task_graph.is_complex(task_graph.levels(chain))
    single, _ = task_graph.check(draft(node("n1", "a")), DOMAINS)
    assert not task_graph.is_complex(task_graph.levels(single))

    _, errors = task_graph.check(draft(node("n1", "a", depends_on=["n2"]), node("n2", "b", depends_on=["n1"])), DOMAINS)
    assert errors == ["依赖关系存在环"]
    _, errors = task_graph.check(draft(node("n1", "a", depends_on=["n1", "n9"]), node("n1", "b")), DOMAINS)
    assert "节点编号重复" in errors and "n1 依赖自身" in errors and any("n9" in error for error in errors)


@pytest.mark.asyncio
async def test_runtime_streams_persists_and_restores_graph_without_changing_execution(context, monkeypatch):
    executed = []

    async def execute(call, _context):
        executed.append(call["function"]["name"])
        return {"data": {"total_fen": 120000}}

    monkeypatch.setattr(nova, "execute_tool", execute)
    events = []

    async def on_event(event):
        events.append(event)

    model = GraphModel([BUDGET_PLAN], calls=[nova_runtime.PlannedCall(name="get_bill_analysis", arguments={})])
    result = await nova_runtime.chat("看本月支出，定下月预算，下周锁500执行", [], model=model,
                                     context=context, on_event=on_event)

    assert executed == ["get_bill_analysis"]
    plan_event = next(event for event in events if event["type"] == "plan")
    assert [n["id"] for n in plan_event["graph"]["nodes"]] == ["n1", "n2", "n3", "n4"]
    assert plan_event["graph"]["state"] == "planned"
    assert all(n["status"] == "planned" for n in plan_event["graph"]["nodes"])
    graph = result["task_graph"]
    assert graph["complex"] and graph["version"] == 1 and graph["request_id"] == "r1" and graph["outcome"] == "completed"
    assert {n["id"]: n["level"] for n in graph["nodes"]} == {"n1": 0, "n2": 0, "n3": 1, "n4": 2}
    # Queries and the answer ran; the scheduled task was planned but no card was opened.
    assert {n["id"]: n["status"] for n in graph["nodes"]} == {
        "n1": "done", "n2": "done", "n3": "done", "n4": "not_started"}
    assert graph["state"] == "incomplete"
    assert graph["nodes"][3]["domains"] == ["scheduled_tasks"] and graph["nodes"][3]["domain_names"] == ["定时任务"]
    assert "当前没有上一版任务图" in model.graph_prompts[0]

    restored = await nova_task_graphs.graphs(context.db, "u1", "graph-session")
    assert [(g["graph_id"], g["version"]) for g in restored] == [(graph["graph_id"], 1)]
    with pytest.raises(ConversationError):
        await nova_task_graphs.graphs(context.db, "someone-else", "graph-session")

    revised = draft(node("n1", "汇总本月支出", domains=["bills"]),
                    node("n2", "给出下月预算建议", kind="answer", depends_on=["n1"]), revises=True)
    followup, events = GraphModel([revised]), []
    second = await nova_runtime.chat("不用锁钱了", [], model=followup, on_event=on_event,
                                     context=replace(context, request_id="r2", original_message="不用锁钱了"))
    # The revision became a chain: it is saved as the next version but not sent to the chat.
    assert "task_graph" not in second and not any(event["type"] == "plan" for event in events)
    latest = await nova_task_graphs.latest(context.db, "u1", "graph-session")
    assert (latest["graph_id"], latest["version"], latest["complex"]) == (graph["graph_id"], 2, False)
    shown = followup.graph_prompts[0]
    assert "设定下周执行的定时任务并锁定资金" in shown and '"level"' not in shown and '"status"' not in shown

    fresh = GraphModel([draft(node("n1", "查看订阅"))])
    third = await nova_runtime.chat("看看订阅", [], model=fresh,
                                    context=replace(context, request_id="r3", original_message="看看订阅"))
    # The model sees the latest graph and decides that an unrelated request starts a new one.
    assert "给出下月预算建议" in fresh.graph_prompts[0]
    latest = await nova_task_graphs.latest(context.db, "u1", "graph-session")
    assert "task_graph" not in third and latest["graph_id"] != graph["graph_id"] and latest["version"] == 1
    restored = await nova_task_graphs.graphs(context.db, "u1", "graph-session")
    assert [(g["graph_id"], g["version"]) for g in restored] == [(graph["graph_id"], 1)]

    from backend.app.services.bank import nova_conversations
    await nova_conversations.remove(context.db, "u1", "graph-session")
    assert await nova_task_graphs.latest(context.db, "u1", "graph-session") is None


@pytest.mark.asyncio
async def test_invalid_graph_gets_one_repair_round_and_planning_failure_never_stops_the_turn(context):
    cyclic = draft(node("n1", "a", depends_on=["n2"]), node("n2", "b", depends_on=["n1"]))
    model = GraphModel([cyclic, BUDGET_PLAN])
    result = await nova_runtime.chat("规划", [], model=model, context=context)
    assert "依赖关系存在环" in model.graph_prompts[1]
    assert len(result["task_graph"]["nodes"]) == 4

    model = GraphModel([cyclic, cyclic])
    result = await nova_runtime.chat("规划", [], model=model, context=replace(context, request_id="r2"))
    assert "task_graph" not in result and result["reply"] == "已按计划处理。"

    model = GraphModel([LLMError("MODEL_SCHEMA_INVALID"), BUDGET_PLAN])
    result = await nova_runtime.chat("规划", [], model=model, context=replace(context, request_id="r4"))
    assert "不要添加额外字段" in model.graph_prompts[1] and len(result["task_graph"]["nodes"]) == 4

    model = GraphModel([LLMError("MODEL_UNAVAILABLE")])
    result = await nova_runtime.chat("规划", [], model=model, context=replace(context, request_id="r3"))
    assert "task_graph" not in result and result["reply"] == "已按计划处理。"


@pytest.mark.asyncio
async def test_revision_without_a_previous_graph_starts_version_one(context):
    model = GraphModel([draft(node("n1", "查看订阅"), revises=True)])
    await nova_runtime.chat("看看订阅", [], model=model, context=context)
    latest = await nova_task_graphs.latest(context.db, "u1", "graph-session")
    assert latest["version"] == 1 and "当前没有上一版任务图" in model.graph_prompts[0]


@pytest.mark.asyncio
async def test_graph_planning_can_be_switched_off(context, monkeypatch):
    monkeypatch.setenv("NOVA_TASK_GRAPH", "0")
    model = GraphModel([])
    result = await nova_runtime.chat("规划", [], model=model, context=context)
    assert "task_graph" not in result and model.graph_prompts == []


TRANSFER_AND_CALENDAR = draft(
    node("n1", "查看本月账单", domains=["bills"]),
    node("n2", "准备给张三的转账", kind="prepare", domains=["transfer"]),
    node("n3", "本人确认转账", kind="await_user", domains=["transfer"], depends_on=["n2"]),
    node("n4", "转账后提醒我记账", kind="answer", depends_on=["n3"]),
    node("n5", "添加周五的聚餐日程", kind="prepare", domains=["calendar"]),
)


def settled(states):
    graph = task_graph.public({**_planned(TRANSFER_AND_CALENDAR), "outcome": "waiting_user"})
    return task_graph_status.link(graph, {"OLD"}, states)


def _planned(value):
    nodes, _ = task_graph.check(value, {"bills", "transfer", "calendar"})
    depth = task_graph.levels(nodes)
    for item in nodes:
        item.update(level=depth[item["id"]], status="planned", domain_names=[])
    return {"graph_id": "TG-x", "version": 1, "request_id": "r1", "rationale": "", "nodes": nodes,
            "complex": True}


def statuses(graph):
    return {n["id"]: n["status"] for n in graph["nodes"]}


def test_cards_created_in_the_turn_link_to_nodes_by_domain_and_drive_status():
    after = {"OLD": {"type": "execute", "state": "waiting_user"},
             "T1": {"type": "execute", "state": "waiting_user"},
             "C1": {"type": "calendar", "state": "waiting_user"}}
    graph = settled(after)
    links = {n["id"]: n["task_ids"] for n in graph["nodes"]}
    # A card that existed before the turn is never claimed; confirmation inherits the prepared card.
    assert links == {"n1": [], "n2": ["T1"], "n3": ["T1"], "n4": [], "n5": ["C1"]}

    waiting = task_graph_status.derive(graph, after)
    assert statuses(waiting) == {"n1": "done", "n2": "done", "n3": "waiting_user", "n4": "blocked",
                                 "n5": "waiting_user"}
    assert waiting["state"] == "waiting_user"

    cancelled = task_graph_status.derive(graph, {**after, "T1": {"type": "execute", "state": "cancelled"}})
    assert statuses(cancelled) == {"n1": "done", "n2": "cancelled", "n3": "cancelled", "n4": "skipped",
                                   "n5": "waiting_user"}
    both = {**after, "T1": {"type": "execute", "state": "cancelled"}, "C1": {"type": "calendar", "state": "done"}}
    assert task_graph_status.derive(graph, both)["state"] == "partially_cancelled"
    gone = task_graph_status.derive(graph, {"OLD": after["OLD"]})
    assert gone["state"] == "cancelled" and statuses(gone)["n5"] == "cancelled"
    done = task_graph_status.derive(graph, {**after, "T1": {"type": "execute", "state": "done"},
                                            "C1": {"type": "calendar", "state": "done"}})
    assert set(statuses(done).values()) == {"done"} and done["state"] == "completed"


def test_record_states_distinguish_cancelled_from_completed_cards():
    state = task_graph_status._record_state
    assert state({"type": "collection", "status": "CANCELLED", "completed": True}) == "cancelled"
    assert state({"type": "collection", "status": "SENT", "completed": True}) == "done"
    assert state({"type": "card", "status": "FORM", "completed": True}) == "cancelled"
    assert state({"type": "card", "status": "COMPLETED", "completed": True}) == "done"
    assert state({"type": "subscription", "status": "CANCELLED", "completed": True}) == "done"
    assert state({"type": "subscription", "status": "ACTIVE", "completed": True}) == "cancelled"
    assert state({"type": "shop", "status": "PAYMENT_CLOSED", "completed": True}) == "cancelled"
    assert state({"type": "calendar", "status": "PENDING", "completed": False}) == "waiting_user"


@pytest.mark.asyncio
async def test_graph_follows_a_card_cancelled_after_the_turn(context, monkeypatch):
    live = {}

    async def card_states(_context):
        return dict(live)

    async def execute(call, _context):
        live["T1"] = {"type": "execute", "state": "waiting_user"}
        return {"data": {"task_opened": True}}

    monkeypatch.setattr(task_graph_status, "card_states", card_states)
    monkeypatch.setattr(nova, "execute_tool", execute)
    model = GraphModel([TRANSFER_AND_CALENDAR],
                       calls=[nova_runtime.PlannedCall(name="manage_transfer_task", arguments={})])
    result = await nova_runtime.chat("看账单，给张三转账，周五聚餐记日程", [], model=model, context=context)
    assert statuses(result["task_graph"])["n3"] == "waiting_user"

    stored = (await nova_task_graphs.graphs(context.db, "u1", "graph-session"))[0]
    assert {n["id"]: n["task_ids"] for n in stored["nodes"]}["n2"] == ["T1"]
    live["T1"] = {"type": "execute", "state": "cancelled"}
    refreshed = task_graph_status.derive(stored, live)
    assert statuses(refreshed)["n2"] == "cancelled" and statuses(refreshed)["n4"] == "skipped"
    # The calendar step never opened a card, so cancelling the transfer cancels everything that ran.
    assert statuses(refreshed)["n5"] == "not_started" and refreshed["state"] == "cancelled"


@pytest.mark.asyncio
async def test_card_state_failure_drops_the_graph_but_not_the_turn(context, monkeypatch):
    calls = []

    async def broken(_context):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("card store unavailable")
        return {}

    monkeypatch.setattr(task_graph_status, "card_states", broken)
    result = await nova_runtime.chat("规划", [], model=GraphModel([BUDGET_PLAN]), context=context)
    assert "task_graph" not in result and result["reply"] == "已按计划处理。"

    calls.append(1)
    result = await nova_runtime.chat("规划", [], model=GraphModel([BUDGET_PLAN]),
                                     context=replace(context, request_id="r2"))
    assert "task_graph" not in result and result["reply"] == "已按计划处理。"
