"""Agent 工具注册中心。

集中注册和管理所有可用的 Agent 工具（Function Calling），
提供工具查找和 Schema 列表导出功能。
"""

from agent.tools.transfer_tool import TRANSFER_TOOL_SCHEMA
from agent.tools.query_tool import QUERY_BALANCE_SCHEMA, QUERY_TRANSACTIONS_SCHEMA
from agent.tools.card_tool import QUERY_CARDS_SCHEMA, PREVIEW_CARD_OPERATION_SCHEMA


# 全局工具注册表：name → schema
TOOL_REGISTRY: dict[str, dict] = {
    "transfer_money": TRANSFER_TOOL_SCHEMA,
    "query_balance": QUERY_BALANCE_SCHEMA,
    "query_transactions": QUERY_TRANSACTIONS_SCHEMA,
    "query_cards": QUERY_CARDS_SCHEMA,
    "preview_card_operation": PREVIEW_CARD_OPERATION_SCHEMA,
}


def get_tool(name: str) -> dict | None:
    """根据工具名获取对应的 Function Calling Schema。

    Args:
        name: 工具名称（如 "transfer_money"）。

    Returns:
        dict | None: 工具的 JSON Schema 定义，不存在则返回 None。
    """
    return TOOL_REGISTRY.get(name)


def get_all_tools() -> list[dict]:
    """获取所有已注册工具的 Function Calling Schema 列表。

    返回的格式可直接用于 OpenAI API 的 tools 参数。

    Returns:
        list[dict]: 所有工具的 Schema 列表，每项包含 type 和 function 字段。
    """
    return [
        {"type": "function", "function": schema}
        for schema in TOOL_REGISTRY.values()
    ]
