"""NOVA collection drafts: the model interprets, the server verifies and writes."""
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from tests.integration.test_task_transfer_flow import db
from backend.app.models import AccountORM, ContactORM, UserORM, TransferTaskORM
from backend.app.routers import nova as nova_router
from backend.app.database.connection import get_db
from backend.app.middleware.auth import get_current_user
from backend.app.services.ai import collection_manager
from backend.app.services.ai import nova as nova_service
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.schemas.nova import PageContext
from backend.app.services.bank import nova_conversations as bank_conversations
from backend.app.models.nova_task import NovaSessionORM


def app_for(db):
    app = FastAPI()
    app.include_router(nova_router.router)

    async def current_user():
        return {"user_id": "U1"}

    async def current_db():
        yield db

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = current_db
    return app


class ScriptedModel:
    """Router, agent loop and collection interpreter, scripted per utterance.

    `decisions` maps the user's message to the structured interpretation a model
    would return (a dict, or a callable receiving the interpreter payload).
    """

    def __init__(self, monkeypatch, decisions: dict):
        self.decisions, self.routed, self.interpreted = decisions, [], []
        monkeypatch.setenv("LLM_MODE", "model")
        monkeypatch.setenv("LLM_API_KEY", "test-secret")
        monkeypatch.setattr(nova_service.llm, "generate_structured", self.structured)
        monkeypatch.setattr(nova_service.llm, "generate_with_tools", self.with_tools)

    async def structured(self, **kwargs):
        payload = json.loads(kwargs["messages"][-1]["content"])
        if kwargs["schema"] is nova_service.RequestRoute:
            self.routed.append(payload)
            return SimpleNamespace(data=nova_service.RequestRoute(
                mode="direct_react", domains=["collection"], has_dependencies=False))
        assert kwargs["schema"] is collection_manager.CollectionInterpretation
        self.interpreted.append(payload)
        decision = self.decisions[payload["message"]]
        decision = decision(payload) if callable(decision) else decision
        return SimpleNamespace(data=collection_manager.CollectionInterpretation.model_validate(decision))

    async def with_tools(self, **kwargs):
        last = kwargs["messages"][-1]
        name = "manage_collection_request" if last.get("role") == "tool" else "inspect_collection_request"
        return SimpleNamespace(data={"role": "assistant", "content": None, "tool_calls": [
            {"id": uuid4().hex, "type": "function", "function": {"name": name, "arguments": "{}"}}]})


def named(change, quote, *names):
    return {"change": change, "scope": "named", "names": list(names), "quote": quote}


def everyone(quote, change="set"):
    return {"change": change, "scope": "all_saved_contacts", "names": [], "quote": quote}


async def add_contact(db, contact_id, name, phone, nickname=None):
    db.add(ContactORM(contact_id=contact_id, user_id="U1", name=name, nickname=nickname,
                      phone=phone, account_number=contact_id, bank="AI 模拟银行",
                      phone_known=True, card_known=False, transfer_count=0))
    await db.commit()


async def add_wangwu(db):
    db.add(UserORM(user_id="U3", name="王五", phone="13700000003",
                   id_card="demo", password_hash="hash", status="active"))
    await add_contact(db, "C3", "王五", "13700000003")


async def say(client, message, previous=None):
    body = {"message": message, "request_id": uuid4().hex}
    if previous:
        body.update(session_id=previous["session_id"], session_version=previous.get("session_version", 0))
    response = await client.post("/api/v1/nova/chat", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def client_for(db):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(db)), base_url="http://test")


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["chat", "chat/stream"])
async def test_collection_prefills_saved_alias_and_unknown_contact(db, monkeypatch, endpoint):
    ScriptedModel(monkeypatch, {"向小李和王五各收100元": {
        "action": "create", "payers": named("set", "小李和王五", "小李", "王五"),
        "amount_quote": "100元", "amount_mode": "per_person"}})
    await add_contact(db, "C2", "李四", "13900000002", "小李")
    async with client_for(db) as client:
        response = await client.post(f"/api/v1/nova/{endpoint}", json={
            "message": "向小李和王五各收100元", "request_id": uuid4().hex,
        })
        assert response.status_code == 200, response.text
        result = (next(json.loads(line[6:]) for line in response.text.splitlines()
                       if line.startswith("data: ") and json.loads(line[6:]).get("type") == "done")
                  if endpoint.endswith("stream") else response.json())
        assert [item["name"] for item in result["tool_calls"][:2]] == [
            "inspect_collection_request", "manage_collection_request"]
        assert "返回资料不完整" not in result["reply"]
        assert result["tasks"] == []
        card = result["collection_cards"][0]
        assert card["status"] == "DRAFT"
        assert card["selected_payer_user_ids"] == ["U2"]
        assert card["unknown_names"] == ["王五"]
        assert card["receive_account_id"] == "A1"
        assert card["amount_fen"] == 10000
        assert card["split_equally"] is False
        assert not card.get("payment_request_ids")
        assert not (await db.scalars(select(TransferTaskORM))).all()
        restored = await client.get(f"/api/v1/nova/task-sessions/{result['session_id']}")
        assert restored.json()["collection_cards"][0]["collection_id"] == card["collection_id"]


