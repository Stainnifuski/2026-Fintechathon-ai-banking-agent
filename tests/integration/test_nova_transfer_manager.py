"""Unified NOVA transfer drafts and handoff to the existing state machine."""
from uuid import uuid4
from datetime import datetime, timezone, timedelta

import pytest
import httpx
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.integration.test_task_transfer_flow import db, InProcessBankApi
from backend.app.models import ContactORM, TransferTaskORM, UserORM, AccountORM, TransactionORM
from backend.app.services.ai.nlu import (
    Extraction, NLUEngine, TransferTaskExtraction, TransferTurnExtraction,
)
from backend.app.services.ai import nova
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.schemas.nova import PageContext
from backend.app.services.bank import nova_conversations as bank_conversations
from backend.app.services.bank import bill_service
from backend.app.services.bank.transaction_display import transfer_counterparty_names, display_counterparty
from backend.app.schemas.bills import BillQuery
from backend.app.services.ai.transfer_manager import (
    ManagerError, _split, get_session, process_message, wants_transfer,
    change_task_form, cancel_chat_task,
)
from backend.app.services.bank.transfer_cards import transfer_card_number
from backend.app.routers import nova as nova_router
from backend.app.database.connection import get_db
from backend.app.middleware.auth import get_current_user


class TransferToolModel:
    async def generate_with_tools(self, **kwargs):
        class Result:
            pass
        result = Result()
        if kwargs["messages"][-1]["role"] == "tool":
            result.data = {"role": "assistant", "content": "已按要求处理，请核对转账任务卡。"}
        else:
            result.data = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call-transfer", "type": "function",
                "function": {"name": "manage_transfer_request", "arguments": "{}"},
            }]}
        return result


def use_transfer_planner(monkeypatch):
    monkeypatch.setattr(nova, "get_mode", lambda: "model")
    monkeypatch.setattr(nova, "llm", TransferToolModel())


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
async def test_inline_form_fill_edit_and_close_task(db):
    draft = await send(db, "转钱")
    task_id = draft["tasks"][0]["chat_task_id"]
    assert draft["tasks"][0]["form"]["recipient"] == ""
    request_id = uuid4().hex
    fields = dict(user_id="U1", session_id=draft["session_id"], chat_task_id=task_id,
                  session_version=draft["session_version"], request_id=request_id,
                  recipient="李四", amount_yuan="100", phone="13900000002", card=None,
                  clear_phone=False, clear_card=False, payer_account_id="A1", note="测试")
    planned = await change_task_form(db, InProcessBankApi(db), **fields)
    assert planned["tasks"][0]["transfer_task_id"]
    assert planned["tasks"][0]["form"]["phone_masked"] != "13900000002"
    duplicate = await change_task_form(db, InProcessBankApi(db), **fields)
    assert duplicate["session_version"] == planned["session_version"]
    fields.update(session_version=planned["session_version"], request_id=uuid4().hex,
                  amount_yuan="150", phone=None)
    modified = await change_task_form(db, InProcessBankApi(db), **fields)
    assert modified["tasks"][0]["task"]["confirmation_card"]["amount"] == 150
    closed = await cancel_chat_task(db, InProcessBankApi(db), user_id="U1",
                                    session_id=draft["session_id"], chat_task_id=task_id,
                                    session_version=modified["session_version"], request_id=uuid4().hex)
    assert closed["tasks"][0]["status"] == "CANCELLED"
    assert closed["tasks"][0]["task"]["outcome"] == "CANCELLED"


@pytest.mark.asyncio
async def test_focused_vague_draft_is_filled_by_next_chat_message(db):
    draft = await send(db, "转钱")
    focused = draft["tasks"][0]["chat_task_id"]
    filled = await send(db, "给李四手机号13900000002转100元", draft, focus_task_id=focused)
    assert len(filled["tasks"]) == 1
    assert filled["tasks"][0]["chat_task_id"] == focused
    assert filled["tasks"][0]["transfer_task_id"]


@pytest.mark.asyncio
async def test_pending_card_accepts_natural_language_field_reply(db):
    draft = await send(db, "转钱")
    task_id = draft["tasks"][0]["chat_task_id"]
    named = await send(db, "收款人是李四", draft, focus_task_id=task_id)
    assert len(named["tasks"]) == 1
    assert named["tasks"][0]["form"]["recipient"] == "李四"
    assert "转账金额" in named["tasks"][0]["missing_fields"]


