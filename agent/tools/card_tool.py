"""卡片管理工具 - 对接后端 /api/v1/cards。

模型只能查询卡片（绿色）和创建待确认预览；本人确认（黄色）与
确认 + TOTP 强验证（红色）只能在可信页面完成，本模块不提供确认、验证工具。
所有卡号、状态、额度与上限均来自后端返回，工具不构造任何银行数据。
"""

from uuid import uuid4

import httpx

QUERY_CARDS_SCHEMA: dict = {
    "name": "query_cards",
    "description": "查询当前用户的银行卡列表，包括状态、信用额度、交易限额及银行核定上限。",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

PREVIEW_CARD_OPERATION_SCHEMA: dict = {
    "name": "preview_card_operation",
    "description": (
        "为卡片操作生成待用户核对的预览，不会执行。申请虚拟卡、设置交易限额需用户确认；"
        "调整信用额度、解锁、挂失需用户确认并通过验证器强验证。card_id/account_id 必须取自 query_cards 结果。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["card_apply_virtual", "card_set_transaction_limits",
                         "card_adjust_credit_limit", "card_unlock", "card_report_lost"],
            },
            "card_id": {"type": "string", "description": "目标卡片 ID（申请虚拟卡时不传）"},
            "account_id": {"type": "string", "description": "虚拟卡关联的借记账户 ID（仅申请虚拟卡）"},
            "credit_limit": {"type": "string", "description": "新信用额度（元，用户原文金额）"},
            "single_txn_limit": {"type": "string", "description": "新单笔交易限额（元）"},
            "daily_txn_limit": {"type": "string", "description": "新单日交易限额（元）"},
        },
        "required": ["action"],
    },
}


async def query_cards(client: httpx.AsyncClient) -> list[dict]:
    """client 需携带用户本人的 Bearer 令牌和后端 base_url。"""
    response = await client.get("/api/v1/cards")
    response.raise_for_status()
    return response.json()


async def preview_card_operation(client: httpx.AsyncClient, **arguments) -> dict:
    """只创建预览；返回的 permission_level、changes 由后端判定，供页面渲染确认卡片。"""
    payload = {key: value for key, value in arguments.items() if value is not None}
    payload["operation_id"] = f"AGENT-{uuid4().hex}"
    response = await client.post("/api/v1/cards/operations", json=payload)
    response.raise_for_status()
    return response.json()