@pytest.mark.asyncio
async def test_collection_refresh_and_explicit_send(db, monkeypatch):
    ScriptedModel(monkeypatch, {"向小李和王五各收100元": {
        "action": "create", "payers": named("set", "小李和王五", "小李", "王五"),
        "amount_quote": "100元", "amount_mode": "per_person"}})
    calls = []

    async def fake_batch(**kwargs):
        calls.append(kwargs)
        return [{"payment_request_id": "PR-1"}, {"payment_request_id": "PR-2"}]

    monkeypatch.setattr(collection_manager, "create_payment_request_batch", fake_batch)
    await add_contact(db, "C2", "李四", "13900000002", "小李")
    db.add(AccountORM(account_id="A0", user_id="U1", account_type="savings",
                      card_number="0", balance=100, daily_transfer_used=0,
                      daily_transfer_limit=50000, status="active"))
    await db.commit()
    async with client_for(db) as client:
        result = await say(client, "向小李和王五各收100元")
        card = result["collection_cards"][0]
        assert card["receive_account_id"] == "A0"
        assert not calls
        blocked = await client.post(
            f"/api/v1/nova/task-sessions/{result['session_id']}/collections/{card['collection_id']}/send",
            json={"session_version": result["session_version"], "payer_user_ids": ["U2"],
                  "receive_account_id": "A0", "amount": 100, "note": "", "split_equally": False})
        assert blocked.status_code == 422
        assert not calls
        await add_wangwu(db)
        refreshed = await client.post(
            f"/api/v1/nova/task-sessions/{result['session_id']}/collections/{card['collection_id']}/refresh",
            json={"session_version": result["session_version"]})
        assert refreshed.status_code == 200, refreshed.text
        data = refreshed.json()
        assert data["collection_cards"][0]["unknown_names"] == []
        assert set(data["collection_cards"][0]["selected_payer_user_ids"]) == {"U2", "U3"}
        wrong_account = await client.post(
            f"/api/v1/nova/task-sessions/{result['session_id']}/collections/{card['collection_id']}/send",
            json={"session_version": data["session_version"], "payer_user_ids": ["U2", "U3"],
                  "receive_account_id": "A2", "amount": 100, "note": "测试", "split_equally": False})
        assert wrong_account.status_code == 422
        assert len(calls) == 0
        sent = await client.post(
            f"/api/v1/nova/task-sessions/{result['session_id']}/collections/{card['collection_id']}/send",
            json={"session_version": data["session_version"], "payer_user_ids": ["U2", "U3"],
                  "receive_account_id": "A1", "amount": 100, "note": "测试", "split_equally": False})
        assert sent.status_code == 200, sent.text
        assert sent.json()["collection_cards"][0]["status"] == "SENT"
        assert sent.json()["collection_cards"][0]["payment_request_ids"] == ["PR-1", "PR-2"]
        assert calls[0]["receive_account_id"] == "A1"
        assert calls[0]["payer_user_ids"] == ["U2", "U3"]


@pytest.mark.asyncio
async def test_ambiguous_saved_name_needs_one_card_selection(db, monkeypatch):
    ScriptedModel(monkeypatch, {"向李四收100元": {
        "action": "create", "payers": named("set", "李四", "李四"), "amount_quote": "100元"}})
    calls = []

    async def fake_batch(**kwargs):
        calls.append(kwargs)
        return [{"payment_request_id": "PR-ONE"}]

    monkeypatch.setattr(collection_manager, "create_payment_request_batch", fake_batch)
    db.add(UserORM(user_id="U3", name="李四", phone="13700000003",
                   id_card="demo", password_hash="hash", status="active"))
    await add_contact(db, "C2", "李四", "13900000002")
    await add_contact(db, "C3", "李四", "13700000003")
    async with client_for(db) as client:
        result = await say(client, "向李四收100元")
        card = result["collection_cards"][0]
        assert card["selected_payer_user_ids"] == []
        assert card["ambiguous_names"] == ["李四"]
        url = f"/api/v1/nova/task-sessions/{result['session_id']}/collections/{card['collection_id']}/send"
        payload = {"session_version": result["session_version"], "payer_user_ids": ["U2", "U3"],
                   "receive_account_id": "A1", "amount": 100, "split_equally": False, "note": ""}
        assert (await client.post(url, json=payload)).status_code == 422
        payload["payer_user_ids"] = ["U2"]
        sent = await client.post(url, json=payload)
        assert sent.status_code == 200, sent.text
        assert calls[0]["payer_user_ids"] == ["U2"]


