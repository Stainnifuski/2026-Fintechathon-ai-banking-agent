"""The phase-one runtime keeps task state from bank facts and scopes tools."""
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import Base
from backend.app.models.nova_conversation import NovaConversationORM
from backend.app.models.nova_task import NovaSessionORM
from backend.app.services.ai import nova_runtime
from backend.app.services.ai import nova
from backend.app.services.ai import debug_trace
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.schemas.nova import PageContext


@pytest_asyncio.fixture
async def context():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        db.add(NovaConversationORM(conversation_id="test-session", user_id="u1", title_ciphertext="test"))
        await db.commit()
        yield ToolContext("u1", db, PageContext(), original_message="查询", request_id="r1",
                          session_id="test-session")
    await engine.dispose()


class FakeModel:
    def __init__(self, dependent: bool, names: list[str], calls: list[nova_runtime.PlannedCall]):
        self.dependent, self.names, self.calls = dependent, names, calls
        self.schemas = []

    async def generate_structured(self, **kwargs):
        schema = kwargs["schema"]
        self.schemas.append(schema)
        if schema is nova_runtime.DependencyDecision:
            return SimpleNamespace(data=schema(depends_on_prior_task=self.dependent, reason="context"))
        if schema is nova_runtime.ToolSelection:
            return SimpleNamespace(data=schema(tool_names=self.names))
        return SimpleNamespace(data=schema(calls=self.calls))


@pytest.mark.asyncio
async def test_grounded_reply_is_sent_in_ordered_deltas():
    events = []

    async def on_event(event):
        events.append(event)

    reply = '已核验的银行卡信息会逐段显示。' * 5
    await nova._stream_grounded_reply(on_event, {'mode': 'model', 'reply': reply})
    assert len(events) > 1
    assert all(event['type'] == 'delta' for event in events)
    assert ''.join(event['text'] for event in events) == reply


@pytest.mark.asyncio
async def test_two_transfer_calls_use_distinct_receipts_and_latest_session_version(context, monkeypatch):
    observed = []

    async def execute(call, tool_context):
        observed.append((tool_context.request_id, tool_context.session_version))
        next_version = tool_context.session_version + 1
        return {"session_id": tool_context.session_id, "session_version": next_version,
                "tasks": [], "focus_task_id": f"card-{next_version}", "reply": "草稿已创建"}

    async def bank(*_args):
        return {"tasks": []}

    monkeypatch.setattr(nova, "execute_tool", execute)
    monkeypatch.setattr(nova_runtime, "get_session", bank)
    model = FakeModel(False, ["manage_transfer_task"], [
        nova_runtime.PlannedCall(name="manage_transfer_task", arguments={"action": "create"}),
        nova_runtime.PlannedCall(name="manage_transfer_task", arguments={"action": "create"}),
    ])
    result = await nova_runtime.chat("发起两笔转账", [], model=model, context=context)
    assert observed == [("r1", 0), ("r1-t2", 1)]
    assert result["session_version"] == 2


@pytest.mark.asyncio
async def test_read_turn_keeps_target_and_records_one_read_task(context, monkeypatch):
    cards = [{"chat_task_id": "card-1", "status": "COLLECTING", "recipient": "李四",
              "amount_fen": None, "form": {}, "missing_fields": ["amount"]}]

    async def bank(*args):
        return {"tasks": cards}

    monkeypatch.setattr(nova_runtime, "get_session", bank)
    await nova_runtime.llm_finish_tool(context, [])
    row, state = await nova_runtime._load(context)
    assert state["target_execute_task_ids"] == ["card-1"]

    model = FakeModel(False, ["get_current_time"],
                      [nova_runtime.PlannedCall(name="get_current_time", arguments={})])
    result = await nova_runtime.chat("现在几点？", [], model=model, context=context)
    assert [item["name"] for item in result["tool_calls"]] == [
        "describe_bank_capabilities", "inject_agent_tools", "get_current_time", "llm_finish_tool"]
    _, state = await nova_runtime._load(context)
    assert state["target_execute_task_ids"] == ["card-1"]
    assert len(state["read_tasks"]) == 1
    assert state["read_tasks"][0]["description"]["tool"] == "get_current_time"


