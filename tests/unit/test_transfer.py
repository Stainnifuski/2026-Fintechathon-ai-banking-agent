"""转账相关单元测试 — 权限分级判定。"""

from security.common import (
    PermissionLevel,
    get_permission_level,
    get_transfer_permission,
)


class TestTransferPermission:
    """转账权限判定测试集。"""

    def test_transfer_permission_small(self) -> None:
        """测试小额转账（日累计 ≤ 1000元）应返回 YELLOW。"""
        # 场景：今日未转账，转账 500 元 → 日累计 500 ≤ 1000
        result = get_transfer_permission(amount=500.0, daily_used=0.0)
        assert result == PermissionLevel.YELLOW  # TODO: 补充更多小额场景

    def test_transfer_permission_large(self) -> None:
        """测试大额转账（日累计 > 1000元）应返回 RED。"""
        # 场景：今日已转 800，再转 500 → 日累计 1300 > 1000
        result = get_transfer_permission(amount=500.0, daily_used=800.0)
        assert result == PermissionLevel.RED  # TODO: 补充更多大额场景

    def test_transfer_permission_boundary(self) -> None:
        """测试边界值：恰好 1000 元应返回 YELLOW。"""
        # 场景：今日未转账，转账 1000 元 → 日累计 1000 == 1000
        result = get_transfer_permission(amount=1000.0, daily_used=0.0)
        assert result == PermissionLevel.YELLOW  # TODO: 测试 1000.01 等边界

    def test_get_permission_level(self) -> None:
        """测试从 PERMISSION_MAP 查询权限级别。"""
        # 查询类操作 → GREEN
        assert get_permission_level("query_balance") == PermissionLevel.GREEN
        assert get_permission_level("query_transactions") == PermissionLevel.GREEN

        # 小额转账 → YELLOW
        assert get_permission_level("transfer_small") == PermissionLevel.YELLOW

        # 大额转账 → RED
        assert get_permission_level("transfer_large") == PermissionLevel.RED

        # 未知操作 → 默认 RED（宁严勿松）
        assert get_permission_level("unknown_action") == PermissionLevel.RED
        # TODO: 补充更多操作名的测试