def test_verification_drops_only_ungrounded_fields():
    decision = collection_manager.CollectionInterpretation.model_validate({
        "action": "update", "payers": named("add", "李四", "李四", "赵六"),
        "amount_quote": "200", "amount_mode": "split_total", "note": "午饭",
        "receive_account_id": "A9", "receive_account_quote": "尾号1234"})
    verified, rejected = collection_manager._verify(
        decision, ["李四也一起，总共200"], [{"collection_id": "X"}],
        [{"account_id": "A1", "card_masked": "1234", "account_type": "savings"}])
    assert rejected == ["payers", "note", "receive_account"]
    assert verified.payers is None and verified.note is None and verified.receive_account_id is None
    assert verified.amount_quote == "200"
    assert verified.amount_mode == "split_total"
    bad_amount, rejected = collection_manager._verify(
        collection_manager.CollectionInterpretation(action="update", amount_quote="300"),
        ["改成200"], [], [])
    assert rejected == ["amount"] and bad_amount.amount_quote is None


@pytest.mark.asyncio
async def test_reported_followup_adds_payer_by_bare_name(db, monkeypatch):
    """Regression: “向王五收款” then “加上李四” left the card unchanged but replied “已更新”."""
    model = ScriptedModel(monkeypatch, {
        "向王五收款": {"action": "create", "payers": named("set", "王五", "王五")},
        "加上李四": {"action": "update", "payers": named("add", "李四", "李四")},
        "李四也一起，总共200，AA均分": {"action": "update", "payers": named("add", "李四", "李四"),
                                      "amount_quote": "200", "amount_mode": "split_total"},
    })
    await add_contact(db, "C2", "李四", "13900000002")
    await add_wangwu(db)
    async with client_for(db) as client:
        first = await say(client, "向王五收款")
        assert first["collection_cards"][0]["selected_payer_user_ids"] == ["U3"]
        second = await say(client, "加上李四", first)
        card = second["collection_cards"][0]
        assert card["collection_id"] == first["collection_cards"][0]["collection_id"]
        assert set(card["selected_payer_user_ids"]) == {"U2", "U3"}
        assert "添加付款人李四" in second["reply"]
        assert "返回资料不完整" not in second["reply"]
        assert model.routed[-1]["transfer_tasks"]["collection_drafts"][0]["requested_names"] == ["王五"]
        third = await say(client, "李四也一起，总共200，AA均分", second)
    card = third["collection_cards"][0]
    assert card["amount_fen"] == 20000
    assert card["split_equally"] is True
    assert "金额改为200.00元" in third["reply"] and "改为AA均分" in third["reply"]


@pytest.mark.asyncio
async def test_update_without_effect_does_not_claim_success(db, monkeypatch):
    ScriptedModel(monkeypatch, {
        "向李四收100元": {"action": "create", "payers": named("set", "李四", "李四"), "amount_quote": "100元"},
        "加上李四": {"action": "update", "payers": named("add", "李四", "李四")},
        "金额改成李四的生日": {"action": "update", "amount_quote": "520"},
    })
    await add_contact(db, "C2", "李四", "13900000002")
    async with client_for(db) as client:
        first = await say(client, "向李四收100元")
        same = await say(client, "加上李四", first)
        assert "已更新" not in same["reply"]
        assert "没有识别到需要修改的内容" in same["reply"]
        row = await db.get(NovaSessionORM, first["session_id"])
        assert row.version == first["session_version"]
        ungrounded = await say(client, "金额改成李四的生日", first)
    assert "未能从你的原话中确认" in ungrounded["reply"] and "金额" in ungrounded["reply"]
    assert "已更新" not in ungrounded["reply"]
    row = await db.get(NovaSessionORM, first["session_id"])
    assert row.state["collection_cards"][0]["amount_fen"] == 10000


