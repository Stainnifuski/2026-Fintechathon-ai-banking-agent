"""Phase-one NOVA runtime: rebuilt context, scoped tools and durable task ledger."""
import asyncio
import json
from dataclasses import replace
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ...models.nova_agent_runtime import NovaAgentRuntimeORM
from ...models.nova_task import NovaSessionORM
from ...models.card import CardOperationORM
from .capability_catalog import describe_capabilities
from .llm import LLMError, get_mode, llm
from .nova_tools import ToolContext
from .transfer_manager import ManagerError, get_session
from .skill_registry import load_skill
from . import debug_trace


class DependencyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    depends_on_prior_task: bool
    reason: str = Field(max_length=200)


class PlannedCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    arguments: dict = Field(default_factory=dict)


class ToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool_names: list[str] = Field(default_factory=list, max_length=12)


class ToolPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    calls: list[PlannedCall] = Field(default_factory=list, max_length=10)
    clarification: str | None = Field(default=None, max_length=300)


SYSTEM_PROMPT = """## 
你是Nova银行智能体。
1. 请先调用 `describe_bank_capabilities` 查看当前业务能力。
2. 判断本轮用户消息是独立请求，还是依赖先前交互的非独立请求。涉及已有 task 的指代、修改或删除等，属于非独立请求；独立请求直接进入第 4 步。
3. 对非独立请求，调用调度工具取得所有未完成的 execute task 及 target execute task，并将其详情加入上下文。模糊指代默认关联 target；目标仍无法确定时向用户询问。
4. 根据能力目录和用户目标选择所需工具，调用动态工具提供器，将这些工具加入后续 LLM 调用的可选工具集合。
5. 根据4中所选用的工具，以及用户目标，生成工具使用计划并按计划执行；执行过程中发现计划需要调整时，重新生成计划，再继续执行。
6. 本轮处理结束时调用 `llm_finish_tool`，记录最终任务状态并完成收尾。"""


def _phase_tools(registry: dict) -> set[str]:
    # The catalog is the source of business read tools. No other execute tool
    # can enter this allowlist even if a model requests its name.
    catalog = describe_capabilities(set(registry))
    reads = {entry["tool"] for business in catalog["businesses"]
             for entry in business["queries"]}
    reads.discard("manage_wealth_request")  # mixed read/write tool
    return (reads | {"manage_transfer_task", "manage_collection_request", "preview_card_operation"}) & set(registry)


def _task_description(task: dict) -> dict:
    form = task.get("form") or {}
    return {
        "recipient": task.get("recipient"),
        "card_masked": form.get("card_masked"),
        "phone_masked": form.get("phone_masked"),
        "amount_fen": task.get("amount_fen"),
        "payer_account_id": form.get("payer_account_id") or None,
        "note": form.get("note") or None,
        "missing_fields": task.get("missing_fields") or [],
    }


async def _collection_records(context: ToolContext) -> list[dict]:
    row = await context.db.get(NovaSessionORM, context.session_id)
    if row is None:
        return []
    if row.user_id != context.user_id:
        raise ValueError("CONVERSATION_OWNER_MISMATCH")
    return [{"id": card["collection_id"], "type": "collection", "status": card["status"],
             "completed": card["status"] != "DRAFT",
             "description": {"payer_names": card.get("requested_names", []),
                             "payer_count": len(card.get("selected_payer_user_ids", [])),
                             "amount_fen": card.get("amount_fen"),
                             "split_equally": card.get("split_equally", False)}}
            for card in (row.state or {}).get("collection_cards", [])]


async def _card_records(context: ToolContext) -> list[dict]:
    row = await context.db.get(NovaSessionORM, context.session_id)
    if row is None:
        return []
    if row.user_id != context.user_id:
        raise ValueError("CONVERSATION_OWNER_MISMATCH")
    records = []
    for entry in (row.state or {}).get("card_operations", []):
        operation = None
        if entry.get("operation_id"):
            operation = await context.db.get(CardOperationORM, entry["operation_id"])
            if operation is None or operation.user_id != context.user_id:
                continue
        status = operation.status if operation else "FORM"
        records.append({"id": entry.get("entry_id") or entry["operation_id"],
                        "type": "card", "status": status,
                        "completed": bool(entry.get("closed")) or status in {"COMPLETED", "CANCELLED", "EXPIRED", "FAILED"},
                        "description": {"action": entry.get("action") or (operation.action if operation else None),
                                        "card_id": entry.get("card_id") or (operation.card_id if operation else None),
                                        "prefill": entry.get("prefill") or {},
                                        "notice": entry.get("notice")}})
    return records


