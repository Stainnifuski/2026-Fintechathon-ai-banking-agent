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
from .capability_catalog import describe_capabilities, visible_business_tools
from .llm import LLMError, get_mode, llm
from .nova_tools import ToolContext
from .transfer_manager import ManagerError, get_session
from .skill_registry import load_skill
from . import debug_trace, task_graph, task_graph_status, planning_knowledge


class DependencyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    depends_on_prior_task: bool
    reason: str = Field(max_length=200)
    planning_mode: Literal['none', 'offer', 'explain', 'start'] = Field(default='none',
        description='none其他具体功能；offer确有办理投资意图且全部现有具体功能均无法匹配；explain规划或配置模型问答；start本人明确要求或确认开始规划。')
    start_quote: str | None = Field(default=None, max_length=300,
        description='start时逐字引用本轮用户明确要求开始或确认进入规划的原文，不能引用助手或历史消息。')
    investment_function: Literal['none','category_recommendation','name_recommendation','purchase',
        'redemption','positions','returns','assessment','detail','compare','planning'] = Field(default='none',
        description='逐项核对能力目录后，本轮投资需求可由哪个现有功能承接。类别即使尚未选定产品仍用category_recommendation，不能判为none。')
    investment_category: Literal['quick_redeem','bank_wealth','bond_fund','mixed_fund','equity_fund'] | None = None
    investment_action_quote: str | None = Field(default=None,max_length=300,
        description='仅当确认本人本轮希望实际办理投资操作时，逐字引用该意图原文；纯知识、假设、犹豫或否定留空。')
    unmatched_investment_reason: str | None = Field(default=None,max_length=300,
        description='仅investment_function=none且确有办理投资的意图时，解释为何全部已开放投资功能都不能匹配。信息缺失不等于功能不匹配。')


def may_offer_planning(decision: DependencyDecision, message: str) -> bool:
    """Require model-assessed action intent and no supported investment operation."""
    return bool(decision.planning_mode == 'offer' and decision.investment_function == 'none'
                and decision.unmatched_investment_reason and decision.investment_action_quote
                and decision.investment_action_quote in message)


class PlannedCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    arguments: dict = Field(default_factory=dict)


class ToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool_names: list[str] = Field(default_factory=list, max_length=12)
    collection_action: Literal["create", "update", "cancel"] | None = Field(
        default=None, description="本轮若要新建、修改或撤销向别人收款的草稿，填写对应动作；查看已有收款申请时留空")


class ToolPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    calls: list[PlannedCall] = Field(default_factory=list, max_length=10)
    clarification: str | None = Field(default=None, max_length=300)


WEALTH_TOOLS = {'assess_wealth_risk','plan_wealth','manage_wealth_positions','show_wealth_returns'}


SYSTEM_PROMPT = """## 
你是Nova银行智能体。
1. 请先调用 `describe_bank_capabilities` 查看当前业务能力。
2. 判断本轮用户消息是独立请求，还是依赖先前交互的非独立请求。涉及已有 task 的指代、修改或删除等，属于非独立请求；明确新建另一张任务卡时，即使当前有未完成任务，也属于独立请求。独立请求直接进入第 4 步。
3. 对非独立请求，调用调度工具取得所有未完成的 execute task 及 target execute task，并将其详情加入上下文。模糊指代默认关联 target；目标仍无法确定时向用户询问。
4. 根据能力目录和用户目标选择所需工具，调用动态工具提供器，将这些工具加入后续 LLM 调用的可选工具集合。
5. 根据4中所选用的工具，以及用户目标，生成工具使用计划并按计划执行；执行过程中发现计划需要调整时，重新生成计划，再继续执行。
6. 本轮处理结束时调用 `llm_finish_tool`，记录最终任务状态并完成收尾。"""
SYSTEM_PROMPT += "\n未完成任务卡可以同时保留，包括同一业务的多张卡。用户明确新建另一项操作时调用对应工具，不结束先前任务；修改或取消旧卡时用可信任务状态确定目标。引用先前卡片的日期或时间时先读取可信任务状态。"


def _phase_tools(registry: dict) -> set[str]:
    # The catalog is the source of business read tools. No other execute tool
    # can enter this allowlist even if a model requests its name.
    catalog = describe_capabilities(set(registry))
    reads = {entry["tool"] for business in catalog["businesses"]
             for entry in business["queries"]}
    return (reads | {"manage_transfer_task", "manage_collection_request",
                     "manage_payment_requests",
                     "preview_card_operation", "prepare_bill_change", "manage_subscription_task", "manipulate_calender_event",
                     "prepare_shop_purchase", "manage_scheduled_task", *WEALTH_TOOLS}) & set(registry)