@pytest.mark.asyncio
async def test_all_saved_contacts_are_scope_and_followup_updates_same_card(db, monkeypatch):
    ScriptedModel(monkeypatch, {
        "向我存着的通讯录每一位朋友发起收款": {"action": "create", "payers": everyone("我存着的通讯录每一位朋友")},
        "每个人收款100元": {"action": "update", "amount_quote": "100元", "amount_mode": "per_person"},
    })
    await add_contact(db, "C2", "李四", "13900000002", "小李")
    async with client_for(db) as client:
        draft = await say(client, "向我存着的通讯录每一位朋友发起收款")
        card = draft["collection_cards"][0]
        assert card["target_scope"] == "all_saved_contacts"
        assert card["requested_names"] == []
        assert card["unknown_names"] == []
        assert card["selected_payer_user_ids"] == ["U2"]
        assert card["amount_fen"] is None
        second = await say(client, "每个人收款100元", draft)
    updated = second["collection_cards"]
    assert len(updated) == 1
    assert updated[0]["collection_id"] == card["collection_id"]
    assert updated[0]["selected_payer_user_ids"] == ["U2"]
    assert updated[0]["amount_fen"] == 10000


@pytest.mark.asyncio
async def test_model_must_inspect_contacts_before_collection_draft(db, monkeypatch):
    ScriptedModel(monkeypatch, {"向我的联系人列表里所有人发起收款": {
        "action": "create", "payers": everyone("联系人列表里所有人")}})
    await add_contact(db, "C2", "李四", "13900000002", "小李")
    calls = []

    async def with_tools(**kwargs):
        names = {tool["function"]["name"] for tool in kwargs["tools"]}
        assert names == {"inspect_collection_request", "manage_collection_request"}
        name = ("manage_collection_request" if len(calls) in (0, 2)
                else "inspect_collection_request")
        calls.append(name)
        return SimpleNamespace(data={"role": "assistant", "content": None,
            "tool_calls": [{"id": f"call-{len(calls)}", "type": "function",
                            "function": {"name": name, "arguments": "{}"}}]})

    monkeypatch.setattr(nova_service.llm, "generate_with_tools", with_tools)
    async with client_for(db) as client:
        data = await say(client, "向我的联系人列表里所有人发起收款")
    assert calls == ["manage_collection_request", "inspect_collection_request",
                     "manage_collection_request"]
    assert data["tool_calls"][0]["result"]["error"] == "COLLECTION_INSPECTION_REQUIRED"
    assert data["collection_cards"][0]["selected_payer_user_ids"] == ["U2"]
    assert data["collection_cards"][0]["requested_names"] == []


@pytest.mark.asyncio
async def test_collection_inspection_expires_when_directory_changes(db, monkeypatch):
    ScriptedModel(monkeypatch, {"向联系人列表里所有人收款": {
        "action": "create", "payers": everyone("联系人列表里所有人")}})
    context = ToolContext(user_id="U1", db=db, page=PageContext(),
                          original_message="向联系人列表里所有人收款", request_id=uuid4().hex,
                          session_id=uuid4().hex)
    inspection = await nova_service.execute_tool({
        "function": {"name": "inspect_collection_request", "arguments": "{}"}}, context)
    assert "error" not in inspection
    await add_contact(db, "C2", "李四", "13900000002", "小李")
    context = replace(context, current_tool_results=[{
        "name": "inspect_collection_request", "result": inspection}])
    with pytest.raises(collection_manager.CollectionError) as error:
        await collection_manager.create_collection_draft(context)
    assert error.value.code == "COLLECTION_INSPECTION_REQUIRED"
    assert (await db.scalars(select(NovaSessionORM))).all() == []


@pytest.mark.asyncio
async def test_unbound_everyone_clarifies_then_answer_continues_request(db, monkeypatch):
    model = ScriptedModel(monkeypatch, {
        "向每个人收款100": {"action": "clarify", "clarify_reason": "payer_scope"},
        "全部已保存联系人": {"action": "create", "continues_clarification": True,
                       "payers": everyone("全部已保存联系人"), "amount_quote": "100"},
    })
    await add_contact(db, "C2", "李四", "13900000002", "小李")
    async with client_for(db) as client:
        first = await say(client, "向每个人收款100")
        assert "哪一组人" in first["reply"]
        assert not first.get("collection_cards")
        assert (await db.scalars(select(NovaSessionORM))).all() == []
        second = await say(client, "全部已保存联系人", first)
    assert model.interpreted[-1]["previous_user_message"] == "向每个人收款100"
    card = second["collection_cards"][0]
    assert card["target_scope"] == "all_saved_contacts"
    assert card["requested_names"] == []
    assert card["selected_payer_user_ids"] == ["U2"]
    assert card["amount_fen"] == 10000