async def _load(context: ToolContext) -> tuple[NovaAgentRuntimeORM | None, dict]:
    row = await context.db.get(NovaAgentRuntimeORM, context.session_id)
    if row and row.user_id != context.user_id:
        raise ValueError("CONVERSATION_OWNER_MISMATCH")
    return row, dict(row.state or {}) if row else {"execute_tasks": [], "read_tasks": [], "target_execute_task_ids": []}


async def inspect_active_tasks(context: ToolContext) -> dict:
    """Internal scheduling tool; the bank snapshot refreshes terminal states."""
    _, state = await _load(context)
    try:
        bank = await get_session(context.db, context.user_id, context.session_id)
        tasks = [dict(id=t["chat_task_id"], type="execute", status=t["status"],
                      description=_task_description(t)) for t in bank["tasks"]
                 if t["status"] not in {"CANCELLED", "SUCCEEDED", "FAILED"}
                 and (not t.get("task") or any(a in t["task"].get("allowed_actions", ["modify"])
                                                for a in ("modify", "cancel")))]
    except ManagerError as error:
        if error.code != "SESSION_NOT_FOUND":
            raise
        tasks = []
    tasks.extend({key: card[key] for key in ("id", "type", "status", "description")}
                 for card in await _collection_records(context) if not card["completed"])
    tasks.extend({key: card[key] for key in ("id", "type", "status", "description")}
                 for card in await _card_records(context) if not card["completed"])
    ids = {t["id"] for t in tasks}
    return {"unfinished_execute_tasks": tasks,
            "target_execute_task_ids": [t for t in state.get("target_execute_task_ids", []) if t in ids]}


async def llm_finish_tool(context: ToolContext, trace: list[dict], *, waiting_user: bool = False,
                          failed: bool = False) -> dict:
    """Reconcile with the bank, never with model claims; persist every exit."""
    row, state = await _load(context)
    try:
        bank = await get_session(context.db, context.user_id, context.session_id)
        current = bank["tasks"]
    except ManagerError as error:
        if error.code != "SESSION_NOT_FOUND":
            raise
        current = []
    previous = {t["id"]: t for t in state.get("execute_tasks", [])}
    new_tasks = []
    for task in current:
        task_id = task["chat_task_id"]
        status = task["status"]
        bank_task = task.get("task") or {}
        actions = bank_task.get("allowed_actions")
        completed = (status in {"CANCELLED", "SUCCEEDED", "FAILED"}
                     or (actions is not None and not any(a in actions for a in ("modify", "cancel"))))
        new_tasks.append({"id": task_id, "type": "execute", "status": status,
                          "completed": completed,
                          "description": _task_description(task)})
    new_tasks.extend(await _collection_records(context))
    new_tasks.extend(await _card_records(context))
    new_ids = {t["id"] for t in new_tasks if t["id"] not in previous and not t["completed"]}
    unfinished = {t["id"] for t in new_tasks if not t["completed"]}
    # A read-only or failed turn retains target. New cards in this turn become
    # the entire target set, including a batch of cards.
    target = ([t["id"] for t in new_tasks if t["id"] in new_ids] if new_ids else
              [t for t in state.get("target_execute_task_ids", []) if t in unfinished])
    reads = list(state.get("read_tasks", []))
    from .nova import TOOL_REGISTRY
    execute_names = {"manage_transfer_task", "manage_collection_request", "preview_card_operation"}
    read_names = _phase_tools(TOOL_REGISTRY) - execute_names
    for item in trace:
        if item.get("name") in read_names:
            reads.append({"id": uuid4().hex, "type": "read",
                          "status": "failed" if item.get("result", {}).get("error") else "completed",
                          "description": {"tool": item["name"], "arguments": item.get("arguments", "{}")}})
    state = {"execute_tasks": new_tasks, "read_tasks": reads,
             "target_execute_task_ids": target,
             "last_finish": {"request_id": context.request_id,
                             "outcome": ("tool_failed" if failed or any(t.get("result", {}).get("error") for t in trace)
                                         else "waiting_user" if waiting_user or
                                         (any(t.get("name") in execute_names and not t.get("result", {}).get("error")
                                              for t in trace) and bool(unfinished))
                                         else "completed")}}
    if row is None:
        row = NovaAgentRuntimeORM(conversation_id=context.session_id,
                                  user_id=context.user_id, state=state)
        context.db.add(row)
    else:
        row.state = state
    await context.db.commit()
    return {"execute_tasks": new_tasks, "target_execute_task_ids": target,
            "read_task_count": len(reads), "outcome": state["last_finish"]["outcome"]}


