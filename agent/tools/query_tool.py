"""查询相关工具 - 余额查询、交易记录查询。

提供余额查询和交易记录查询的 Function Calling Schema 定义、
响应数据模型以及查询执行函数。查询类操作为绿色权限，无需用户确认。
"""

from pydantic import BaseModel, Field


# 余额查询 Function Calling Schema
QUERY_BALANCE_SCHEMA: dict = {
    "name": "query_balance",
    "description": "查询用户银行账户余额信息。",
    "parameters": {
        "type": "object",
        "properties": {
            "user_id": {
                "type": "string",
                "description": "用户 ID（如 U123456）",
            },
            "account_type": {
                "type": "string",
                "description": "账户类型过滤：savings(储蓄) / checking(活期) / credit(信用卡)，不传则查询全部",
                "enum": ["savings", "checking", "credit"],
            },
        },
        "required": ["user_id"],
    },
}

# 交易记录查询 Function Calling Schema
QUERY_TRANSACTIONS_SCHEMA: dict = {
    "name": "query_transactions",
    "description": "查询用户的历史交易记录，支持按时间范围和条数过滤。",
    "parameters": {
        "type": "object",
        "properties": {
            "user_id": {
                "type": "string",
                "description": "用户 ID（如 U123456）",
            },
            "start_date": {
                "type": "string",
                "description": "起始日期，ISO 格式（如 2026-01-01）",
            },
            "end_date": {
                "type": "string",
                "description": "结束日期，ISO 格式（如 2026-09-16）",
            },
            "limit": {
                "type": "integer",
                "description": "返回记录条数上限，默认 20",
            },
        },
        "required": ["user_id"],
    },
}


class BalanceResult(BaseModel):
    """余额查询结果数据模型。"""

    account_id: str = Field(description="账户 ID")
    account_type: str = Field(description="账户类型：savings / checking / credit")
    balance: float = Field(description="账户余额（元）")
    card_number: str = Field(description="银行卡号（脱敏格式）")
    daily_transfer_used: float = Field(description="今日已转账金额（元）")
    daily_transfer_limit: float = Field(description="日转账限额（元）")


async def query_balance(
    user_id: str,
    account_type: str | None = None,
) -> list[BalanceResult]:
    """查询用户账户余额信息。

    Args:
        user_id: 用户唯一标识。
        account_type: 可选，按账户类型过滤（savings / checking / credit）。

    Returns:
        list[BalanceResult]: 匹配的账户余额列表。
    """
    # TODO: 实现具体逻辑 — 通过 httpx 调用后端 GET /api/v1/accounts/balance
    return [
        BalanceResult(
            account_id="A001",
            account_type="checking",
            balance=0.0,
            card_number="6222****0000",
            daily_transfer_used=0.0,
            daily_transfer_limit=50000.0,
        )
    ]


async def query_transactions(
    user_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """查询用户历史交易记录。

    Args:
        user_id: 用户唯一标识。
        start_date: 可选，起始日期（ISO 格式）。
        end_date: 可选，结束日期（ISO 格式）。
        limit: 返回记录条数上限，默认 20。

    Returns:
        list[dict]: 交易记录列表，每条包含 tx_id / type / amount / timestamp 等字段。
    """
    # TODO: 实现具体逻辑 — 通过 httpx 调用后端 GET /api/v1/transactions
    return []
