"""Recipient shortcut history is owner-scoped and can be cleared without deleting ledger rows."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.main import app
from backend.app.middleware.auth import get_current_user
from backend.app.models.transaction import TransactionORM
from backend.app.models.workflow import (
    TransferOperationORM, TransferPlanORM, TransferStepORM, TransferTaskORM,
)
from backend.app.services.bank import contacts
from backend.app.services.bank.transfer_cards import transfer_card_number
from tests.integration.test_task_transfer_flow import db


@pytest.mark.asyncio
async def test_history_shortcut_can_be_cleared_without_deleting_transfer(db, monkeypatch):
    monkeypatch.setattr(contacts, "AsyncSessionLocal", async_sessionmaker(db.bind, expire_on_commit=False))
    db.add(TransferTaskORM(task_id="TASKCARD", user_id="U1", phase="FINISHED", outcome="SUCCESS"))
    await db.flush()
    db.add(TransferPlanORM(plan_id="PLANCARD", task_id="TASKCARD", plan_version=1,
                           params={"recipient_card_confirmed": True}))
    await db.flush()
    db.add(TransferStepORM(step_id="STEPCARD", task_id="TASKCARD", plan_id="PLANCARD",
                           kind="TRANSFER", status="FINISHED"))
    await db.flush()
    db.add(TransferOperationORM(operation_id="OPCARD", task_id="TASKCARD", step_id="STEPCARD",
                                plan_version=1, status="SUCCEEDED", from_account="A1",
                                to_account="A2", amount_fen=100, param_digest="card-test"))
    db.add(TransactionORM(tx_id="HISTORY1", operation_id="OPCARD", account_id="A1", type="transfer_out",
                          amount_fen=100, counterparty="A2", category="transfer",
                          status="completed", channel="app"))
    await db.commit()
    identity = {"user_id": "U1"}
    app.dependency_overrides[get_current_user] = lambda: identity
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            result = await client.get("/api/v1/users/transfer-recipients/history")
            assert result.status_code == 200
            assert len(result.json()) == 1
            assert result.json()[0]["account_id"] == "A2"
            assert result.json()[0]["user_id"] == "U2"
            assert result.json()[0]["phone_masked"] is None
            assert result.json()[0]["phone_number"] is None
            identity["user_id"] = "U2"
            assert (await client.get("/api/v1/users/transfer-recipients/history")).json() == []
            identity["user_id"] = "U1"
            assert (await client.delete("/api/v1/users/transfer-recipients/history")).status_code == 204
            assert (await client.get("/api/v1/users/transfer-recipients/history")).json() == []
            assert await db.scalar(select(TransactionORM).where(TransactionORM.tx_id == "HISTORY1"))
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_phone_only_then_card_history_preserves_both_contact_methods(db, monkeypatch):
    monkeypatch.setattr(contacts, "AsyncSessionLocal", async_sessionmaker(db.bind, expire_on_commit=False))
    db.add(TransferTaskORM(task_id="TASKPHONE", user_id="U1", phase="FINISHED",
                           outcome="SUCCESS"))
    await db.flush()
    db.add(TransferPlanORM(plan_id="PLANPHONE", task_id="TASKPHONE", plan_version=1,
                           params={"recipient_phone": "13900000002"}))
    await db.flush()
    db.add(TransferStepORM(step_id="STEPPHONE", task_id="TASKPHONE",
                           plan_id="PLANPHONE", kind="TRANSFER", status="FINISHED"))
    await db.flush()
    db.add(TransferOperationORM(operation_id="OPPHONE", task_id="TASKPHONE",
                                step_id="STEPPHONE", plan_version=1, status="SUCCEEDED",
                                from_account="A1", to_account="A2", amount_fen=100,
                                param_digest="phone-test"))
    db.add(TransactionORM(tx_id="TXPHONE", operation_id="OPPHONE", account_id="A1",
                          type="transfer_out", amount_fen=100, counterparty="A2",
                          category="transfer", status="completed", channel="app"))
    await db.commit()
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "U1"}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            phone_history = (await client.get("/api/v1/users/transfer-recipients/history")).json()[0]
            assert phone_history["card_number"] is None
            assert phone_history["phone_number"] == "13900000002"
            created = await client.post("/api/v1/users/contacts", json={
                "phone": phone_history["phone_number"], "account_id": "A2", "nickname": "老李",
            })
            assert created.status_code == 201, created.text
            contact_id = created.json()["contact_id"]
            resolved = (await client.post(f"/api/v1/users/contacts/{contact_id}/resolve")).json()[0]
            assert resolved["preferred_mode"] == "phone"
            assert resolved["card_number"] is None
            assert resolved["phone_number"] == "13900000002"

            db.add(TransferTaskORM(task_id="TASKCARD", user_id="U1", phase="FINISHED",
                                   outcome="SUCCESS"))
            await db.flush()
            db.add(TransferPlanORM(plan_id="PLANCARD", task_id="TASKCARD", plan_version=1,
                                   params={"recipient_card_confirmed": True}))
            await db.flush()
            db.add(TransferStepORM(step_id="STEPCARD", task_id="TASKCARD",
                                   plan_id="PLANCARD", kind="TRANSFER", status="FINISHED"))
            await db.flush()
            db.add(TransferOperationORM(operation_id="OPCARD", task_id="TASKCARD",
                                        step_id="STEPCARD", plan_version=1, status="SUCCEEDED",
                                        from_account="A1", to_account="A2", amount_fen=200,
                                        param_digest="card-test"))
            db.add(TransactionORM(tx_id="TXCARD", operation_id="OPCARD", account_id="A1",
                                  type="transfer_out", amount_fen=200, counterparty="A2",
                                  category="transfer", status="completed", channel="app"))
            await db.commit()
            both = (await client.get("/api/v1/users/transfer-recipients/history")).json()[0]
            assert both["card_number"] == transfer_card_number("A2")
            assert both["phone_number"] == "13900000002"
            resolved = (await client.post(f"/api/v1/users/contacts/{contact_id}/resolve")).json()[0]
            assert resolved["preferred_mode"] == "card"
            assert resolved["card_number"] == transfer_card_number("A2")
            assert resolved["phone_number"] == "13900000002"
            assert (await client.delete("/api/v1/users/transfer-recipients/history")).status_code == 204
            assert (await client.get("/api/v1/users/transfer-recipients/history")).json() == []
            retained = (await client.post(f"/api/v1/users/contacts/{contact_id}/resolve")).json()[0]
            assert retained["card_number"] == transfer_card_number("A2")
            assert retained["phone_number"] == "13900000002"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_recipient_resolution_returns_bank_reason_for_wrong_name(db, monkeypatch):
    from backend.app.services.bank import data_service
    sessions = async_sessionmaker(db.bind, expire_on_commit=False)
    monkeypatch.setattr(contacts, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(data_service, "AsyncSessionLocal", sessions)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "U1"}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            wrong = await client.post("/api/v1/users/recipients/resolve", json={
                "name": "张三", "mode": "card", "number": transfer_card_number("A2"),
            })
            assert wrong.status_code == 409
            assert wrong.json()["detail"]["code"] == "RECIPIENT_MISMATCH"
            right = await client.post("/api/v1/users/recipients/resolve", json={
                "name": "李四", "mode": "phone", "number": "13900000002",
            })
            assert right.status_code == 200
            assert right.json()["account_id"] == "A2"
            missing_phone = await client.post("/api/v1/users/recipients/resolve", json={
                "name": "李四", "mode": "phone", "number": "",
            })
            assert missing_phone.status_code == 422
            assert missing_phone.json()["detail"]["message"] == "请输入完整手机号"
    finally:
        app.dependency_overrides.clear()