@pytest.mark.asyncio
async def test_half_available_balance_creates_draft_and_quotes_from_bank_api(db):
    draft = await send(db, "把我银行卡里一半的钱转给赵六。")
    task = draft["tasks"][0]
    assert task["recipient"] == "赵六"
    assert task["amount_fen"] == 250000
    assert task["form"]["amount_rule"] == "付款账户可用余额的一半"
    assert task["form"]["amount_basis_available_fen"] == 500000
    assert task["transfer_task_id"] is None
    assert "完整手机号或完整银行卡号" in task["missing_fields"]


@pytest.mark.asyncio
async def test_legacy_transfer_account_code_is_displayed_as_verified_person_name(db):
    when = datetime(2026, 10, 10, tzinfo=timezone.utc)
    outgoing = TransactionORM(tx_id="DISPLAY-OUT", account_id="A1", type="transfer_out",
                              amount_fen=10000, counterparty="A2", category="transfer",
                              note=None, timestamp=when, status="completed", channel="app")
    incoming = TransactionORM(tx_id="DISPLAY-IN", account_id="A2", type="transfer_in",
                              amount_fen=10000, counterparty="A1", category="transfer",
                              note=None, timestamp=when, status="completed", channel="app")
    db.add_all([outgoing, incoming])
    await db.commit()
    names = await transfer_counterparty_names(db, [outgoing, incoming])
    assert display_counterparty(outgoing, names) == "李四"
    assert display_counterparty(incoming, names) == "张三"
    page = await bill_service.transactions(db, "U1", BillQuery(period="month", date="2026-10"),
                                           as_of=when+timedelta(seconds=1))
    assert page["items"][0]["counterparty"] == "李四"


@pytest.mark.asyncio
async def test_half_balance_requires_payer_choice_when_multiple_accounts(db):
    db.add(AccountORM(account_id="A4", user_id="U1", account_type="savings", card_number="4",
                      balance=1000, daily_transfer_used=0, daily_transfer_limit=50000, status="active"))
    await db.commit()
    draft = await send(db, "把我银行卡里一半的钱转给李四")
    task = draft["tasks"][0]
    assert task["amount_fen"] is None
    assert "付款账户" in task["missing_fields"]
    assert len(task["candidates"]) == 2
    selected = await send(db, "选择", draft, candidate_id=task["candidates"][1]["candidate_id"],
                          focus_task_id=task["chat_task_id"])
    assert selected["tasks"][0]["amount_fen"] == 50000
    assert "完整手机号或完整银行卡号" in selected["tasks"][0]["missing_fields"]


@pytest.mark.asyncio
async def test_half_balance_requotes_before_plan_and_then_freezes_amount(db):
    draft = await send(db, "把我银行卡里一半的钱转给李四")
    assert draft["tasks"][0]["amount_fen"] == 250000
    account = await db.get(AccountORM, "A1")
    account.balance = 4000
    await db.commit()
    planned = await send(db, "13900000002", draft,
                         focus_task_id=draft["tasks"][0]["chat_task_id"])
    task = planned["tasks"][0]
    assert task["amount_fen"] == 200000
    assert task["task"]["confirmation_card"]["amount"] == 2000
    assert (await db.get(AccountORM, "A1")).balance == 4000


@pytest.mark.asyncio
async def test_half_in_note_does_not_change_explicit_amount(db):
    draft = await send(db, "给李四转100元，备注一半的钱")
    assert draft["tasks"][0]["amount_fen"] == 10000
    assert draft["tasks"][0]["form"]["amount_rule"] is None


