"""Model-planned task graph for a NOVA turn.

The graph records sub-goals and their dependencies; it names catalog domains, never tools,
so one node may need no tool, one tool or several. The server only checks structure
(ids, references, cycles, known domains). Phase one plans and shows the graph; tool
execution still follows the existing select-plan-execute loop.
"""
import json
import logging
import os
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .llm import LLMError
from . import debug_trace

logger = logging.getLogger(__name__)
MAX_NODES = 8


class TaskNode(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=8, description="节点编号，如 n1、n2")
    goal: str = Field(min_length=1, max_length=120, description="这一步要达成的子目标，用自然语言描述，不写工具名")
    kind: Literal["query", "prepare", "await_user", "answer", "clarify"] = Field(
        description="query 查询事实；prepare 生成或修改待本人确认的任务卡；await_user 等待本人确认、验证或付款；"
                    "answer 根据已有结果回答或分析；clarify 向用户追问缺少的信息")
    domains: list[str] = Field(default_factory=list, max_length=4,
                               description="涉及的能力目录 domain；纯回答或追问可为空")
    depends_on: list[str] = Field(default_factory=list, max_length=MAX_NODES,
                                  description="必须先完成的节点编号")
    done_when: str = Field(min_length=1, max_length=120, description="判断这一步完成的标准")


class TaskGraphDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    nodes: list[TaskNode] = Field(min_length=1, max_length=MAX_NODES)
    revises_previous: bool = Field(description="本轮是在上一版任务图基础上继续或修改时为 true；全新的请求为 false")
    rationale: str = Field(max_length=200, description="为何这样拆分与排序")


PLAN_PROMPT = (
    "先把用户本轮目标拆成任务图（有向无环图），只做规划，不调用工具。"
    "每个节点是一个子目标，不是一次工具调用：一个节点可以不需要工具，也可以需要多次查询。"
    "depends_on 只写真正需要先完成的节点；彼此独立的节点不要串成一条链。"
    "用户需要本人确认、验证或付款的步骤用 await_user 单独成节点，NOVA 不能代为完成。"
    "根据某一步的结果直接回答属于该步本身，不要另拆回答节点；只有需要综合多个节点的结果时才使用 answer 节点。"
    "domains 只能取能力目录中的 domain。简单请求只需一个节点。"
    "若提供了上一版任务图，判断本轮是否在延续、细化或修改那次请求：是则 revises_previous 为 true 并给出完整的新版本，"
    "与之无关的新请求为 false。")


def enabled() -> bool:
    return os.getenv("NOVA_TASK_GRAPH", "1") != "0"


def check(draft: TaskGraphDraft, domains: set[str]) -> tuple[list[dict], list[str]]:
    """Structural errors are returned for one repair round; unknown domains drop per node."""
    errors, ids = [], [node.id for node in draft.nodes]
    if len(set(ids)) != len(ids):
        errors.append("节点编号重复")
    known = set(ids)
    for node in draft.nodes:
        if node.id in node.depends_on:
            errors.append(f"{node.id} 依赖自身")
        missing = [ref for ref in node.depends_on if ref not in known]
        if missing:
            errors.append(f"{node.id} 依赖不存在的节点 {missing}")
    if not errors and _topological(draft.nodes) is None:
        errors.append("依赖关系存在环")
    nodes = [{**node.model_dump(mode="json"),
              "depends_on": list(dict.fromkeys(node.depends_on)),
              "domains": [d for d in dict.fromkeys(node.domains) if d in domains],
              "dropped_domains": [d for d in node.domains if d not in domains]}
             for node in draft.nodes]
    return nodes, errors


def _topological(nodes: list[TaskNode]) -> list[str] | None:
    pending = {node.id: set(node.depends_on) for node in nodes}
    order = []
    while pending:
        ready = sorted(node_id for node_id, deps in pending.items() if not deps)
        if not ready:
            return None
        for node_id in ready:
            order.append(node_id)
            del pending[node_id]
        for deps in pending.values():
            deps.difference_update(ready)
    return order


def is_complex(depth: dict[str, int]) -> bool:
    """A graph is complex once two nodes share a level; a single chain is a simple task."""
    return len(set(depth.values())) < len(depth)


def levels(nodes: list[dict]) -> dict[str, int]:
    """Longest dependency depth of each node; the frontend lays out columns by it."""
    by_id = {node["id"]: node for node in nodes}
    depth: dict[str, int] = {}

    def visit(node_id: str) -> int:
        if node_id not in depth:
            deps = by_id[node_id]["depends_on"]
            depth[node_id] = 1 + max((visit(dep) for dep in deps), default=-1)
        return depth[node_id]
    for node_id in by_id:
        visit(node_id)
    return depth


async def plan(client, messages: list[dict], catalog: dict, previous: dict | None,
               request_id: str | None) -> dict | None:
    """One structured planning call plus one repair round; failures never stop the turn."""
    names = {business["domain"]: business["name"] for business in catalog["businesses"]}
    domains = set(names)
    prompt = [*messages, {"role": "system", "content": PLAN_PROMPT + (
        "上一版任务图：" + json.dumps(_as_draft(previous), ensure_ascii=False) if previous else "当前没有上一版任务图。")}]
    for _ in range(2):
        try:
            draft = (await client.generate_structured(messages=prompt, schema=TaskGraphDraft,
                                                      max_tokens=1500)).data
        except (LLMError, ValidationError, TypeError, ValueError) as error:
            code = getattr(error, "code", type(error).__name__)
            logger.warning("NOVA task graph unavailable: %s", code)
            debug_trace.record("task_graph_error", {"error": code})
            if code != "MODEL_SCHEMA_INVALID":
                return None
            prompt = [*prompt, {"role": "system", "content": "任务图不符合输出格式，请严格按 schema 重新输出，不要添加额外字段。"}]
            continue
        if not isinstance(draft, TaskGraphDraft):
            return None
        nodes, errors = check(draft, domains)
        debug_trace.record("task_graph_draft", {**draft.model_dump(mode="json"), "errors": errors})
        if not errors:
            break
        prompt = [*prompt, {"role": "system", "content": "任务图结构不合法，请修正后重新输出：" +
                            json.dumps(errors, ensure_ascii=False)}]
    else:
        return None
    revises = draft.revises_previous and previous is not None
    depth = levels(nodes)
    for node in nodes:
        node.update(level=depth[node["id"]], status="planned",
                    domain_names=[names[domain] for domain in node["domains"]])
    return {"graph_id": previous["graph_id"] if revises else "TG-" + uuid4().hex[:16],
            "version": previous["version"] + 1 if revises else 1,
            "request_id": request_id, "rationale": draft.rationale,
            "nodes": nodes, "complex": is_complex(depth), "outcome": "planned"}


def _as_draft(graph: dict) -> dict:
    """The previous graph in the planner's own output shape, so it is not copied back with display fields."""
    fields = set(TaskNode.model_fields)
    return {"rationale": graph["rationale"],
            "nodes": [{key: value for key, value in node.items() if key in fields} for node in graph["nodes"]]}


def settle(graph: dict | None, outcome: str) -> dict | None:
    """Phase one does not run per node, so only the turn outcome is recorded."""
    if graph is None:
        return None
    return {**graph, "outcome": {"tool_failed": "failed"}.get(outcome, outcome)}


def public(graph: dict | None) -> dict | None:
    if graph is None:
        return None
    return {key: graph[key] for key in ("graph_id", "version", "request_id", "rationale", "nodes", "complex", "outcome")}
