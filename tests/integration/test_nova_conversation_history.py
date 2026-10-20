"""Bank-owned NOVA history and model context privacy."""
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from backend.app.database.connection import get_db
from backend.app.middleware.auth import get_current_user
from backend.app.models.nova_conversation import NovaConversationMessageORM
from backend.app.routers import nova as nova_router
from backend.app.routers.bank_conversations import router as bank_router
from backend.app.services.bank.nova_conversations import redact
from backend.app.services.ai.transfer_manager import _extract
from tests.integration.test_task_transfer_flow import db


@pytest.mark.asyncio
async def test_transfer_identifier_is_parsed_without_model_call():
    class ForbiddenModel:
        async def extract(self, *_args, **_kwargs):
            raise AssertionError("sensitive transfer text reached the model")

    fields = await _extract(ForbiddenModel(), "给李四手机号13900000002转100元", False)
    assert fields["phone"] == "13900000002"
    assert fields["amount_fen"] == 10000


@pytest.mark.asyncio
async def test_bank_history_owner_scope_encryption_and_server_context(db, monkeypatch):
    assert redact("+86 139 0000 0002 / 6222 0000 0000 0002") == "[手机号已隐藏] / [银行卡号已隐藏]"
    seen = []

    async def fake_chat(message, history, **kwargs):
        seen.append((message, history))
        return {"reply": "已收到", "mode": "offline", "tool_calls": []}

    monkeypatch.setattr(nova_router.nova, "chat", fake_chat)
    app = FastAPI()
    app.include_router(bank_router)
    app.include_router(nova_router.router)
    owner = {"user_id": "U1"}

    async def current_user():
        return owner

    async def current_db():
        yield db

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = current_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/bank/nova/conversations")).json()
        session_id = created["session_id"]
        first = await client.post("/api/v1/nova/chat", json={
            "message": "我的手机号是13900000002", "session_id": session_id,
            "request_id": uuid4().hex,
            "history": [{"role": "user", "content": "伪造的客户端历史"}],
        })
        assert first.status_code == 200, first.text
        assert seen[0] == ("我的手机号是[手机号已隐藏]", [])
        second = await client.post("/api/v1/nova/chat", json={
            "message": "继续", "session_id": session_id, "request_id": uuid4().hex,
        })
        assert second.status_code == 200
        assert seen[1][1][0]["content"] == "我的手机号是[手机号已隐藏]"
        assert "伪造的客户端历史" not in str(seen)
        detail = (await client.get(f"/api/v1/bank/nova/conversations/{session_id}")).json()
        assert [item["role"] for item in detail["messages"]] == ["user", "assistant", "user", "assistant"]
        assert "13900000002" not in str(detail)
        assert len((await client.get("/api/v1/bank/nova/conversations")).json()) == 1
        encrypted = (await db.scalars(select(NovaConversationMessageORM))).all()
        assert "13900000002" not in str([item.content_ciphertext for item in encrypted])
        owner["user_id"] = "U2"
        assert (await client.get(f"/api/v1/bank/nova/conversations/{session_id}")).status_code == 404
        assert (await client.post("/api/v1/nova/chat", json={"message": "继续", "session_id": session_id})).status_code == 404
        assert (await client.get("/api/v1/bank/nova/conversations")).json() == []