@pytest.mark.asyncio
async def test_form_clear_saved_contact_number_returns_to_missing_state(db):
    db.add(ContactORM(contact_id="NOVA-CLEAR", user_id="U1", name="李四", nickname="老李",
                      phone="13900000002", account_number=transfer_card_number("A2"),
                      phone_known=True, card_known=False, bank="AI 模拟银行", transfer_count=1))
    await db.commit()
    planned = await send(db, "给老李转100元")
    target = planned["tasks"][0]
    cleared = await change_task_form(db, InProcessBankApi(db), user_id="U1",
                                     session_id=planned["session_id"], chat_task_id=target["chat_task_id"],
                                     session_version=planned["session_version"], request_id=uuid4().hex,
                                     recipient=target["form"]["recipient"], amount_yuan="100",
                                     phone=None, card=None, clear_phone=True, clear_card=False,
                                     payer_account_id=target["form"]["payer_account_id"], note="")
    assert cleared["tasks"][0]["transfer_task_id"] is None
    assert cleared["tasks"][0]["form"]["phone_masked"] is None
    assert "完整手机号或完整银行卡号" in cleared["tasks"][0]["missing_fields"]


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
async def test_model_decides_task_count_and_receives_all_open_tasks(db):
    observed = []
    observed_contexts = []

    class TaskPlanner:
        async def extract_tasks(self, message, context):
            observed_contexts.append(context)
            observed.append(context["pending_tasks"])
            if not context["pending_tasks"]:
                return TransferTurnExtraction(tasks=[
                    TransferTaskExtraction(
                        task_action="create", intent="transfer",
                        recipient="李四", amount_text="100元",
                    ),
                    TransferTaskExtraction(
                        task_action="create", intent="transfer",
                        recipient="王五", amount_text="200元",
                    ),
                ])
            target = context["pending_tasks"][1]
            return TransferTurnExtraction(tasks=[
                TransferTaskExtraction(
                    task_action="update", chat_task_id=target["chat_task_id"],
                    intent="modify", amount_text="300元",
                ),
            ])

    nlu = NLUEngine(TaskPlanner())
    first = await process_message(
        db, InProcessBankApi(db), nlu, user_id="U1",
        message="分别安排李四100元以及王五200元",
        request_id=uuid4().hex, session_id=None, session_version=0,
        conversation_history=[
            {"role": "user", "content": "刚才提到手机号13900000002"},
            {"role": "assistant", "content": "请继续说明转账要求"},
        ],
        page_context={"page": "transfer"},
        current_tool_results=[{"name": "get_current_time", "result": {"local_time": "2026-10-20T15:00:00+08:00"}}],
        goal_plan={"execution_mode": "parallel", "goals": [{"goal_id": "transfer"}]},
    )
    assert len(first["tasks"]) == 2
    assert [task["amount_fen"] for task in first["tasks"]] == [10000, 20000]
    assert observed_contexts[0]["conversation_history"] == [
        {"role": "user", "content": "刚才提到手机号[手机号已隐藏]"},
        {"role": "assistant", "content": "请继续说明转账要求"},
    ]
    assert observed_contexts[0]["page_context"] == {"page": "transfer"}
    assert observed_contexts[0]["current_tool_results"][0]["name"] == "get_current_time"
    assert observed_contexts[0]["goal_plan"]["execution_mode"] == "parallel"
    second = await process_message(
        db, InProcessBankApi(db), nlu, user_id="U1",
        message="把王五这笔改成300元",
        request_id=uuid4().hex, session_id=first["session_id"],
        session_version=first["session_version"],
    )
    assert len(observed[1]) == 2
    assert {task["chat_task_id"] for task in observed[1]} == {
        task["chat_task_id"] for task in first["tasks"]
    }
    assert second["tasks"][1]["amount_fen"] == 30000


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
    use_transfer_planner(monkeypatch)
    monkeypatch.setattr(nova_router, "get_bank_api", lambda: InProcessBankApi(db))
    app = FastAPI()
    app.include_router(nova_router.router)
    async def current_user():
        return {"user_id": "U1"}
    async def current_db():
        yield db
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = current_db
    first_request_id = uuid4().hex
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/nova/chat", json={
            "message": "给李四转100元", "request_id": first_request_id,
        })
        assert response.status_code == 200
        draft = response.json()
        assert draft["tasks"][0]["status"] == "COLLECTING"
        assert draft["tasks"][0]["origin_request_id"] == first_request_id
        history = await bank_conversations.get(db, "U1", draft["session_id"])
        assert history["messages"][0]["request_id"] == first_request_id
        assert history["messages"][1]["request_id"] == first_request_id
        restored = await client.get(f"/api/v1/nova/task-sessions/{draft['session_id']}")
        assert restored.status_code == 200
        assert restored.json()["tasks"][0]["chat_task_id"] == draft["tasks"][0]["chat_task_id"]
        assert restored.json()["tasks"][0]["origin_request_id"] == first_request_id
        streamed = await client.post("/api/v1/nova/chat/stream", json={
            "message": "13900000002", "request_id": uuid4().hex,
            "session_id": draft["session_id"], "session_version": draft["session_version"],
        })
        assert streamed.status_code == 200
        assert "event: done" in streamed.text
        assert "WAITING_CONFIRMATION" in streamed.text


