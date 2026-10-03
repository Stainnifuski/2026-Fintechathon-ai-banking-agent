"""安全基础模块 — 权限分级定义与工具函数。

本模块是整个 AI Banking Agent 项目共享的安全基础设施，
定义了操作权限级别枚举、操作→权限映射表以及权限判定函数。
所有业务模块在执行敏感操作前，必须通过本模块获取权限级别。
"""

from enum import Enum


class PermissionLevel(Enum):
    """操作权限级别枚举。

    银行场景下，每个操作都必须归属到以下三个安全级别之一：
    - GREEN:  绿色 — 自动执行，无需用户确认（如查询类操作）
    - YELLOW: 黄色 — 需要用户明确确认后才可执行（如小额转账）
    - RED:    红色 — 需要多因子验证（MFA）后才可执行（如大额转账）
    """

    GREEN = "green"    # 🟢 自动执行：查询类操作，无需确认
    YELLOW = "yellow"  # 🟡 用户确认：展示详情后需用户明确确认
    RED = "red"        # 🔴 强验证：需 MFA（短信验证码/人脸/U盾）


# 操作名 → 权限级别映射表（MVP 阶段：转账 + 查询）
PERMISSION_MAP: dict[str, PermissionLevel] = {
    # 🟢 绿色 - 自动执行
    "query_balance": PermissionLevel.GREEN,          # 查询余额
    "query_transactions": PermissionLevel.GREEN,     # 查询交易记录
    "query_cards": PermissionLevel.GREEN,            # 查询卡片

    # 🟡 黄色 - 用户确认
    "transfer_small": PermissionLevel.YELLOW,        # 小额转账（日累计 ≤ 1000元）
    "schedule_transfer": PermissionLevel.YELLOW,     # 定时转账
    "card_apply_virtual": PermissionLevel.YELLOW,    # 申请虚拟卡
    "card_set_transaction_limits": PermissionLevel.YELLOW,  # 设置交易限额

    # 🔴 红色 - 强验证 MFA
    "transfer_large": PermissionLevel.RED,           # 大额转账（日累计 > 1000元）
    "card_adjust_credit_limit": PermissionLevel.RED, # 调整信用额度
    "card_unlock": PermissionLevel.RED,              # 解锁卡片
    "card_report_lost": PermissionLevel.RED,         # 卡片挂失
}


def get_transfer_permission(amount: float, daily_used: float) -> PermissionLevel:
    """根据转账金额和当日已用额度，判定转账操作的权限级别。

    判定规则：
    - 如果 日累计已用额度 + 本次转账金额 ≤ 1000 元 → 黄色（用户确认）
    - 否则 → 红色（需要 MFA 强验证）

    Args:
        amount: 本次转账金额（元）。
        daily_used: 当日已累计转账金额（元）。

    Returns:
        PermissionLevel: 转账操作对应的权限级别。
    """
    if daily_used + amount <= 1000:
        return PermissionLevel.YELLOW
    else:
        return PermissionLevel.RED


def get_permission_level(action: str) -> PermissionLevel:
    """查询指定操作名的权限级别。

    从 PERMISSION_MAP 中查找对应的权限级别。
    如果操作名不在映射表中，出于安全考虑，默认返回最高级别 RED。

    Args:
        action: 操作名称（如 "query_balance"、"transfer_small"）。

    Returns:
        PermissionLevel: 该操作对应的权限级别；未知操作返回 RED。
    """
    return PERMISSION_MAP.get(action, PermissionLevel.RED)