@pytest.mark.asyncio
async def test_multiple_cards_reconcile_and_terminal_target_clears(context, monkeypatch):
    cards = [{"chat_task_id": name, "status": "COLLECTING", "recipient": name,
              "amount_fen": None, "form": {}, "missing_fields": ["amount"]}
             for name in ("card-1", "card-2")]

    async def bank(*args):
        return {"tasks": cards}

    monkeypatch.setattr(nova_runtime, "get_session", bank)
    first = await nova_runtime.llm_finish_tool(context, [])
    assert first["target_execute_task_ids"] == ["card-1", "card-2"]
    cards[0]["status"] = "CANCELLED"
    second = await nova_runtime.llm_finish_tool(context, [])
    assert second["target_execute_task_ids"] == ["card-2"]
    assert second["execute_tasks"][0]["completed"] is True
    cards[1]["status"] = "FAILED"
    third = await nova_runtime.llm_finish_tool(context, [])
    assert third["target_execute_task_ids"] == []


@pytest.mark.asyncio
async def test_collection_tool_is_available_without_inspection_call(context, monkeypatch):
    async def bank(*args):
        raise nova_runtime.ManagerError("missing", "SESSION_NOT_FOUND")

    monkeypatch.setattr(nova_runtime, "get_session", bank)
    model = FakeModel(False, ["manage_collection_request"],
                      [nova_runtime.PlannedCall(name="manage_collection_request", arguments={})])
    result = await nova_runtime.chat("向别人收款", [], model=model, context=context)
    assert any(t["name"] == "manage_collection_request" and
               t["result"].get("error") == "INVALID_ARGUMENTS" for t in result["tool_calls"])
    assert all(t["name"] != "inspect_collection_request" for t in result["tool_calls"])
    assert model.schemas[:3] == [nova_runtime.DependencyDecision,
                                 nova_runtime.ToolSelection, nova_runtime.ToolPlan]


@pytest.mark.asyncio
async def test_card_operation_is_injected_as_execute_tool(context, monkeypatch):
    async def bank(*args):
        raise nova_runtime.ManagerError("missing", "SESSION_NOT_FOUND")

    seen = []

    async def execute(call, tool_context):
        seen.append(call["function"]["name"])
        if call["function"]["name"] == "list_cards":
            return {"data": {"items": [{"card_id": "C1"}]}}
        row = NovaSessionORM(session_id=tool_context.session_id, user_id=tool_context.user_id,
                             version=0, state={"card_operations": [{
                                 "entry_id": "card-entry-1", "origin_request_id": tool_context.request_id,
                                 "action": "card_lock", "card_id": None, "prefill": {},
                                 "notice": None, "operation_id": None, "closed": False,
                             }]})
        tool_context.db.add(row)
        await tool_context.db.commit()
        return {"source": "cards.operation_form", "data": {"form_opened": True, "executed": False}}

    monkeypatch.setattr(nova_runtime, "get_session", bank)
    monkeypatch.setattr(nova, "execute_tool", execute)
    model = FakeModel(False, ["preview_card_operation"], [
        nova_runtime.PlannedCall(name="preview_card_operation", arguments={"action": "card_lock"})])
    result = await nova_runtime.chat("锁定这张卡", [], model=model, context=context)
    assert "preview_card_operation" in nova_runtime._phase_tools(nova.TOOL_REGISTRY)
    assert seen == ["list_cards", "preview_card_operation"]
    assert any(item["name"] == "preview_card_operation" and not item["result"].get("error")
               for item in result["tool_calls"])
    finish = result["tool_calls"][-1]["result"]
    assert finish["outcome"] == "waiting_user"
    assert finish["target_execute_task_ids"] == ["card-entry-1"]
    assert finish["read_task_count"] == 1
    assert (await nova_runtime.inspect_active_tasks(context))["unfinished_execute_tasks"][0]["type"] == "card"