@pytest.mark.asyncio
async def test_nova_http_inline_form_and_cancel(db, monkeypatch):
    use_transfer_planner(monkeypatch)
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
        draft = (await client.post("/api/v1/nova/chat", json={
            "message": "转钱", "request_id": uuid4().hex,
        })).json()
        session_id = draft["session_id"]
        task_id = draft["tasks"][0]["chat_task_id"]
        saved = await client.post(f"/api/v1/nova/task-sessions/{session_id}/tasks/{task_id}/form", json={
            "request_id": uuid4().hex, "session_version": draft["session_version"],
            "recipient": "李四", "amount_yuan": "100", "phone": "13900000002",
            "payer_account_id": "A1",
        })
        assert saved.status_code == 200, saved.text
        planned = saved.json()
        assert planned["tasks"][0]["transfer_task_id"]
        closed = await client.post(f"/api/v1/nova/task-sessions/{session_id}/tasks/{task_id}/cancel", json={
            "request_id": uuid4().hex, "session_version": planned["session_version"],
        })
        assert closed.status_code == 200, closed.text
        assert closed.json()["tasks"][0]["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_nova_http_half_balance_request_returns_task_card(db, monkeypatch):
    use_transfer_planner(monkeypatch)
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
            "message": "把我银行卡里一半的钱转给赵六。", "request_id": uuid4().hex,
        })
    assert response.status_code == 200, response.text
    task = response.json()["tasks"][0]
    assert task["amount_fen"] == 250000
    assert "完整手机号或完整银行卡号" in task["missing_fields"]


def test_transfer_intent_does_not_capture_read_only_transfer_history():
    assert wants_transfer("转钱")
    assert wants_transfer("给李四转100元")
    assert wants_transfer("把我银行卡里一半的钱转给赵六")
    assert not wants_transfer("请查看我的转账记录")
    assert not wants_transfer("我给李四转过多少钱")


def test_single_transfer_with_fronted_conditions_is_not_split_at_recipient_clause():
    message = "按照最近一次转的钱数，从我第二张银行卡里，给赵六转钱"
    assert _split(message) == [message]
    assert _split("给李四转100元，给王五转200元") == ["给李四转100元", "给王五转200元"]


@pytest.mark.asyncio
async def test_comma_separated_each_transfer_creates_four_independent_drafts(db):
    result = await send(db, "给赵六，李四，王五，周八各转账100元。")
    assert len(result["tasks"]) == 4
    assert [task["recipient"] for task in result["tasks"]] == ["赵六", "李四", "王五", "周八"]
    assert [task["amount_fen"] for task in result["tasks"]] == [10000] * 4


@pytest.mark.asyncio
async def test_chat_can_modify_and_clear_focused_draft_note(db):
    draft = await send(db, "给李四转100元")
    task_id = draft["tasks"][0]["chat_task_id"]
    modified = await send(db, "修改备注为房租", draft, focus_task_id=task_id)
    assert len(modified["tasks"]) == 1
    assert modified["tasks"][0]["form"]["note"] == "房租"
    cleared = await send(db, "清空备注", modified, focus_task_id=task_id)
    assert cleared["tasks"][0]["form"]["note"] == ""


@pytest.mark.asyncio
async def test_model_derived_amount_and_payer_are_revalidated_before_transfer_draft(db, monkeypatch):
    source = TransactionORM(
        tx_id="SOURCE-TRANSFER", account_id="A1", type="transfer_out",
        amount_fen=12345, counterparty="A2", category="transfer", note=None,
        timestamp=datetime(2026, 10, 19, tzinfo=timezone.utc),
        status="completed", channel="app",
    )
    db.add_all([
        source,
        AccountORM(account_id="A4", user_id="U1", account_type="savings", card_number="4",
                   balance=1000, daily_transfer_used=0, daily_transfer_limit=50000, status="active"),
    ])
    await db.commit()

    class PlannedNlu:
        mode = "model"

        async def extract_tasks(self, message, context):
            return TransferTurnExtraction(tasks=[TransferTaskExtraction(
                task_action="create", intent="transfer",
                recipient="李四", recipient_phone="13900000002",
                payer="我第二张银行卡", amount_text=None,
            )])

    monkeypatch.setattr(nova, "get_nlu", lambda: PlannedNlu())
    context = ToolContext(
        "U1", db, PageContext(),
        original_message="按照最近一次转的钱数，从我第二张银行卡里，给李四手机号13900000002转钱",
        request_id=uuid4().hex, session_id="NOVA-DERIVED-TRANSFER",
        bank_api=InProcessBankApi(db),
    )
    result = await nova.manage_transfer_request(nova.TransferRequestArguments(
        amount_fen=12345, amount_source_tx_id=source.tx_id, payer_account_id="A4",
    ), context)
    task = result["tasks"][0]
    assert len(result["tasks"]) == 1
    assert task["amount_fen"] == 12345
    assert task["form"]["payer_account_id"] == "A4"
    assert task["task"]["phase"] == "WAITING_CONFIRMATION"


