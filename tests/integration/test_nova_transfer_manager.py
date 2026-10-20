"""Unified NOVA transfer drafts and handoff to the existing state machine."""
from uuid import uuid4

import pytest
import httpx
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.integration.test_task_transfer_flow import db, InProcessBankApi
from backend.app.models import ContactORM, TransferTaskORM, UserORM, AccountORM
from backend.app.services.ai.nlu import NLUEngine
from backend.app.services.ai.transfer_manager import ManagerError, get_session, process_message, wants_transfer
from backend.app.services.bank.transfer_cards import transfer_card_number
from backend.app.routers import nova as nova_router
from backend.app.database.connection import get_db
from backend.app.middleware.auth import get_current_user


async def send(db, message, previous=None, **kwargs):
    return await process_message(
        db, InProcessBankApi(db), NLUEngine(), user_id="U1", message=message,
        request_id=kwargs.pop("request_id", uuid4().hex),
        session_id=previous["session_id"] if previous else None,
        session_version=previous["session_version"] if previous else 0, **kwargs,
    )


@pytest.mark.asyncio
async def test_new_recipient_requires_name_and_full_number_before_plan(db):
    first = await send(db, "给李四转100元")
    assert len(first["tasks"]) == 1
    assert "完整手机号或完整银行卡号" in first["tasks"][0]["missing_fields"]
    assert await db.scalar(select(func.count()).select_from(TransferTaskORM)) == 0
    second = await send(db, "13900000002", first)
    assert second["tasks"][0]["task"]["operation"]["amount_fen"] == 10000
    assert second["tasks"][0]["task"]["phase"] == "WAITING_CONFIRMATION"
    assert (await db.get(AccountORM, "A1")).balance == 5000


@pytest.mark.asyncio
async def test_saved_contact_needs_only_name_and_amount_and_is_restored(db):
    db.add(ContactORM(contact_id="NOVA-C1", user_id="U1", name="李四", nickname="老李",
                      phone="13900000002", account_number=transfer_card_number("A2"),
                      phone_known=True, card_known=False, bank="AI 模拟银行", transfer_count=1))
    await db.commit()
    first = await send(db, "给老李转200元")
    task = first["tasks"][0]
    assert task["task"]["operation"]["amount_fen"] == 20000
    assert task["task"]["confirmation_card"]["recipient_name"] == "李四"
    restored = await get_session(db, "U1", first["session_id"])
    assert restored["tasks"][0]["transfer_task_id"] == task["transfer_task_id"]
    with pytest.raises(ManagerError):
        await get_session(db, "U2", first["session_id"])


@pytest.mark.asyncio
async def test_card_only_contact_does_not_turn_unknown_phone_into_known_phone(db):
    db.add(ContactORM(contact_id="NOVA-CARD", user_id="U1", name="李四", nickname="卡号联系人",
                      phone="13900000002", account_number=transfer_card_number("A2"),
                      phone_known=False, card_known=True, bank="AI 模拟银行", transfer_count=1))
    await db.commit()
    reply = await send(db, "给卡号联系人转100元")
    card = reply["tasks"][0]["task"]["confirmation_card"]
    assert card["recipient_card_masked"] is not None
    assert card["recipient_phone_masked"] is None


@pytest.mark.asyncio
async def test_two_transfers_have_independent_drafts_and_idempotent_reply(db):
    db.add(UserORM(user_id="U3", name="王五", phone="13700000003", id_card="demo",
                   password_hash="hash", status="active"))
    await db.flush()
    db.add(AccountORM(account_id="A3", user_id="U3", account_type="savings", card_number="3",
                      balance=0, daily_transfer_used=0, daily_transfer_limit=50000, status="active"))
    await db.commit()
    request_id = uuid4().hex
    first = await send(db, "给李四转100元，给王五转200元", request_id=request_id)
    assert len(first["tasks"]) == 2
    assert [task["amount_fen"] for task in first["tasks"]] == [10000, 20000]
    same = await send(db, "给李四转100元，给王五转200元", request_id=request_id)
    assert same["session_id"] == first["session_id"]
    assert len(same["tasks"]) == 2
    assert await db.scalar(select(func.count()).select_from(TransferTaskORM)) == 0
    selected = first["tasks"][0]["chat_task_id"]
    second = await send(db, "13900000002", first, focus_task_id=selected)
    assert second["tasks"][0]["transfer_task_id"]
    assert second["tasks"][1]["transfer_task_id"] is None