@pytest.mark.asyncio
async def test_card_read_is_followed_by_operation_call(context, monkeypatch):
    async def bank(*args):
        raise nova_runtime.ManagerError("missing", "SESSION_NOT_FOUND")

    seen = []

    async def execute(call, tool_context):
        name = call["function"]["name"]
        seen.append(name)
        return {"data": {"items": [{"card_id": "C1", "card_number": "****8842"}]}} if name == "list_cards" else {
            "source": "cards.operation_form", "data": {"form_opened": True, "executed": False}}

    class ReadThenOperate(FakeModel):
        def __init__(self):
            super().__init__(False, ["list_cards", "preview_card_operation"], [])
            self.plans = 0

        async def generate_structured(self, **kwargs):
            if kwargs["schema"] is nova_runtime.ToolPlan:
                self.plans += 1
                call = (nova_runtime.PlannedCall(name="list_cards", arguments={}) if self.plans == 1
                        else nova_runtime.PlannedCall(name="preview_card_operation",
                                                      arguments={"action": "card_lock", "card_id": "C1"}))
                return SimpleNamespace(data=nova_runtime.ToolPlan(calls=[call]))
            return await super().generate_structured(**kwargs)

    monkeypatch.setattr(nova_runtime, "get_session", bank)
    monkeypatch.setattr(nova, "execute_tool", execute)
    await nova_runtime.chat("锁定尾号8842的卡", [], model=ReadThenOperate(), context=context)
    assert seen == ["list_cards", "preview_card_operation"]


@pytest.mark.asyncio
async def test_dependent_turn_inspects_pending_cards_and_records_wait(context, monkeypatch):
    cards = [{"chat_task_id": "card-1", "status": "COLLECTING", "recipient": "李四",
              "amount_fen": None, "form": {}, "missing_fields": ["amount"]}]

    async def bank(*args):
        return {"tasks": cards}

    monkeypatch.setattr(nova_runtime, "get_session", bank)
    await nova_runtime.llm_finish_tool(context, [])
    model = FakeModel(True, [], [])
    result = await nova_runtime.chat("把这笔改一下", [], model=model, context=context)
    assert [t["name"] for t in result["tool_calls"]][:2] == [
        "describe_bank_capabilities", "inspect_active_tasks"]
    assert result["tool_calls"][-1]["result"]["outcome"] == "waiting_user"


@pytest.mark.asyncio
async def test_native_model_receives_only_dynamically_injected_tools(context, monkeypatch):
    async def bank(*args):
        raise nova_runtime.ManagerError("missing", "SESSION_NOT_FOUND")

    monkeypatch.setattr(nova_runtime, "get_session", bank)

    class NativeModel(FakeModel):
        async def generate_with_tools(self, **kwargs):
            assert [tool["function"]["name"] for tool in kwargs["tools"]] == ["get_current_time"]
            return SimpleNamespace(data={"tool_calls": [{"function": {
                "name": "manage_collection_request", "arguments": "{}"}}]})

    model = NativeModel(False, ["get_current_time"],
                        [nova_runtime.PlannedCall(name="get_current_time", arguments={})])
    result = await nova_runtime.chat("现在几点？", [], model=model, context=context)
    assert all(t["name"] != "manage_collection_request" or
               t["result"].get("error") == "TOOL_NOT_ALLOWED" for t in result["tool_calls"])


@pytest.mark.asyncio
async def test_debug_mode_records_runtime_decisions_and_results(context, monkeypatch, tmp_path):
    monkeypatch.setattr(debug_trace, "_root", tmp_path)

    async def bank(*args):
        raise nova_runtime.ManagerError("missing", "SESSION_NOT_FOUND")

    monkeypatch.setattr(nova_runtime, "get_session", bank)
    model = FakeModel(False, ["get_current_time"],
                      [nova_runtime.PlannedCall(name="get_current_time", arguments={})])
    with debug_trace.capture("u1", "debug-runtime-1", True, context.session_id):
        await nova_runtime.chat("现在几点？", [], model=model, context=context)
    import json
    trace = json.loads(debug_trace.read("u1", "debug-runtime-1"))
    stages = [event["stage"] for event in trace["events"]]
    assert {"runtime_context", "dependency_decision", "tool_selection", "tool_injection",
            "tool_plan", "tool_call", "runtime_finish"} <= set(stages)
    assert debug_trace.list_recent("u1", context.session_id)[0]["request_id"] == "debug-runtime-1"