@pytest.mark.asyncio
async def test_history_reference_reuses_server_verified_recipient_and_amount(db, monkeypatch):
    source = TransactionORM(
        tx_id="SOURCE-HISTORY-REFERENCE", account_id="A1", type="transfer_out",
        amount_fen=23456, counterparty="A2", category="transfer", note=None,
        timestamp=datetime(2026, 10, 18, tzinfo=timezone.utc),
        status="completed", channel="app",
    )
    db.add(source)
    await db.commit()

    class ReferenceNlu:
        mode = "model"

        async def extract_tasks(self, message, context):
            assert context["trusted_history_reference"] == {
                "resolved": True, "reuse_recipient": True, "reuse_amount": True,
            }
            return TransferTurnExtraction(tasks=[TransferTaskExtraction(
                task_action="create", intent="transfer",
            )])

    monkeypatch.setattr(nova, "get_nlu", lambda: ReferenceNlu())
    context = ToolContext(
        "U1", db, PageContext(),
        original_message="给倒数第六笔转账的人转相应的钱数",
        request_id=uuid4().hex, session_id="NOVA-HISTORY-REFERENCE",
        bank_api=InProcessBankApi(db),
    )
    result = await nova.manage_transfer_request(nova.TransferRequestArguments(
        history_reference={
            "tx_id": source.tx_id,
            "reuse_recipient": True,
            "reuse_amount": True,
        },
    ), context)
    task = result["tasks"][0]
    assert task["recipient"] == "李四"
    assert task["amount_fen"] == 23456
    assert task["task"]["operation"]["to_account"] == "A2"
    assert task["task"]["phase"] == "WAITING_CONFIRMATION"


@pytest.mark.asyncio
async def test_model_plans_relative_transfer_before_bank_read_and_redacts_identifiers(db, monkeypatch):
    events = []

    class PlanningModel:
        async def generate_with_tools(self, **kwargs):
            class Result:
                pass
            result = Result()
            if kwargs["messages"][-1]["role"] == "tool":
                result.data = {"role": "assistant", "content": "请核对任务卡。"}
            else:
                events.append("model_plan")
                assert "13900000002" not in kwargs["messages"][-1]["content"]
                assert any(tool["function"]["name"] == "manage_transfer_request"
                           for tool in kwargs["tools"])
                result.data = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "transfer", "type": "function",
                    "function": {"name": "manage_transfer_request", "arguments": "{}"},
                }]}
            return result

    class ObservedBank(InProcessBankApi):
        async def transfer_funding_accounts(self, **kwargs):
            events.append("read_funding_accounts")
            return await super().transfer_funding_accounts(**kwargs)

    monkeypatch.setattr(nova, "get_mode", lambda: "model")
    monkeypatch.setattr(nova, "llm", PlanningModel())
    monkeypatch.setattr(nova_router, "get_bank_api", lambda: ObservedBank(db))
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
            "message": "把我银行卡里一半的钱转给赵六，手机号13900000002", "request_id": uuid4().hex,
        })
    assert response.status_code == 200, response.text
    assert events == ["model_plan", "read_funding_accounts"]
    assert response.json()["tasks"][0]["amount_fen"] == 250000


@pytest.mark.asyncio
async def test_model_keeps_transfer_history_read_only_by_not_calling_write_tool(monkeypatch):
    class ReadPlanner:
        async def generate_with_tools(self, **kwargs):
            class Result:
                data = {"role": "assistant", "content": "我会先查询转账流水。"}
            return Result()

    monkeypatch.setattr(nova, "get_mode", lambda: "model")
    result = await nova.chat("请查看我的转账记录", [], model=ReadPlanner())
    assert '尚未取得可核验' in result['reply']
    assert result['grounding']['status'] == 'abstained'
    assert result["tool_calls"] == []


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
