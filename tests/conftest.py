"""pytest 公共 fixture — 为所有测试提供模拟数据。"""

from datetime import date, datetime
import os

import pytest
import pytest_asyncio

# Set before application imports: ordinary tests never read a real signing key.
os.environ['JWT_SECRET'] = 'isolated-test-only-jwt-signing-key-32-bytes'

# 必须在应用包读取 .env 前确定付费联调开关，避免普通 pytest 被本地文件自动开启。
for _flag, _default in (("WEALTH_LIVE_TEST", "false"), ("WEALTH_SEMANTIC_LIVE", "0"),
                        ("WEALTH_WORKSPACE_LIVE", "0")):
    os.environ.setdefault(_flag, _default)


@pytest.fixture(autouse=True)
def isolated_llm_environment(monkeypatch, request):
    """默认回归不使用开发者 .env 中的真实模型凭据。"""
    live_files = {
        "test_wealth_live_model.py": os.getenv("WEALTH_LIVE_TEST") == "true",
        "test_wealth_semantic_live.py": os.getenv("WEALTH_SEMANTIC_LIVE") == "1",
        "test_wealth_workspace_mainline.py": os.getenv("WEALTH_WORKSPACE_LIVE") == "1",
    }
    if live_files.get(request.node.path.name, False):
        return
    for name in tuple(os.environ):
        if name.startswith("LLM_") or name.startswith(("TRANSFER_MODEL_", "WEALTH_MODEL_", "BILL_MODEL_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("LLM_MODE", "offline")


@pytest.fixture(autouse=True)
def isolated_knowledge_key(monkeypatch):
    """Never read or create a deployment's knowledge signing secret in tests."""
    monkeypatch.setenv('AGENT_KNOWLEDGE_SIGNING_KEY', 'isolated-test-knowledge-key-at-least-32-bytes')


@pytest_asyncio.fixture(autouse=True)
async def close_shared_llm():
    """共享客户端不跨越 pytest 的事件循环。"""
    yield
    from backend.app.services.ai.llm import llm
    await llm.aclose()


@pytest.fixture(scope="session")
def request_audit_store(tmp_path_factory):
    """Independent HTTP observations must never use a developer's bank store.

    Existing route tests override get_db with their business fixture. The audit
    admission/finalisation session is separate and needs its own isolated sink.
    NullPool avoids sharing async connections across pytest event loops.
    """
    from contextlib import asynccontextmanager
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from backend.app.models import Base
    directory = tmp_path_factory.mktemp("http_audit")
    engine = create_async_engine(f"sqlite+aiosqlite:///{directory / 'observations.db'}", poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    ready = False

    @asynccontextmanager
    async def factory():
        nonlocal ready
        if not ready:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            ready = True
        async with sessions() as db:
            yield db
    yield factory, directory
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def isolated_request_audit(monkeypatch, request_audit_store):
    from backend.app.services.audit import writer
    factory, directory = request_audit_store
    monkeypatch.setattr(writer, "default_factory", lambda: factory)
    monkeypatch.setattr(writer, "_health", {"status": "ok", "fallback_count": 0})
    monkeypatch.setenv("AUDIT_FALLBACK_DIR", str(directory / "recovery"))


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
