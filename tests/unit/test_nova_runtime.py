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

    async def generate_text(self, **kwargs):
        reply = '模型根据工具结果给出的自然语言回复。'
        if kwargs.get('on_delta'):
            await kwargs['on_delta'](reply[:6])
            await kwargs['on_delta'](reply[6:])
        return SimpleNamespace(data=reply)


@pytest.mark.asyncio
async def test_calendar_query_skill_replans_after_search_and_list(context, monkeypatch):
    class CalendarModel:
        def __init__(self):
            self.round = 0
            self.planning_messages = []

        async def generate_structured(self, **kwargs):
            schema = kwargs['schema']
            if schema is nova_runtime.DependencyDecision:
                return SimpleNamespace(data=schema(depends_on_prior_task=False, reason='calendar query'))
            if schema is nova_runtime.ToolSelection:
                name = ('search_calendar_events', 'list_calendar_events', 'get_calendar_event')[self.round]
                self.round += 1
                return SimpleNamespace(data=schema(tool_names=[name]))
            self.planning_messages.append(kwargs['messages'])
            name = ('search_calendar_events', 'list_calendar_events', 'get_calendar_event')[self.round - 1]
            arguments = {'keyword': '会议'} if name == 'search_calendar_events' else {}
            return SimpleNamespace(data=schema(calls=[nova_runtime.PlannedCall(name=name, arguments=arguments)]))

        async def generate_text(self, **kwargs):
            return SimpleNamespace(data='已根据实际日程核对。')

    observed = []
    async def execute(call, _context):
        name = call['function']['name']
        observed.append(name)
        if name == 'search_calendar_events':
            return {'data': {'items': [], 'total': 0}}
        if name == 'list_calendar_events':
            return {'data': {'items': [{'event_id': 'CE1', 'title': '项目会'}], 'total': 1}}
        return {'data': {'event_id': 'CE1', 'title': '项目会'}}

    monkeypatch.setattr(nova, 'execute_tool', execute)
    model = CalendarModel()
    await nova_runtime.chat('查询会议', [], model=model, context=context)
    assert observed == ['search_calendar_events', 'list_calendar_events', 'get_calendar_event']
    assert any('日历信息查询规则' in item['content'] for item in model.planning_messages[0])
    assert any('"total": 0' in item['content'] for item in model.planning_messages[1])
    assert any('项目会' in item['content'] for item in model.planning_messages[2])


@pytest.mark.asyncio
async def test_calendar_add_continues_after_time_read_and_opens_card(context):
    class CalendarAddModel(FakeModel):
        def __init__(self):
            super().__init__(False, ['get_current_time', 'manipulate_calender_event'], [])
            self.round = 0

        async def generate_structured(self, **kwargs):
            schema = kwargs['schema']
            if schema is nova_runtime.ToolPlan:
                self.round += 1
                call = (nova_runtime.PlannedCall(name='get_current_time', arguments={}) if self.round == 1 else
                        nova_runtime.PlannedCall(name='manipulate_calender_event', arguments={
                            'action': 'add', 'start_date': '2026-10-28'}))
                return SimpleNamespace(data=schema(calls=[call]))
            return await super().generate_structured(**kwargs)

    result = await nova_runtime.chat('明天上午添加一个事件', [], model=CalendarAddModel(), context=context)
    assert [item['name'] for item in result['tool_calls'] if item['name'] in
            {'get_current_time', 'manipulate_calender_event'}] == ['get_current_time', 'manipulate_calender_event']
    row = await context.db.get(NovaSessionORM, context.session_id)
    assert len(row.state['calendar_tasks']) == 1
    assert row.state['calendar_tasks'][0]['changes'] == {'start_date': '2026-10-28'}


@pytest.mark.asyncio
async def test_calendar_add_never_claims_card_without_successful_tool_call(context):
    model = FakeModel(False, ['get_current_time', 'manipulate_calender_event'], [
        nova_runtime.PlannedCall(name='get_current_time', arguments={})])
    result = await nova_runtime.chat('明天上午添加一个事件', [], model=model, context=context)
    assert '尚未生成' in result['reply']
    assert not any(item['name'] == 'manipulate_calender_event' for item in result['tool_calls'])


