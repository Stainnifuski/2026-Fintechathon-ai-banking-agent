"""pytest 公共 fixture — 为所有测试提供模拟数据。"""

from datetime import date, datetime

import pytest


@pytest.fixture
def fake_user() -> dict:
    """返回一个模拟用户字典。"""
    return {
        "user_id": "U123456",
        "name": "张三",
        "phone": "138****5678",
        "id_card": "110***********1234",
        "risk_level": "balanced",
        "created_at": datetime(2025, 1, 15, 10, 30, 0),
        "status": "active",
    }


@pytest.fixture
def fake_account() -> dict:
    """返回一个模拟账户字典，余额 10000 元，今日未转账。"""
    return {
        "account_id": "A001",
        "user_id": "U123456",
        "account_type": "savings",
        "card_number": "6222****8842",
        "balance": 10000.0,
        "daily_transfer_used": 0.0,
        "daily_transfer_limit": 50000.0,
        "status": "active",
    }


@pytest.fixture
def fake_contact() -> dict:
    """返回一个模拟联系人字典。"""
    return {
        "contact_id": "C001",
        "user_id": "U123456",
        "name": "李四",
        "nickname": "老李",
        "phone": "139****9012",
        "account_number": "6228****6655",
        "bank": "中国工商银行",
        "transfer_count": 5,
        "last_transfer": date(2026, 9, 10),
    }
