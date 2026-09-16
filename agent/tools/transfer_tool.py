"""转账相关工具 - 对接后端转账 API。

提供转账操作的 Function Calling Schema 定义、
请求/响应数据模型，以及转账执行和前置校验函数。
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


# OpenAI Function Calling 格式的 JSON Schema
TRANSFER_TOOL_SCHEMA: dict = {
    "name": "transfer_money",
    "description": "执行银行转账操作。将指定金额从用户账户转入收款人账户。",
    "parameters": {
        "type": "object",
        "properties": {
            "from_account": {
                "type": "string",
                "description": "转出账户 ID（如 A001）",
            },
            "to_contact": {
                "type": "string",
                "description": "收款人联系人 ID 或姓名",
            },
            "amount": {
                "type": "number",
                "description": "转账金额（元），必须大于 0",
            },
            "note": {
                "type": "string",
                "description": "转账备注（可选）",
            },
            "scheduled_time": {
                "type": "string",
                "description": "定时转账时间，ISO 8601 格式（可选）",
            },
        },
        "required": ["from_account", "to_contact", "amount"],
    },
}


class TransferRequest(BaseModel):
    """转账请求数据模型。"""

    from_account: str = Field(description="转出账户 ID")
    to_contact: str = Field(description="收款人联系人 ID 或姓名")
    amount: float = Field(gt=0, description="转账金额（元）")
    note: str | None = Field(default=None, description="转账备注")
    scheduled_time: str | None = Field(default=None, description="定时转账时间")


class TransferResult(BaseModel):
    """转账结果数据模型。"""

    tx_id: str = Field(description="交易流水号")
    status: str = Field(description="交易状态：completed / pending / failed")
    amount: float = Field(description="实际转账金额")
    from_account: str = Field(description="转出账户 ID")
    to_contact: str = Field(description="收款人")
    timestamp: datetime = Field(description="交易时间")
    message: str = Field(description="结果描述信息")


async def execute_transfer(
    request: TransferRequest,
    user_context: dict,
) -> TransferResult:
    """执行转账操作，调用后端转账 API。

    Args:
        request: 转账请求参数。
        user_context: 用户上下文，包含认证信息等。

    Returns:
        TransferResult: 转账执行结果。
    """
    # TODO: 实现具体逻辑 — 通过 httpx 调用后端 POST /api/v1/transactions/transfer
    return TransferResult(
        tx_id="TX-PLACEHOLDER",
        status="pending",
        amount=request.amount,
        from_account=request.from_account,
        to_contact=request.to_contact,
        timestamp=datetime.now(timezone.utc),
        message="转账请求已提交（待实现后端对接）",
    )


async def validate_transfer(
    request: TransferRequest,
    user_context: dict,
) -> dict:
    """转账前置校验：余额是否充足、是否超出日限额等。

    Args:
        request: 转账请求参数。
        user_context: 用户上下文，包含余额、日限额等信息。

    Returns:
        dict: 校验结果，包含 valid(bool) 和 errors(list[str])。
    """
    # TODO: 实现具体逻辑 — 查询账户余额和日累计额度
    errors: list[str] = []
    balance: float = user_context.get("balance", 0)
    daily_used: float = user_context.get("daily_used", 0)
    daily_limit: float = user_context.get("daily_limit", 50000)

    if request.amount > balance:
        errors.append(f"余额不足：当前余额 {balance} 元，转账金额 {request.amount} 元")

    if daily_used + request.amount > daily_limit:
        errors.append(
            f"超出日限额：今日已转 {daily_used} 元，"
            f"本次 {request.amount} 元，日限额 {daily_limit} 元"
        )

    return {"valid": len(errors) == 0, "errors": errors}