@pytest.mark.asyncio
async def test_fallback_reply_is_sent_in_ordered_deltas():
    events = []

    async def on_event(event):
        events.append(event)

    reply = '已核验的银行卡信息会逐段显示。' * 5
    await nova._stream_reply(on_event, {'mode': 'model', 'reply': reply})
    assert len(events) > 1
    assert all(event['type'] == 'delta' for event in events)
    assert ''.join(event['text'] for event in events) == reply


@pytest.mark.asyncio
async def test_chat_returns_model_final_answer_and_forwards_its_deltas(context):
    events = []

    async def on_event(event):
        events.append(event)

    model = FakeModel(False, ['get_current_time'], [
        nova_runtime.PlannedCall(name='get_current_time', arguments={})])
    result = await nova.chat('现在几点？', [], model=model, context=context, on_event=on_event)
    assert result['reply'] == '模型根据工具结果给出的自然语言回复。'
    assert [event['text'] for event in events if event['type'] == 'delta'] == [
        '模型根据工具', '结果给出的自然语言回复。']
    assert 'grounding' not in result


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
async def test_subscription_operation_is_injected_and_recorded_as_execute_task(context, monkeypatch):
    async def bank(*args):
        raise nova_runtime.ManagerError('missing', 'SESSION_NOT_FOUND')

    async def execute(call, tool_context):
        assert call['function']['name'] == 'manage_subscription_task'
        assert call['function']['arguments'] == '{"action": "open", "merchant_name": "音乐会员"}'
        tool_context.db.add(NovaSessionORM(session_id=tool_context.session_id,
            user_id=tool_context.user_id, version=0, state={'subscription_tasks': [{
                'entry_id': 'sub-entry-1', 'origin_request_id': tool_context.request_id,
                'subscription_id': None, 'closed': False,
            }]}))
        await tool_context.db.commit()
        return {'source': 'subscriptions.cancel_request', 'data': {'form_opened': True, 'executed': False}}

    monkeypatch.setattr(nova_runtime, 'get_session', bank)
    monkeypatch.setattr(nova, 'execute_tool', execute)
    model = FakeModel(False, ['manage_subscription_task'], [
        nova_runtime.PlannedCall(name='manage_subscription_task',
                                 arguments={'action': 'open', 'merchant_name': '音乐会员'})])
    result = await nova_runtime.chat('取消音乐会员订阅', [], model=model, context=context)
    assert 'manage_subscription_task' in nova_runtime._phase_tools(nova.TOOL_REGISTRY)
    assert any(item['name'] == 'manage_subscription_task' and not item['result'].get('error')
               for item in result['tool_calls'])
    finish = result['tool_calls'][-1]['result']
    assert finish['target_execute_task_ids'] == ['sub-entry-1']
    assert finish['outcome'] == 'waiting_user'
    assert (await nova_runtime.inspect_active_tasks(context))['unfinished_execute_tasks'][0]['type'] == 'subscription'


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
async def test_model_can_cancel_existing_card_task_without_opening_another_form(context):
    context.db.add(NovaSessionORM(session_id=context.session_id, user_id=context.user_id,
        version=0, state={'card_operations': [{
            'entry_id': 'pending-card', 'origin_request_id': 'earlier-request',
            'action': 'card_lock', 'card_id': None, 'prefill': {},
            'notice': None, 'operation_id': None, 'closed': False,
        }]}))
    await context.db.commit()
    model = FakeModel(True, ['preview_card_operation'], [
        nova_runtime.PlannedCall(name='preview_card_operation',
                                 arguments={'task_action': 'cancel', 'task_entry_id': 'pending-card'})])
    result = await nova_runtime.chat('取消刚才的银行卡操作任务', [], model=model, context=context)
    calls = [item for item in result['tool_calls'] if item['name'] == 'preview_card_operation']
    assert len(calls) == 1 and calls[0]['result']['data']['task_cancelled']
    assert not any(item['name'] == 'list_cards' for item in result['tool_calls'])
    assert (await nova_runtime.inspect_active_tasks(context))['unfinished_execute_tasks'] == []


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
