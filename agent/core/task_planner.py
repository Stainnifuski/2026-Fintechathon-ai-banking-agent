"""任务规划器：DAG 生成 + 权限判定 + 执行调度。

本模块负责将用户意图转化为可执行的任务 DAG（有向无环图），
按照依赖关系编排子任务的执行顺序，并在执行前进行权限分级判定。
"""

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from security.common import (
    PermissionLevel,
    get_permission_level,
    get_transfer_permission,
)


class TaskStatus(Enum):
    """任务执行状态枚举。"""

    PENDING = "pending"                        # 待执行
    RUNNING = "running"                        # 执行中
    COMPLETED = "completed"                    # 已完成
    FAILED = "failed"                          # 失败
    CANCELLED = "cancelled"                    # 已取消
    WAITING_CONFIRMATION = "waiting_confirmation"  # 等待用户确认
    WAITING_MFA = "waiting_mfa"                # 等待 MFA 验证


class TaskNode(BaseModel):
    """任务 DAG 中的单个节点。"""

    task_id: str = Field(description="任务节点唯一标识")
    action: str = Field(description="操作名称，如 query_balance / transfer_money")
    params: dict = Field(default_factory=dict, description="操作参数")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="当前状态")
    depends_on: list[str] = Field(default_factory=list, description="依赖的前置任务 ID")
    result: dict | None = Field(default=None, description="执行结果")


class TaskDAG(BaseModel):
    """任务有向无环图，描述一组有依赖关系的子任务。"""

    dag_id: str = Field(description="DAG 唯一标识")
    nodes: list[TaskNode] = Field(default_factory=list, description="所有任务节点")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="DAG 创建时间",
    )


class TaskPlanner:
    """任务规划器。

    将用户意图拆解为子任务 DAG，支持串行依赖、并行执行和条件分支。
    在执行前通过 security.common 进行权限分级判定。
    """

    def __init__(self) -> None:
        """初始化任务规划器。"""
        pass

    async def plan_transfer(
        self,
        slots: dict,
        user_context: dict,
    ) -> TaskDAG:
        """规划转账任务 DAG：查余额 → 权限判定 → 执行转账。

        Args:
            slots: 已收集的转账槽位信息（recipient, amount, note 等）。
            user_context: 用户上下文，包含 user_id、account_id、daily_used 等。

        Returns:
            TaskDAG: 包含查余额、权限判定、执行转账三个节点的 DAG。
        """
        # TODO: 实现具体逻辑 — 根据 slots 和上下文构建完整的 DAG
        dag_id = f"DAG-{uuid.uuid4().hex[:8]}"

        # 节点 1: 查询余额
        check_balance = TaskNode(
            task_id=f"{dag_id}-01",
            action="query_balance",
            params={"user_id": user_context.get("user_id", "")},
        )

        # 节点 2: 权限判定（依赖余额查询结果）
        permission_check = TaskNode(
            task_id=f"{dag_id}-02",
            action="check_permission",
            params={
                "amount": slots.get("amount", 0),
                "daily_used": user_context.get("daily_used", 0),
            },
            depends_on=[check_balance.task_id],
        )

        # 节点 3: 执行转账（依赖权限判定）
        execute = TaskNode(
            task_id=f"{dag_id}-03",
            action="transfer_money",
            params={
                "from_account": user_context.get("account_id", ""),
                "to_contact": slots.get("recipient", ""),
                "amount": slots.get("amount", 0),
                "note": slots.get("note"),
            },
            depends_on=[permission_check.task_id],
        )

        return TaskDAG(
            dag_id=dag_id,
            nodes=[check_balance, permission_check, execute],
        )

    async def execute_dag(self, dag: TaskDAG) -> dict:
        """按 DAG 依赖顺序执行所有任务节点。

        遍历 DAG 中的节点，按拓扑序依次执行。
        如果遇到需要用户确认或 MFA 验证的节点，暂停执行并返回等待状态。

        Args:
            dag: 待执行的任务 DAG。

        Returns:
            dict: 执行结果摘要，包含各节点状态和最终结果。
        """
        # TODO: 实现具体逻辑 — 拓扑排序 + 逐节点执行 + 异常处理
        results: dict[str, dict | None] = {}
        for node in dag.nodes:
            # 检查依赖是否全部完成
            deps_met = all(
                results.get(dep) is not None for dep in node.depends_on
            )
            if not deps_met:
                node.status = TaskStatus.FAILED
                results[node.task_id] = {"error": "依赖任务未完成"}
                continue

            node.status = TaskStatus.RUNNING
            # TODO: 根据 node.action 分发到具体的工具函数执行
            node.status = TaskStatus.COMPLETED
            node.result = {"status": "success"}
            results[node.task_id] = node.result

        return {
            "dag_id": dag.dag_id,
            "node_results": results,
            "status": "completed",
        }

    def check_permission(
        self,
        action: str,
        amount: float = 0,
        daily_used: float = 0,
    ) -> str:
        """判定操作的权限级别。

        对于转账类操作，基于金额和日累计额度动态判定；
        对于其他操作，查询全局权限映射表。

        Args:
            action: 操作名称（如 "transfer_money"、"query_balance"）。
            amount: 转账金额（仅转账类操作需要）。
            daily_used: 当日已累计转账金额（仅转账类操作需要）。

        Returns:
            str: 权限级别字符串 "green" / "yellow" / "red"。
        """
        if action in ("transfer_money", "transfer_small", "transfer_large"):
            permission: PermissionLevel = get_transfer_permission(amount, daily_used)
            return permission.value

        permission = get_permission_level(action)
        return permission.value