@pytest.mark.asyncio
async def test_full_card_recipient_and_plan_modification(db):
    card = transfer_card_number("A2")
    first = await send(db, f"给李四银行卡号{card}转100元")
    task = first["tasks"][0]["task"]
    assert task["operation"]["amount_fen"] == 10000
    assert task["confirmation_card"]["recipient_card_masked"].endswith(card[-4:])
    modified = await send(db, "改成150元", first,
                          focus_task_id=first["tasks"][0]["chat_task_id"])
    revised = modified["tasks"][0]["task"]
    assert revised["task_id"] == task["task_id"]
    assert revised["plan_version"] == 2
    assert revised["operation"]["amount_fen"] == 15000


@pytest.mark.asyncio
async def test_multiple_recipient_accounts_require_selection(db):
    db.add(AccountORM(account_id="A4", user_id="U2", account_type="savings", card_number="4",
                      balance=0, daily_transfer_used=0, daily_transfer_limit=50000, status="active"))
    await db.commit()
    first = await send(db, "给李四手机号13900000002转100元")
    choices = first["tasks"][0]["candidates"]
    assert len(choices) == 2
    assert all("account_id" not in choice for choice in choices)
    with pytest.raises(ManagerError):
        await send(db, "选择", first, candidate_id="invented")
    await db.rollback()
    chosen = await send(db, "选择", first, candidate_id=choices[1]["candidate_id"])
    assert chosen["tasks"][0]["task"]["operation"]["to_account"] == "A4"


@pytest.mark.asyncio
async def test_nova_http_transfer_reply_and_restore(db, monkeypatch):
    monkeypatch.setattr(nova_router, "get_bank_api", lambda: InProcessBankApi(db))
    app = FastAPI()
    app.include_router(nova_router.router)
    async def current_user():
        return {"user_id": "U1"}
    async def current_db():
        yield db
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = current_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/nova/chat", json={
            "message": "给李四转100元", "request_id": uuid4().hex,
        })
        assert response.status_code == 200
        draft = response.json()
        assert draft["tasks"][0]["status"] == "COLLECTING"
        restored = await client.get(f"/api/v1/nova/task-sessions/{draft['session_id']}")
        assert restored.status_code == 200
        assert restored.json()["tasks"][0]["chat_task_id"] == draft["tasks"][0]["chat_task_id"]
        streamed = await client.post("/api/v1/nova/chat/stream", json={
            "message": "13900000002", "request_id": uuid4().hex,
            "session_id": draft["session_id"], "session_version": draft["session_version"],
        })
        assert streamed.status_code == 200
        assert "event: done" in streamed.text
        assert "WAITING_CONFIRMATION" in streamed.text


def test_transfer_intent_does_not_capture_read_only_transfer_history():
    assert wants_transfer("转钱")
    assert wants_transfer("给李四转100元")
    assert not wants_transfer("请查看我的转账记录")
    assert not wants_transfer("我给李四转过多少钱")


@pytest.mark.asyncio
async def test_vague_transfer_creates_visible_draft_and_confirmation_requests_missing_fields(db):
    draft = await send(db, "转钱")
    assert len(draft["tasks"]) == 1
    assert draft["tasks"][0]["transfer_task_id"] is None
    assert "收款人姓名" in draft["tasks"][0]["missing_fields"]
    assert "转账金额" in draft["tasks"][0]["missing_fields"]
    confirmation = await send(db, "确认", draft)
    assert "还是草稿" in confirmation["reply"]
    assert await db.scalar(select(func.count()).select_from(TransferTaskORM)) == 0