def _task_description(task: dict) -> dict:
    form = task.get("form") or {}
    return {
        "recipient": task.get("recipient"),
        "card_masked": form.get("card_masked"),
        "phone_masked": form.get("phone_masked"),
        "amount_fen": task.get("amount_fen"),
        "payer_account_id": form.get("payer_account_id") or None,
        "note": form.get("note") or None,
        "execution_mode": form.get("execution_mode", "instant"),
        "scheduled_at": form.get("scheduled_at"),
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


async def _subscription_records(context: ToolContext) -> list[dict]:
    row = await context.db.get(NovaSessionORM, context.session_id)
    if row is None:
        return []
    if row.user_id != context.user_id:
        raise ValueError("CONVERSATION_OWNER_MISMATCH")
    entries = (row.state or {}).get('subscription_tasks', [])
    if not entries:
        return []
    from .card_subscription_tools import subscription_rows
    subscriptions = {item['subscription_id']: item for item in await subscription_rows(context)}
    records = []
    for entry in entries:
        subscription = subscriptions.get(entry.get('subscription_id'))
        status = subscription['status'] if subscription else 'FORM'
        records.append({'id': entry['entry_id'], 'type': 'subscription', 'status': status,
                        'completed': bool(entry.get('closed')) or status == 'CANCELLED',
                        'description': {'subscription_id': entry.get('subscription_id'),
                                        'merchant_name': subscription['merchant_name'] if subscription else
                                                         entry.get('merchant_name_hint')}})
    return records


async def _calendar_records(context: ToolContext) -> list[dict]:
    row = await context.db.get(NovaSessionORM, context.session_id)
    if row is None:
        return []
    if row.user_id != context.user_id:
        raise ValueError('CONVERSATION_OWNER_MISMATCH')
    return [{'id': entry['entry_id'], 'type': 'calendar', 'status': entry.get('status', 'PENDING'),
             'completed': entry.get('closed', False) or entry.get('status') == 'COMPLETED',
             'description': {'action': entry.get('action'), 'targets': entry.get('targets', []),
                             'changes': entry.get('changes', {})}}
            for entry in (row.state or {}).get('calendar_tasks', []) if entry.get('kind') == 'mutation']


async def _shop_records(context: ToolContext) -> list[dict]:
    row = await context.db.get(NovaSessionORM, context.session_id)
    if row is None:
        return []
    if row.user_id != context.user_id:
        raise ValueError('CONVERSATION_OWNER_MISMATCH')
    from .. import shop_reads
    records = []
    for entry in (row.state or {}).get('shop_tasks', []):
        order = None
        if entry.get('shop') and entry.get('order_id'):
            try:
                order = (await shop_reads.orders(context.db, context.user_id, entry['shop'],
                                                order_id=entry['order_id']))[0]
            except shop_reads.ShopReadError:
                pass
        status = order['status'] if order else 'FORM'
        records.append({'id': entry['entry_id'], 'type': 'shop', 'status': status,
                        'completed': bool(entry.get('closed')) or status in {
                            'COMPLETED', 'PAYMENT_CLOSED', 'REFUNDED', 'PARTIALLY_REFUNDED'},
                        'description': {'shop': entry.get('shop'), 'prefill': entry.get('prefill') or {},
                                        'order_id': entry.get('order_id'),
                                        'product_name': order['product_name'] if order else None}})
    return records


async def _payment_request_records(context: ToolContext) -> list[dict]:
    row = await context.db.get(NovaSessionORM, context.session_id)
    if row is None:
        return []
    if row.user_id != context.user_id:
        raise ValueError("CONVERSATION_OWNER_MISMATCH")
    from .payment_request_tools import chat_payment_request_tasks
    cards = await chat_payment_request_tasks(context.user_id, row.state or {})
    return [{'id': card['entry_id'], 'type': 'payment_request', 'status': card['action'],
             'completed': not any(request['status'] == 'PENDING' if card['action'] == 'cancel' else
                                  request['status'] in {'PENDING', 'ACCEPTED'}
                                  for request in card['requests']),
             'description': {'action': card['action'], 'request_count': len(card['requests']),
                             'request_ids': [request['payment_request_id'] for request in card['requests']]}}
            for card in cards]


async def _wealth_records(context: ToolContext) -> list[dict]:
    from .wealth_agent import cards
    from .wealth_steps import sequence,effective_kind
    from .execution_queue import wealth_state
    current = await cards(context.db, context.user_id, context.session_id)
    active_kinds = {'planning', 'recommend', 'purchase', 'positions', 'redeem', 'assessment', 'clarify'}
    records = []
    for card in current:
        if card.get('parent_task_id') or (card.get('command') or {}).get('kind') not in active_kinds:
            continue
        completed = bool(card.get('closed'))
        if not completed and (card.get('execution_plan_id') or card['command']['kind']=='assessment'):
            completed = await wealth_state(context, card['task_id'], 'settled') == 'completed'
        view=card.get('view_context') or {}
        completed_stages=view.get('completed_stages',[])
        stage=next((item for item in sequence(effective_kind(card)) if item not in completed_stages),'trade')
        records.append({'id': card['task_id'], 'type': 'wealth',
                        'status': 'PAUSED' if card.get('paused') else 'COMPLETED' if completed else 'OPEN',
                        'completed': completed,
                        'description': {'kind': card['command']['kind'],
                                        'stage': stage,
                                        'completed_stages': completed_stages,
                                        'visible_products': view.get('visible_products',[])[:60],
                                        'selected_products': view.get('selected_products',[])[:160],
                                        'cash_field': view.get('cash_field'),
                                        'cash_yuan': view.get('cash_yuan'),
                                        'amounts_yuan': view.get('amounts_yuan',{}),
                                        'risk_answered_questions': list((view.get('risk_answers') or {}).keys()),
                                        'assessment_pending': bool(card.get('assessment_pending')),
                                        'paused': bool(card.get('paused'))}})
    return records


async def _load(context: ToolContext) -> tuple[NovaAgentRuntimeORM | None, dict]:
    row = await context.db.get(NovaAgentRuntimeORM, context.session_id)
    if row and row.user_id != context.user_id:
        raise ValueError("CONVERSATION_OWNER_MISMATCH")
    return row, dict(row.state or {}) if row else {"execute_tasks": [], "read_tasks": [], "target_execute_task_ids": []}


def _transfer_chat_completed(task: dict) -> bool:
    """A confirmed reservation finishes the chat workflow before bank execution."""
    status = task["status"]
    bank_task = task.get("task") or {}
    actions = bank_task.get("allowed_actions")
    return (status in {"CANCELLED", "SUCCEEDED", "FAILED"}
            or (bank_task.get("phase") == "WAITING_EXECUTION" and bool(bank_task.get("scheduled_at")))
            or (actions is not None and not any(action in actions for action in ("modify", "cancel"))))


async def inspect_active_tasks(context: ToolContext) -> dict:
    """Internal scheduling tool; the bank snapshot refreshes terminal states."""
    _, state = await _load(context)
    try:
        bank = await get_session(context.db, context.user_id, context.session_id)
        tasks = [dict(id=t["chat_task_id"], type="execute", status=t["status"],
                      description=_task_description(t)) for t in bank["tasks"]
                 if not _transfer_chat_completed(t)]
    except ManagerError as error:
        if error.code != "SESSION_NOT_FOUND":
            raise
        tasks = []
    tasks.extend({key: card[key] for key in ("id", "type", "status", "description")}
                 for card in await _collection_records(context) if not card["completed"])
    tasks.extend({key: card[key] for key in ("id", "type", "status", "description")}
                 for card in await _card_records(context) if not card["completed"])
    tasks.extend({key: task[key] for key in ("id", "type", "status", "description")}
                 for task in await _subscription_records(context) if not task["completed"])
    tasks.extend({key: task[key] for key in ('id', 'type', 'status', 'description')}
                 for task in await _calendar_records(context) if not task['completed'])
    tasks.extend({key: task[key] for key in ('id', 'type', 'status', 'description')}
                 for task in await _shop_records(context) if not task['completed'])
    tasks.extend({key: task[key] for key in ('id', 'type', 'status', 'description')}
                 for task in await _payment_request_records(context) if not task['completed'])
    tasks.extend({key: task[key] for key in ('id', 'type', 'status', 'description')}
                 for task in await _wealth_records(context) if not task['completed'])
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
        completed = _transfer_chat_completed(task)
        new_tasks.append({"id": task_id, "type": "execute", "status": status,
                          "completed": completed,
                          "description": _task_description(task)})
    new_tasks.extend(await _collection_records(context))
    new_tasks.extend(await _card_records(context))
    new_tasks.extend(await _subscription_records(context))
    new_tasks.extend(await _calendar_records(context))
    new_tasks.extend(await _shop_records(context))
    new_tasks.extend(await _payment_request_records(context))
    new_tasks.extend(await _wealth_records(context))
    new_ids = {t["id"] for t in new_tasks if t["id"] not in previous and not t["completed"]}
    unfinished = {t["id"] for t in new_tasks if not t["completed"]}
    # A read-only or failed turn retains target. New cards in this turn become
    # the entire target set, including a batch of cards.
    target = ([t["id"] for t in new_tasks if t["id"] in new_ids] if new_ids else
              [t for t in state.get("target_execute_task_ids", []) if t in unfinished])
    reads = list(state.get("read_tasks", []))
    from .nova import TOOL_REGISTRY
    execute_names = {"manage_transfer_task", "manage_collection_request", "prepare_bill_change",
                     "manage_payment_requests", *WEALTH_TOOLS,
                     "preview_card_operation", "manage_subscription_task", "manipulate_calender_event",
                     "prepare_shop_purchase", "manage_scheduled_task"}
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


async def _save_graph(context: ToolContext, graph: dict) -> dict | None:
    from ..bank import nova_task_graphs
    from ..bank.nova_conversations import ConversationError
    try:
        saved = await nova_task_graphs.save(context.db, context.user_id, context.session_id,
                                            context.request_id, graph)
        await context.db.commit()
    except ConversationError:
        return task_graph.public(graph)
    debug_trace.record("task_graph", saved)
    return task_graph.public(saved)


async def _graph_cards(context: ToolContext) -> dict | None:
    """The graph is presentation only: a failure to read cards drops it, never the turn."""
    try:
        return await task_graph_status.card_states(context)
    except Exception:
        task_graph.logger.exception("NOVA task graph card states unavailable")
        return None


async def _finish_graph(context: ToolContext, graph: dict, outcome: str, cards_before: set[str],
                        result: dict) -> None:
    states = await _graph_cards(context)
    if states is None:
        return
    try:
        graph = task_graph_status.link(task_graph.settle(graph, outcome), cards_before, states)
        saved = await _save_graph(context, graph)
    except Exception:
        task_graph.logger.exception("NOVA task graph not saved")
        return
    if saved["complex"]:
        result["task_graph"] = task_graph_status.derive(saved, states)


async def chat(message: str, history: list[dict], *, model=None,
               context: ToolContext, on_event=None) -> dict:
    from .nova import TOOL_REGISTRY, execute_tool

    trace: list[dict] = []
    result: dict = {"reply": "", "mode": "model", "tool_calls": trace}
    if get_mode() != "model" and model is None:
        result.update(reply="当前为离线模式，请启用模型后重试此业务请求。", mode="offline")
        await llm_finish_tool(context, trace)
        return result
    client = model or llm
    business_tools = _phase_tools(TOOL_REGISTRY)
    allowed = business_tools | {"describe_bank_capabilities"}
    catalog = describe_capabilities(business_tools)
    progress_tools = visible_business_tools(catalog)
    trace.append({"name": "describe_bank_capabilities", "result": catalog, "internal": True})
    debug_trace.record("capability_catalog", catalog)
    active_tasks = await inspect_active_tasks(context)
    status = {"recent_turns": history[-10:],
              "target_execute_task_ids": active_tasks["target_execute_task_ids"],
              "wealth_focus": {"task_id": context.wealth_task_id, "stage": context.wealth_stage}
              if context.wealth_task_id else None}
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"system_status": status,
                    "message": message, "page_context": context.page.model_dump(mode="json", exclude_none=True)}, ensure_ascii=False)}]
    debug_trace.record("runtime_context", {"system_status": status, "allowed_business_tools": sorted(business_tools),
                                           "page_context": context.page.model_dump(mode="json", exclude_none=True)})
    waiting_user = False
    runtime_failed = False
    graph = None
    cards_before: set[str] = set()
    try:
        async with asyncio.timeout(90):
            decision = (await client.generate_structured(messages=[
                *messages, {"role": "system", "content": "判断本轮是否依赖先前未完成的转账、收款、理财、银行卡、订阅、日历或店铺订单任务卡；只输出结构化判断。\n" + planning_knowledge.ROUTING_RULES + '\n当前业务能力目录：' + json.dumps(catalog, ensure_ascii=False)}],
                schema=DependencyDecision, max_tokens=550)).data
            debug_trace.record("dependency_decision", decision.model_dump(mode="json"))
            planning_confirmed = (decision.planning_mode == 'start' and bool(decision.start_quote)
                                  and decision.start_quote in message)
            offer_allowed = may_offer_planning(decision, message)
            if decision.planning_mode == 'explain' or offer_allowed:
                result['reply'] = await planning_knowledge.explain(client, message, history,
                    offer=offer_allowed, local_data={'active_tasks': active_tasks})
                return result
            if decision.planning_mode == 'start' and not planning_confirmed:
                result['reply'] = '未能核实本轮开始规划的确认，请明确是否要启动理财规划；目前没有创建规划任务。'
                return result
            messages.append({'role':'system','content':'本轮已核验的投资功能匹配：' + json.dumps({
                'function':decision.investment_function,'category':decision.investment_category,
                'planning_offer_allowed':offer_allowed,'planning_start_confirmed':planning_confirmed},ensure_ascii=False) +
                '。已有具体投资功能优先，缺产品或金额仍用相应表单补充；不得把缺参数、未评估或未选产品当成规划引导理由。'
                'planning_offer_allowed=false时不得主动建议进入规划或输出进入按钮。'
                '只有planning_start_confirmed=true时可按本人确认调用kind=planning新建规划。'})
            if decision.depends_on_prior_task:
                tasks = await inspect_active_tasks(context)
                trace.append({"name": "inspect_active_tasks", "result": tasks})
                debug_trace.record("active_tasks", tasks)
                messages.append({"role": "system", "content": "当前可信任务状态：" + json.dumps(tasks, ensure_ascii=False)})
                from .task_flow import focus
                context = await focus(context)
                if len(tasks["target_execute_task_ids"]) == 1:
                    target = next((task for task in tasks['unfinished_execute_tasks']
                                   if task['id'] == tasks['target_execute_task_ids'][0]), None)
                    if target and target['type'] == 'wealth' and not context.wealth_task_id:
                        context = replace(context, wealth_task_id=target['id'])
                    elif target and target['type'] == 'execute' and not context.focus_task_id:
                        context = replace(context, focus_task_id=target['id'])
                    elif target and target['type'] in {'collection', 'card', 'subscription', 'calendar', 'shop', 'payment_request'}:
                        context = replace(context, active_entry_type=target['type'], active_entry_id=target['id'])
            else:
                context = replace(context, focus_task_id=None, wealth_task_id=None, wealth_stage=None,
                                  active_entry_type=None, active_entry_id=None)
            messages.append({"role": "system", "content": "第一阶段业务能力目录：" + json.dumps(catalog, ensure_ascii=False)})
            if task_graph.enabled():
                # Phase one plans and shows the graph; tool execution below is unchanged.
                from ..bank import nova_task_graphs
                previous = await nova_task_graphs.latest(context.db, context.user_id, context.session_id)
                graph = await task_graph.plan(client, messages, catalog, previous, context.request_id)
                if graph is not None:
                    before = await _graph_cards(context)
                    graph, cards_before = (None, set()) if before is None else (graph, set(before))
                # A single chain is a simple task: kept for revisions, not shown.
                if graph is not None and graph["complex"] and on_event is not None:
                    await on_event({"type": "plan", "graph": task_graph_status.derive(task_graph.public(graph), {})})
            selected: set[str] = set()
            collection_create_requested = False
            transfer_call_index = 0
            collection_skill_loaded = False
            transfer_skill_loaded = False
            card_skill_loaded = False
            subscription_skill_loaded = False
            calendar_skill_loaded = False
            shop_skill_loaded = False
            scheduled_skill_loaded = False
            wealth_skill_loaded = False
            bills_skill_loaded = False
            calendar_query_skill_loaded = False
            calendar_search_empty = False
            calendar_list_after_search = False
            calendar_list_replanned = False
            for _ in range(3):
                selection = (await client.generate_structured(messages=[*messages,
                    {"role": "system", "content": "依据能力目录选择本轮需要的业务工具名称。用户明确要办理转账时选择 manage_transfer_task；收款人、金额等尚未提供也可打开空白转账草稿。理财风险评估选 assess_wealth_risk；规划、推荐、产品详情或申购选 plan_wealth；持仓查询、调整或赎回选 manage_wealth_positions；整体理财收益选 show_wealth_returns。新建理财规划必须已有本轮明确启动确认，宽泛投资愿望先邀请进入。查看或处理现有收款申请使用 manage_payment_requests；新建、修改或撤销向别人收款的草稿时填写 collection_action 并选择 manage_collection_request，查联系人后本轮仍须调用该办理工具。仅查看或处理已发出的收款申请使用 manage_payment_requests。打开或修改银行卡片任务时同时选择 list_cards 和 preview_card_operation；取消已有卡片任务可直接使用 preview_card_operation。用户要添加、修改或删除日程时必须选择 manipulate_calender_event；字段不全也可打开任务卡，不要只选择 get_current_time。用户要让 NOVA 到某个时间再办理一件事，或要为这类任务锁定、预留资金时选择 manage_scheduled_task；查看定时任务选择 list_scheduled_tasks；只在日历记一条安排才用日历工具，到时自动转账用 manage_transfer_task。用户要在花店或蛋糕店购买商品时选择 prepare_shop_purchase；商品或数量未确定也可打开订单表单，不要查完商品就结束。日历事件搜索无结果时继续选择 list_calendar_events 查看相关日期的活动。可选：" + json.dumps(sorted(allowed))}],
                    schema=ToolSelection, max_tokens=400)).data
                debug_trace.record("tool_selection", selection.model_dump(mode="json"))
                invalid = [n for n in selection.tool_names if n not in allowed]
                if invalid:
                    messages.append({"role": "system", "content": "所选工具不在第一阶段开放范围：" + json.dumps(invalid)})
                    trace.append({"name": "agent_tool_policy", "result": {
                        "error": "TOOL_NOT_ALLOWED", "message": "第一阶段尚未开放该写入业务，请到对应工作台办理。"}})
                selected.update(n for n in selection.tool_names if n in allowed)
                if selection.collection_action is not None:
                    selected.add("manage_collection_request")
                    collection_create_requested |= selection.collection_action == "create"
                if "preview_card_operation" in selected:
                    selected.add("list_cards")
                if 'prepare_shop_purchase' in selected:
                    selected.add('list_shop_products')
                if selected & {'search_calendar_events', 'list_calendar_events', 'get_calendar_event'}:
                    selected.update({'search_calendar_events', 'list_calendar_events', 'get_calendar_event'} & allowed)
                if selected & {"manage_collection_request", "manage_payment_requests"} and not collection_skill_loaded:
                    messages.append({"role": "system", "content": "收款业务规则：" + load_skill("collection")})
                    collection_skill_loaded = True
                if "manage_transfer_task" in selected and not transfer_skill_loaded:
                    messages.append({"role": "system", "content": "转账业务规则：" + load_skill("transfer")})
                    transfer_skill_loaded = True
                if selected & WEALTH_TOOLS and not wealth_skill_loaded:
                    messages.append({"role": "system", "content": "理财业务规则：" + load_skill("wealth")})
                    wealth_skill_loaded = True
                if selected & {"get_bill_analysis", "get_bill_catalog", "list_bill_anomalies", "preview_bill_merchant_change", "prepare_bill_change", "get_budget_status", "list_forecast_reviews", "list_recurring_records", "project_cashflow"} and not bills_skill_loaded:
                    messages.append({"role": "system", "content": "账单业务规则：" + load_skill("bills")})
                    bills_skill_loaded = True
                if "preview_card_operation" in selected and not card_skill_loaded:
                    messages.append({"role": "system", "content": "卡片业务规则：" + load_skill("card")})
                    card_skill_loaded = True
                if "manage_subscription_task" in selected and not subscription_skill_loaded:
                    messages.append({"role": "system", "content": "订阅业务规则：" + load_skill("subscription")})
                    subscription_skill_loaded = True
                if 'manipulate_calender_event' in selected and not calendar_skill_loaded:
                    messages.append({'role': 'system', 'content': '日历业务规则：' + load_skill('calendar')})
                    calendar_skill_loaded = True
                if 'prepare_shop_purchase' in selected and not shop_skill_loaded:
                    messages.append({'role': 'system', 'content': '店铺业务规则：' + load_skill('shop')})
                    shop_skill_loaded = True
                if selected & {'manage_scheduled_task', 'list_scheduled_tasks'} and not scheduled_skill_loaded:
                    messages.append({'role': 'system', 'content': '定时任务业务规则：' + load_skill('scheduled')})
                    scheduled_skill_loaded = True
                if selected & {'search_calendar_events', 'list_calendar_events', 'get_calendar_event'} and not calendar_query_skill_loaded:
                    messages.append({'role': 'system', 'content': '日历信息查询规则：' + load_skill('calendar_query')})
                    calendar_query_skill_loaded = True
                trace.append({"name": "inject_agent_tools", "result": {"tool_names": sorted(selected)}})
                debug_trace.record("tool_injection", {"tool_names": sorted(selected), "rejected": invalid})
                schemas = [TOOL_REGISTRY[n]["schema"] for n in sorted(selected)]
                plan = (await client.generate_structured(messages=[*messages,
                    {"role": "system", "content": "现在规划工具调用。仅使用已动态提供的工具 Schema：" +
                     json.dumps(schemas, ensure_ascii=False) +
                     "。按依赖顺序列出 calls；用户要办理转账时调用 manage_transfer_task，明确 action。新建转账即使收款人和金额都未知也调用 action=create，省略未知字段以打开空白任务卡；能力目录查询不能替代任务卡。新建向别人收款时调用 manage_collection_request(action=create)，把已知付款人和金额填入参数；缺少字段也要打开可补全的草稿卡。查联系人后仍须继续调用该工具。修改或撤销收款草稿时调用 manage_collection_request 并指定可信卡片 ID。理财由主模型直接填写工具参数：评估用 assess_wealth_risk，规划或推荐用 plan_wealth，持仓或赎回用 manage_wealth_positions，整体收益用 show_wealth_returns。缺少预算或产品仍打开卡片，交易由本人核对确认。查看或处理现有收款申请调用 manage_payment_requests，取消/支付/拒绝只打开待本人操作的卡片。打开或修改银行卡片任务时先调用 list_cards，再根据返回的卡片内部 ID 调用 preview_card_operation；取消已有任务时使用 task_action=cancel 和未完成任务的 entry_id。日历添加即使缺少事件名称或具体时间也要调用 manipulate_calender_event(action=add) 打开可补全的任务卡；若先查当前时间，本轮查完后继续规划并调用日历操作工具。新建自定义定时任务即使缺少日期或时间也调用 manage_scheduled_task(action=create) 打开可补全的表单；要开始、完成或取消已有定时任务时先 list_scheduled_tasks，再用 action=show 打开其卡片。花店或蛋糕店购买时先查 list_shop_products，再按可信商品 ID 调用 prepare_shop_purchase；缺商品或数量仍须打开可补全订单表单。日历事件按名称或地点搜索无结果时，使用 list_calendar_events 查看相关日期范围的活动，再根据列表规划下一步。arguments 是工具 JSON 参数对象。工具失败可下一轮重新规划。"}],
                    schema=ToolPlan, max_tokens=3000)).data
                debug_trace.record("tool_plan", plan.model_dump(mode="json"))
                if plan.clarification and not plan.calls:
                    result["reply"] = plan.clarification
                    waiting_user = True
                    break
                if not plan.calls and "manage_collection_request" in selected and not plan.clarification:
                    messages.append({"role": "system", "content": "已选择收款草稿办理工具，但尚未生成任务卡。请继续规划 manage_collection_request；新建可使用 action=create 并省略未知字段。"})
                    continue
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
                        if (any(item.name == 'manage_transfer_task' for item in plan.calls)
                                and not any(item.name == 'manage_transfer_task' for item in calls)):
                            calls = plan.calls
                        if (any(item.name == 'manage_collection_request' for item in plan.calls)
                                and not any(item.name == 'manage_collection_request' for item in calls)):
                            calls = plan.calls
                # The model must see the current owner-scoped card list before it
                # chooses a card ID. Run that read alone, then let it replan.
                if (any(call.name == "preview_card_operation" and
                        call.arguments.get("task_action") != "cancel" for call in calls)
                        and not any(item["name"] == "list_cards" and not item["result"].get("error")
                                    for item in trace)):
                    calls = [next((call for call in calls if call.name == "list_cards"),
                                  PlannedCall(name="list_cards", arguments={}))]
                purchase = next((call for call in calls if call.name == 'prepare_shop_purchase'), None)
                shop_hint = (purchase.arguments.get('shop') or (context.page.shop.shop if context.page.shop else None)) if purchase else None
                selected_product = bool(context.page.shop and context.page.shop.shop == shop_hint and
                                        context.page.shop.selected and context.page.shop.selected.type == 'product')
                if (purchase and shop_hint and not selected_product and
                        not any(item['name'] == 'list_shop_products' and not item['result'].get('error')
                                    for item in trace)):
                    calls = [next((call for call in calls if call.name == 'list_shop_products'),
                                  PlannedCall(name='list_shop_products', arguments={'shop': shop_hint}))]
                failed = False
                for call in calls:
                    if (call.name == 'plan_wealth' and call.arguments.get('kind', 'planning') == 'planning'
                            and not call.arguments.get('task_id') and not context.wealth_task_id
                            and not planning_confirmed):
                        graph = None
                        value={'error':'PLANNING_START_NOT_CONFIRMED','message':
                            '用户未确认启动规划。请按已匹配的具体投资功能重新填写工具参数；若需要产品类别/名称推荐，使用kind=recommend，不能改为规划引导。'}
                        trace.append({'name':call.name,'result':value})
                        messages.append({'role':'system','content':json.dumps(value,ensure_ascii=False)})
                        failed=True
                        continue
                    if on_event is not None and call.name in selected and call.name in progress_tools:
                        await on_event({'type': 'tool', 'name': call.name})
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
                    if call.name == 'search_calendar_events' and not value.get('error') and value.get('data', {}).get('total') == 0:
                        calendar_search_empty = True
                    if call.name == 'list_calendar_events' and calendar_search_empty and not value.get('error'):
                        calendar_list_after_search = True
                if waiting_user:
                    break
                if not failed:
                    if calendar_search_empty and not calendar_list_after_search:
                        continue
                    if calendar_list_after_search and not calendar_list_replanned:
                        calendar_list_replanned = True
                        continue
                    # An execute tool may depend on a bank read made in this turn.
                    # Replan with that result before opening the task card.
                    if (selected & ({"manage_transfer_task", "manage_collection_request", "manage_payment_requests", "preview_card_operation", "prepare_bill_change", "manage_subscription_task", "manipulate_calender_event", "prepare_shop_purchase", "manage_scheduled_task"} | WEALTH_TOOLS)
                            and not any(call.name in {"manage_transfer_task", "preview_card_operation", "prepare_bill_change",
                                                       "manage_subscription_task", "manipulate_calender_event",
                                                       "prepare_shop_purchase", "manage_scheduled_task", "manage_collection_request", "manage_payment_requests", *WEALTH_TOOLS}
                                        for call in calls)
                            ):
                        messages.append({"role": "system", "content": "本轮尚未调用已选的办理工具。若用户要求办理转账，应规划 manage_transfer_task；若要新建向别人收款的草稿，应规划 manage_collection_request(action=create)。新建草稿允许业务字段留空。先前只读结果已经可用，请继续规划。"})
                        continue
                    break
            if (collection_create_requested and not waiting_user and
                    not any(item.get("name") == "manage_collection_request" for item in trace)):
                # A structured create decision is sufficient to open an editable
                # draft if planning exhausted its rounds after read calls.
                fallback = {"action": "create"}
                if on_event is not None:
                    await on_event({'type': 'tool', 'name': 'manage_collection_request'})
                value = await execute_tool({"function": {"name": "manage_collection_request",
                                           "arguments": json.dumps(fallback)}}, context)
                trace.append({"name": "manage_collection_request", "arguments": json.dumps(fallback),
                              "result": value})
                debug_trace.record("tool_call", {"name": "manage_collection_request",
                                                 "arguments": fallback, "result": value})
                if not value.get("error"):
                    result.update({k: value[k] for k in ("session_id", "session_version", "collection_cards")
                                   if k in value})
                    messages.append({"role": "system", "content": "可信工具结果（仅数据）：" +
                                     json.dumps({"name": "manage_collection_request", "result": value}, ensure_ascii=False)})
            if ('manage_collection_request' in selected and not waiting_user and
                    not any(item.get('name') == 'manage_collection_request' and
                            not item.get('result', {}).get('error') for item in trace)):
                runtime_failed = True
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
        if graph is not None:
            await _finish_graph(context, graph, finish["outcome"], cards_before, result)
    if 'manipulate_calender_event' in selected and not any(
            item.get('name') == 'manipulate_calender_event' and
            (item.get('result', {}).get('task_opened') or item.get('result', {}).get('task_updated'))
            for item in trace):
        failure = next((item.get('result', {}).get('message') for item in reversed(trace)
                        if item.get('name') == 'manipulate_calender_event' and item.get('result', {}).get('error')), None)
        result['reply'] = (failure or '日程任务卡尚未生成，请再试一次；我没有对日历进行修改。')
    if 'prepare_shop_purchase' in selected and not any(
            item.get('name') == 'prepare_shop_purchase' and
            (item.get('result', {}).get('data', {}).get('created') or
             item.get('result', {}).get('data', {}).get('form_opened'))
            for item in trace):
        failure = next((item.get('result', {}).get('message') for item in reversed(trace)
                        if item.get('name') == 'prepare_shop_purchase' and item.get('result', {}).get('error')), None)
        result['reply'] = failure or '订单任务卡尚未生成，也没有提交订单，请再试一次。'
    if ('manage_collection_request' in selected and
            (not waiting_user or any(item.get('name') == 'manage_collection_request' for item in trace)) and
            not any(
            item.get('name') == 'manage_collection_request' and
            not item.get('result', {}).get('error') and
            'collection_cards' in item.get('result', {}) for item in trace)):
        failure = next((item.get('result', {}).get('message') for item in reversed(trace)
                        if item.get('name') == 'manage_collection_request' and item.get('result', {}).get('error')), None)
        result['reply'] = failure or '收款任务卡尚未生成，请再试一次；目前没有发出收款申请。'
    called_business = [item for item in trace if item.get('name') in business_tools]
    planning_call = next((item for item in called_business if item['name'] == 'plan_wealth'
        and not item.get('result', {}).get('error')
        and json.loads(item.get('arguments', '{}')).get('kind', 'planning') == 'planning'), None)
    planning_intro = None
    if planning_call and finish['outcome'] != 'tool_failed':
        try:
            planning_intro = await planning_knowledge.explain(client, message, history, intro=True,
                continuing=bool(json.loads(planning_call.get('arguments', '{}')).get('task_id') or context.wealth_task_id),
                local_data={'wealth_cards': planning_call['result'].get('wealth_cards', [])})
        except LLMError:
            planning_intro = '规划卡片已打开，知识库解释暂时未能生成。你可以在卡片继续核对，或稍后追问。'
        if len(called_business) == 1:
            result['reply'] = planning_intro
    if (not result['reply'] and len(called_business) == 1
            and called_business[0]['name'] in WEALTH_TOOLS
            and finish['outcome'] != 'tool_failed'):
        trusted_reply = called_business[0].get('result', {}).get('trusted_reply')
        if isinstance(trusted_reply, str) and trusted_reply:
            result['reply'] = trusted_reply
    if not result["reply"]:
        if on_event is not None:
            await on_event({'type': 'status', 'text': '正在整理回复'})
        final_messages = [*messages, {"role": "system", "content":
            "请直接用自然语言回答用户本轮请求，表达方式由你决定。以本轮可信工具结果和任务收尾状态为事实依据；"
            "区分已生成任务卡、待本人确认、已完成和失败，不把预览说成已执行。"
            "不要输出内部卡片 ID、工具参数或 JSON 证据编号。任务收尾状态：" +
            json.dumps(finish, ensure_ascii=False)}]
        if planning_intro:
            final_messages.append({'role':'system', 'content':
                '理财规划和资产配置模型的知识库解释由服务端单独附加。这里只汇总其他业务工具的实际结果，'
                '不要补充规划或模型的理论、事实、算法说明，也不要使用外部知识。'})
        async def on_delta(chunk: str) -> None:
            if on_event is not None:
                await on_event({"type": "delta", "text": chunk})
        response = await client.generate_text(messages=final_messages, max_tokens=1200,
                                              on_delta=on_delta if on_event is not None else None)
        result["reply"] = response.data
        result["_reply_streamed"] = on_event is not None
    if planning_intro and len(called_business) != 1:
        result['reply'] = result['reply'] + '\n\n' + planning_intro
        # A streamed multi-business summary must also deliver the sourced part.
        if on_event is not None and result.get('_reply_streamed'):
            await on_event({'type':'delta', 'text':'\n\n' + planning_intro})
    return result
