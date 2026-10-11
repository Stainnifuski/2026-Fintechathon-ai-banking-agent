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
        "为卡片操作生成待用户核对的预览，不会执行。申请虚拟卡、"
        "锁卡、注销虚拟卡、补卡、调低额度/限额、关闭渠道需用户确认；调高额度/限额、打开渠道、解锁、挂失还需验证器强验证。card_id/account_id 必须取自 query_cards 结果。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["card_apply_virtual", "card_set_transaction_limits",
                         "card_adjust_credit_limit", "card_unlock", "card_report_lost",
                         "card_lock", "card_cancel_virtual", "card_reissue", "card_set_channels"],
            },
            "card_id": {"type": "string", "description": "目标卡片 ID（申请虚拟卡时不传）"},
            "account_id": {"type": "string", "description": "虚拟卡关联的借记账户 ID（仅申请虚拟卡）"},
            "credit_limit": {"type": "string", "description": "新信用额度（元，用户原文金额）"},
            "single_txn_limit": {"type": "string", "description": "新单笔交易限额（元）"},
            "daily_txn_limit": {"type": "string", "description": "新单日交易限额（元）"},
            "online_enabled": {"type": "boolean", "description": "线上支付开关（仅设置交易渠道，四个开关须同时传）"},
            "atm_enabled": {"type": "boolean", "description": "ATM取现开关"},
            "overseas_enabled": {"type": "boolean", "description": "境外交易开关"},
            "contactless_enabled": {"type": "boolean", "description": "免密/闪付开关"},
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