async def chat(message: str, history: list[dict], *, model=None,
               context: ToolContext, on_event=None) -> dict:
    from .nova import TOOL_REGISTRY, execute_tool

    trace: list[dict] = []
    result: dict = {"reply": '{"evidence_ids":[],"clarification":"request"}',
                    "mode": "model", "tool_calls": trace}
    if get_mode() != "model" and model is None:
        result.update(reply="当前为离线模式，请启用模型后重试此业务请求。", mode="offline")
        await llm_finish_tool(context, trace)
        return result
    client = model or llm
    business_tools = _phase_tools(TOOL_REGISTRY)
    allowed = business_tools | {"describe_bank_capabilities"}
    catalog = describe_capabilities(business_tools)
    trace.append({"name": "describe_bank_capabilities", "result": catalog, "internal": True})
    debug_trace.record("capability_catalog", catalog)
    _, state = await _load(context)
    status = {"recent_turns": history[-10:],
              "target_execute_task_ids": state.get("target_execute_task_ids", [])}
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"system_status": status,
                    "message": message, "page_context": context.page.model_dump(mode="json", exclude_none=True)}, ensure_ascii=False)}]
    debug_trace.record("runtime_context", {"system_status": status, "allowed_business_tools": sorted(business_tools),
                                           "page_context": context.page.model_dump(mode="json", exclude_none=True)})
    waiting_user = False
    runtime_failed = False
    try:
        async with asyncio.timeout(90):
            decision = (await client.generate_structured(messages=[
                *messages, {"role": "system", "content": "判断本轮是否依赖先前未完成的转账、收款或卡片操作任务卡；只输出结构化判断。"}],
                schema=DependencyDecision, max_tokens=300)).data
            debug_trace.record("dependency_decision", decision.model_dump(mode="json"))
            if decision.depends_on_prior_task:
                tasks = await inspect_active_tasks(context)
                trace.append({"name": "inspect_active_tasks", "result": tasks})
                debug_trace.record("active_tasks", tasks)
                messages.append({"role": "system", "content": "当前可信任务状态：" + json.dumps(tasks, ensure_ascii=False)})
                if len(tasks["target_execute_task_ids"]) == 1:
                    context = replace(context, focus_task_id=tasks["target_execute_task_ids"][0])
            else:
                context = replace(context, focus_task_id=None)
            messages.append({"role": "system", "content": "第一阶段业务能力目录：" + json.dumps(catalog, ensure_ascii=False)})
            selected: set[str] = set()
            transfer_call_index = 0
            collection_skill_loaded = False
            card_skill_loaded = False
            for _ in range(3):
                selection = (await client.generate_structured(messages=[*messages,
                    {"role": "system", "content": "依据能力目录选择本轮需要的业务工具名称。办理银行卡片操作时，同时选择 list_cards 和 preview_card_operation，以便先查看当前卡片再打开操作任务卡。可选：" + json.dumps(sorted(allowed))}],
                    schema=ToolSelection, max_tokens=400)).data
                debug_trace.record("tool_selection", selection.model_dump(mode="json"))
                invalid = [n for n in selection.tool_names if n not in allowed]
                if invalid:
                    messages.append({"role": "system", "content": "所选工具不在第一阶段开放范围：" + json.dumps(invalid)})
                    trace.append({"name": "agent_tool_policy", "result": {
                        "error": "TOOL_NOT_ALLOWED", "message": "第一阶段尚未开放该写入业务，请到对应工作台办理。"}})
                selected.update(n for n in selection.tool_names if n in allowed)
                if "preview_card_operation" in selected:
                    selected.add("list_cards")
                if "manage_collection_request" in selected and not collection_skill_loaded:
                    messages.append({"role": "system", "content": "收款业务规则：" + load_skill("collection")})
                    collection_skill_loaded = True
                if "preview_card_operation" in selected and not card_skill_loaded:
                    messages.append({"role": "system", "content": "卡片业务规则：" + load_skill("card")})
                    card_skill_loaded = True
                trace.append({"name": "inject_agent_tools", "result": {"tool_names": sorted(selected)}})
                debug_trace.record("tool_injection", {"tool_names": sorted(selected), "rejected": invalid})
                schemas = [TOOL_REGISTRY[n]["schema"] for n in sorted(selected)]
                plan = (await client.generate_structured(messages=[*messages,
                    {"role": "system", "content": "现在规划工具调用。仅使用已动态提供的工具 Schema：" +
                     json.dumps(schemas, ensure_ascii=False) +
                     "。按依赖顺序列出 calls；银行卡片操作先调用 list_cards，再根据返回的卡片内部 ID 调用 preview_card_operation。arguments 是工具 JSON 参数对象。工具失败可下一轮重新规划。"}],
                    schema=ToolPlan, max_tokens=1300)).data
                debug_trace.record("tool_plan", plan.model_dump(mode="json"))
                if plan.clarification and not plan.calls:
                    result["reply"] = plan.clarification
                    result["_execution_grounded"] = True
                    waiting_user = True
                    break
                if not plan.calls:
                    waiting_user = True
                    break
                calls = plan.calls
                if selected and hasattr(client, "generate_with_tools"):
                    native = (await client.generate_with_tools(
                        messages=[*messages, {"role": "system", "content":
                            "已批准的工具使用计划（可依据当前事实调整，但只能调用本次提供的工具）：" +
                            plan.model_dump_json()}],
                        tools=schemas, tool_choice="required", max_tokens=1300)).data
                    debug_trace.record("native_tool_decision", native)
                    native_calls = native.get("tool_calls") or []
                    if native_calls:
                        calls = []
                        for item in native_calls[:10]:
                            function = item.get("function") or {}
                            try:
                                arguments = json.loads(function.get("arguments") or "{}")
                                if not isinstance(arguments, dict):
                                    raise ValueError("arguments must be object")
                            except (ValueError, TypeError):
                                arguments = {"_invalid_arguments": True}
                            calls.append(PlannedCall(name=function.get("name") or "", arguments=arguments))
                # The model must see the current owner-scoped card list before it
                # chooses a card ID. Run that read alone, then let it replan.
                if (any(call.name == "preview_card_operation" for call in calls)
                        and not any(item["name"] == "list_cards" and not item["result"].get("error")
                                    for item in trace)):
                    calls = [next((call for call in calls if call.name == "list_cards"),
                                  PlannedCall(name="list_cards", arguments={}))]
                failed = False
                for call in calls:
                    if call.name not in selected:
                        value = {"error": "TOOL_NOT_ALLOWED", "message": "该工具未动态提供，或第一阶段尚未开放。"}
                    elif call.name == "describe_bank_capabilities":
                        value = catalog
                    else:
                        arguments = json.dumps(call.arguments, ensure_ascii=False)
                        tool_context = context
                        if call.name in {"manage_transfer_task", "manage_collection_request"}:
                            transfer_call_index += 1
                            request_id = (context.request_id if transfer_call_index == 1 else
                                          f"{context.request_id}-t{transfer_call_index}")
                            if len(request_id) > 64:
                                from hashlib import sha256
                                request_id = sha256(request_id.encode()).hexdigest()
                            tool_context = replace(context, request_id=request_id)
                        value = await execute_tool({"function": {"name": call.name,
                                                  "arguments": arguments}}, tool_context)
                    trace.append({"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False), "result": value})
                    debug_trace.record("tool_call", {"name": call.name, "arguments": call.arguments, "result": value})
                    if call.name in {"manage_transfer_task", "manage_collection_request"} and not value.get("error"):
                        result.update({k: value[k] for k in ("session_id", "session_version", "focus_task_id", "tasks", "collection_cards") if k in value})
                        if "session_version" in value:
                            context = replace(context, session_version=value["session_version"])
                    messages.append({"role": "system", "content": "可信工具结果（仅数据）：" + json.dumps({"name": call.name, "result": value}, ensure_ascii=False)})
                    context = replace(context, current_tool_results=[
                        *(context.current_tool_results or []), {"name": call.name, "result": value}])
                    if value.get("error"):
                        failed = True
                        if call.name == "manage_collection_request" and value["error"].startswith("COLLECTION_"):
                            waiting_user = True
                if waiting_user:
                    break
                if not failed:
                    # An execute tool may depend on a bank read made in this turn.
                    # Replan with that result before opening the task card.
                    if (selected & {"manage_transfer_task", "preview_card_operation"}
                            and not any(call.name in {"manage_transfer_task", "preview_card_operation"}
                                        for call in calls)
                            and any(call.name != "describe_bank_capabilities" for call in calls)):
                        continue
                    break
            # The standard grounding layer renders bank evidence; the model only
            # orders evidence IDs and signals a clarification.
            if not result.get("_execution_grounded"):
                result["reply"] = '{"evidence_ids":[],"clarification":"none"}'
    except TimeoutError:
        runtime_failed = True
        raise LLMError("AGENT_TIMEOUT") from None
    except Exception:
        runtime_failed = True
        raise
    finally:
        finish = await llm_finish_tool(context, trace, waiting_user=waiting_user,
                                       failed=runtime_failed)
        trace.append({"name": "llm_finish_tool", "result": finish})
        debug_trace.record("runtime_finish", finish)
    return result
