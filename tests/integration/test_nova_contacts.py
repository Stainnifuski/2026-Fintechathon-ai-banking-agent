"""NOVA contact reads share the bank's owner and number-knowledge rules."""

import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.models import ContactORM, TransactionORM
from backend.app.models.workflow import (
    TransferOperationORM, TransferPlanORM, TransferStepORM, TransferTaskORM,
)
from backend.app.schemas.nova import PageContext
from backend.app.services.ai import nova
from backend.app.services.ai.nova_tools import ToolContext
from backend.app.services.bank import contacts
from backend.app.services.bank.transfer_cards import transfer_card_number
from tests.integration.test_task_transfer_flow import db


def call(name, arguments=None):
    return {"function": {"name": name, "arguments": json.dumps(arguments or {})}}


@pytest.mark.asyncio
async def test_nova_contact_tools_only_return_owned_known_numbers(db, monkeypatch):
    monkeypatch.setattr(contacts, "AsyncSessionLocal", async_sessionmaker(db.bind, expire_on_commit=False))
    card = transfer_card_number("A2")
    db.add_all([
        ContactORM(contact_id="PHONE", user_id="U1", name="李四", nickname="同事",
                   phone="13900000002", account_number="6214****0002", bank="AI 模拟银行",
                   phone_known=True, card_known=False, transfer_count=0),
        ContactORM(contact_id="CARD", user_id="U2", name="张三", nickname="私人联系人",
                   phone="13800000001", account_number=card, bank="AI 模拟银行",
                   phone_known=False, card_known=True, transfer_count=0),
    ])
    await db.commit()
    owner = ToolContext("U1", db, PageContext(page="transfer"))
    other = ToolContext("U2", db, PageContext(page="transfer"))
    result = await nova.execute_tool(call("list_contacts", {"include_history": False}), owner)
    assert result["data"]["total"] == 1
    assert result["data"]["items"][0]["phone_masked"] == "139****0002"
    assert result["data"]["items"][0]["card_masked"] is None
    assert card not in json.dumps(result) and "私人联系人" not in json.dumps(result)
    assert (await nova.execute_tool(call("search_contacts", {"query": "同事"}), owner))["data"]["total"] == 1
    assert (await nova.execute_tool(call("search_contacts", {"query": card}), owner))["data"]["total"] == 0
    assert (await nova.execute_tool(call("search_contacts", {"query": "13900000002"}), owner))["data"]["total"] == 1
    assert (await nova.execute_tool(call("list_contacts", {"include_history": False}), other))["data"]["items"][0]["phone_masked"] is None
    assert (await nova.execute_tool(call("list_contacts", {"user_id": "U2"}), owner))["error"] == "INVALID_ARGUMENTS"
    assert (await nova.execute_tool(call("search_contacts", {"query": "  "}), owner))["error"] == "INVALID_ARGUMENTS"
    assert (await nova.execute_tool(call("list_contacts")))["error"] == "AUTH_CONTEXT_REQUIRED"


@pytest.mark.asyncio
async def test_nova_history_reads_only_proven_transfer_method(db, monkeypatch):
    monkeypatch.setattr(contacts, "AsyncSessionLocal", async_sessionmaker(db.bind, expire_on_commit=False))
    db.add(TransferTaskORM(task_id="NOVAPHONE", user_id="U1", phase="FINISHED", outcome="SUCCESS"))
    await db.flush()
    db.add(TransferPlanORM(plan_id="NOVAPLAN", task_id="NOVAPHONE", plan_version=1,
                           params={"recipient_phone": "13900000002"}))
    await db.flush()
    db.add(TransferStepORM(step_id="NOVASTEP", task_id="NOVAPHONE", plan_id="NOVAPLAN",
                           kind="TRANSFER", status="FINISHED"))
    await db.flush()
    db.add(TransferOperationORM(operation_id="NOVAOP", task_id="NOVAPHONE",
                                step_id="NOVASTEP", plan_version=1, status="SUCCEEDED",
                                from_account="A1", to_account="A2", amount_fen=100,
                                param_digest="nova-phone"))
    db.add(TransactionORM(tx_id="NOVATX", operation_id="NOVAOP", account_id="A1",
                          type="transfer_out", amount_fen=100, counterparty="A2",
                          category="transfer", status="completed", channel="app"))
    await db.commit()
    owner = ToolContext("U1", db, PageContext(page="transfer"))
    data = (await nova.execute_tool(call("list_contacts"), owner))["data"]
    assert data["total"] == 1
    assert data["items"][0]["source"] == "transfer_history"
    assert data["items"][0]["phone_masked"] == "139****0002"
    assert data["items"][0]["card_masked"] is None
    assert (await nova.execute_tool(call("search_contacts", {"query": transfer_card_number("A2")}), owner))["data"]["total"] == 0
    assert (await nova.execute_tool(call("list_contacts", {"include_history": False}), owner))["data"]["total"] == 0
    monkeypatch.setenv("LLM_MODE", "offline")
    reply = await nova.chat("我的联系人有哪些？", [], context=owner)
    assert reply["tool_calls"][0]["name"] == "list_contacts"
    assert "139****0002" in reply["reply"].replace('\\', '') and transfer_card_number("A2")[-4:] not in reply["reply"]