@pytest.mark.asyncio
async def test_failed_clarification_answer_can_be_retried(db, monkeypatch):
    ScriptedModel(monkeypatch, {
        "向每个人收款100": {"action": "clarify", "clarify_reason": "payer_scope"},
        "全部已保存联系人": {"action": "create", "continues_clarification": True,
                       "payers": everyone("全部已保存联系人"), "amount_quote": "100"},
    })
    async with client_for(db) as client:
        first = await say(client, "向每个人收款100")
        await bank_conversations.append_turn(
            db, "U1", first["session_id"], uuid4().hex, "全部已保存联系人",
            "**收款草稿**\n暂时无法查询，请稍后重试；未取得可核验结果。")
        retried = await say(client, "全部已保存联系人", first)
    assert retried["collection_cards"][0]["amount_fen"] == 10000


@pytest.mark.asyncio
async def test_history_amount_is_not_reused_without_continuing_clarification(db, monkeypatch):
    ScriptedModel(monkeypatch, {
        "向每个人收款100": {"action": "clarify", "clarify_reason": "payer_scope"},
        "向李四收款": {"action": "create", "payers": named("set", "李四", "李四"), "amount_quote": "100"},
    })
    async with client_for(db) as client:
        first = await say(client, "向每个人收款100")
        second = await say(client, "向李四收款", first)
    card = second["collection_cards"][0]
    assert card["amount_fen"] is None
    assert "金额" in second["reply"] and "未能从你的原话中确认" in second["reply"]


@pytest.mark.asyncio
async def test_chat_can_set_aa_edit_fields_and_cancel_collection(db, monkeypatch):
    ScriptedModel(monkeypatch, {
        "向小李AA收100元": {"action": "create", "payers": named("set", "小李", "小李"),
                        "amount_quote": "100元", "amount_mode": "split_total"},
        "每人100元": {"action": "update", "amount_quote": "100元", "amount_mode": "per_person"},
        "把这次收款改成每人收50元，备注改为午餐": {"action": "update", "amount_quote": "50元",
                                      "amount_mode": "per_person", "note": "午餐"},
        "备注改为晚饭": {"action": "update", "note": "晚饭"},
        "清空备注": {"action": "update", "clear_note": True},
        "撤销这次收款": {"action": "cancel"},
    })
    await add_contact(db, "C2", "李四", "13900000002", "小李")
    async with client_for(db) as client:
        first = await say(client, "向小李AA收100元")
        card = first["collection_cards"][0]
        assert card["split_equally"] is True
        assert card["selected_payer_user_ids"] == ["U2"]
        per_person = await say(client, "每人100元", first)
        assert per_person["collection_cards"][0]["split_equally"] is False
        changed = await say(client, "把这次收款改成每人收50元，备注改为午餐", per_person)
        edited = changed["collection_cards"][0]
        assert edited["collection_id"] == card["collection_id"]
        assert edited["split_equally"] is False
        assert edited["amount_fen"] == 5000
        assert edited["note"] == "午餐"
        note_only = await say(client, "备注改为晚饭", changed)
        assert note_only["collection_cards"][0]["note"] == "晚饭"
        cleared = await say(client, "清空备注", note_only)
        assert cleared["collection_cards"][0]["note"] == ""
        cancelled = await say(client, "撤销这次收款", cleared)
        assert cancelled["collection_cards"][0]["status"] == "CANCELLED"
        denied = await client.post(
            f"/api/v1/nova/task-sessions/{first['session_id']}/collections/{card['collection_id']}/send",
            json={"session_version": cancelled["session_version"], "payer_user_ids": ["U2"],
                  "receive_account_id": "A1", "amount": 50, "split_equally": False, "note": "午餐"})
        assert denied.status_code == 409
        assert denied.json()["detail"]["code"] == "COLLECTION_NOT_EDITABLE"


