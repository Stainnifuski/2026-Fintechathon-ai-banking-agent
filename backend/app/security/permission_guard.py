"""权限分级拦截器"""

import sys
from pathlib import Path

# 将项目根目录加入 sys.path，以便导入 security.common
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from security.common import (
    PermissionLevel,
    PERMISSION_MAP,
    get_transfer_permission,
    get_permission_level,
)

class PermissionDeniedError(Exception):
    """权限不足异常。"""

    def __init__(self, message: str = "权限不足，操作被拒绝", level: str = "red"):
        self.message = message
        self.level = level
        super().__init__(self.message)


async def check_operation_permission(
    action: str, user_id: str, params: dict | None = None
) -> dict:
    """检查操作权限，返回权限级别和所需验证方式。

    Args:
        action: 操作名称（如 "transfer_small"、"query_balance"）。
        user_id: 用户ID。
        params: 操作参数，转账时需包含 amount 和 account_id。

    Returns:
        权限检查结果字典，包含：
        - level: 权限级别 (green/yellow/red)
        - requires_confirmation: 是否需要用户确认
        - requires_mfa: 是否需要 MFA 验证
    """
    params = params or {}

    # 转账操作需要动态判定权限级别
    if action in ("transfer_small", "transfer_large", "transfer"):
        amount = params.get("amount", 0)
        # 银行累计额必须由4号查询后作为可信事实传入，权限层不得直读银行表。
        daily_used = params.get("daily_transfer_used", 0.0)
        level = get_transfer_permission(amount, daily_used)
    else:
        level = get_permission_level(action)

    return {
        "level": level.value,
        "requires_confirmation": level in (PermissionLevel.YELLOW, PermissionLevel.RED),
        "requires_mfa": level == PermissionLevel.RED,
    }


async def enforce_permission(
    action: str, user_id: str, params: dict | None = None
) -> None:
    """强制执行权限检查，权限不足时抛出异常。

    对于需要确认或 MFA 的操作，此函数不会自动拒绝，
    而是通过返回值告知调用方需要何种验证。
    仅在操作明确被禁止（如账户冻结等）时抛出异常。

    Args:
        action: 操作名称。
        user_id: 用户ID。
        params: 操作参数。

    Raises:
        PermissionDeniedError: 操作被禁止时抛出。
    """
    permission = await check_operation_permission(action, user_id, params)

    # 检查用户账户状态等附加条件（预留扩展点）
    # TODO: 查询用户状态，冻结账户拒绝所有操作

    if permission["requires_mfa"]:
        mfa_verified = (params or {}).get("mfa_verified", False)
        if not mfa_verified:
            raise PermissionDeniedError(
                message="此操作需要多因子验证（MFA），请先完成验证",
                level="red",
            )

    if permission["requires_confirmation"]:
        user_confirmed = (params or {}).get("user_confirmed", False)
        if not user_confirmed:
            raise PermissionDeniedError(
                message="此操作需要用户确认，请确认后再执行",
                level="yellow",
            )