@pytest.mark.asyncio
async def test_chat_targets_one_of_multiple_collection_drafts_and_can_create_another(db, monkeypatch):
    def card_id(index):
        return lambda payload: payload["open_collection_cards"][index]["collection_id"]

    ScriptedModel(monkeypatch, {
        "向小李收100元": {"action": "create", "payers": named("set", "小李", "小李"), "amount_quote": "100元"},
        "向王五收200元": {"action": "create", "payers": named("set", "王五", "王五"), "amount_quote": "200元"},
        # A guess without any identifying words in the utterance is not accepted.
        "改成AA": lambda payload: {"action": "update", "amount_mode": "split_total",
                                 "target_card_id": card_id(0)(payload)},
        "把小李那笔收款改成AA": lambda payload: {"action": "update", "amount_mode": "split_total",
                                       "target_card_id": card_id(0)(payload), "target_quote": "小李那笔"},
        "撤销第二张收款": lambda payload: {"action": "cancel", "target_card_id": card_id(1)(payload),
                                    "target_quote": "第二张"},
        "向李四收300元": {"action": "create", "payers": named("set", "李四", "李四"), "amount_quote": "300元"},
    })
    await add_contact(db, "C2", "李四", "13900000002", "小李")
    async with client_for(db) as client:
        first = await say(client, "向小李收100元")
        second = await say(client, "向王五收200元", first)
        assert len(second["collection_cards"]) == 2
        ambiguous = await say(client, "改成AA", second)
        assert "第几张或原付款人" in ambiguous["reply"]
        assert all(not card["split_equally"] for card in ambiguous["collection_cards"])
        targeted = await say(client, "把小李那笔收款改成AA", second)
        assert targeted["collection_cards"][0]["split_equally"] is True
        assert targeted["collection_cards"][1]["split_equally"] is False
        cancelled = await say(client, "撤销第二张收款", targeted)
        assert [card["status"] for card in cancelled["collection_cards"]] == ["DRAFT", "CANCELLED"]
        another = await say(client, "向李四收300元", cancelled)
    assert len(another["collection_cards"]) == 3
    assert another["collection_cards"][2]["status"] == "DRAFT"


@pytest.mark.asyncio
async def test_chat_can_edit_payers_and_receive_account(db, monkeypatch):
    ScriptedModel(monkeypatch, {
        "向李四收100元": {"action": "create", "payers": named("set", "李四", "李四"), "amount_quote": "100元"},
        "王五也要付": {"action": "update", "payers": named("add", "王五", "王五")},
        "把收款账户换成尾号1234的银行卡": {"action": "update", "receive_account_id": "A3",
                                 "receive_account_quote": "尾号1234"},
        "李四不用了，只找王五": {"action": "update", "payers": named("set", "只找王五", "王五")},
    })
    await add_contact(db, "C2", "李四", "13900000002")
    await add_wangwu(db)
    db.add(AccountORM(account_id="A3", user_id="U1", account_type="checking",
                      card_number="6222000000001234", balance=100, daily_transfer_used=0,
                      daily_transfer_limit=50000, status="active"))
    await db.commit()
    async with client_for(db) as client:
        first = await say(client, "向李四收100元")
        added = await say(client, "王五也要付", first)
        assert set(added["collection_cards"][0]["selected_payer_user_ids"]) == {"U2", "U3"}
        switched = await say(client, "把收款账户换成尾号1234的银行卡", added)
        assert switched["collection_cards"][0]["receive_account_id"] == "A3"
        assert "收款账户改为尾号1234" in switched["reply"]
        replaced = await say(client, "李四不用了，只找王五", switched)
    assert replaced["collection_cards"][0]["requested_names"] == ["王五"]
    assert replaced["collection_cards"][0]["selected_payer_user_ids"] == ["U3"]


@pytest.mark.asyncio
async def test_model_failure_does_not_fall_back_to_keyword_parsing(db, monkeypatch):
    model = ScriptedModel(monkeypatch, {})

    async def broken(**kwargs):
        if kwargs["schema"] is nova_service.RequestRoute:
            return await model.structured(**kwargs)
        raise RuntimeError("model down")

    monkeypatch.setattr(nova_service.llm, "generate_structured", broken)
    async with client_for(db) as client:
        result = await say(client, "向李四收100元")
    assert "暂时不可用" in result["reply"]
    assert not result.get("collection_cards")
    assert (await db.scalars(select(NovaSessionORM))).all() == []


@pytest.mark.asyncio
async def test_offline_mode_does_not_guess_collection(db, monkeypatch):
    monkeypatch.setenv("LLM_MODE", "offline")
    async with client_for(db) as client:
        result = await say(client, "向李四收100元")
    assert not result.get("collection_cards")
    assert (await db.scalars(select(NovaSessionORM))).all() == []
