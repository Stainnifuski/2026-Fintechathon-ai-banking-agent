"""联系人快捷选择的用户隔离及银行实时核验。"""

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.main import app
from backend.app.database.connection import get_db
from backend.app.middleware.auth import get_current_user
from backend.app.models import ContactORM, UserORM
from backend.app.services.bank import contacts
from backend.app.services.bank import data_service
from backend.app.services.bank.transfer_cards import transfer_card_number
from backend.app.services.bank.transfer_service import transfer_directory
from tests.integration.test_task_transfer_flow import db


@pytest.mark.asyncio
async def test_contact_lifecycle_is_owner_scoped_and_resolves_current_account(db, monkeypatch):
    monkeypatch.setattr(contacts, "AsyncSessionLocal", async_sessionmaker(db.bind, expire_on_commit=False))
    monkeypatch.setattr(data_service, "AsyncSessionLocal", async_sessionmaker(db.bind, expire_on_commit=False))
    identity = {"user_id": "U1"}
    app.dependency_overrides[get_current_user] = lambda: identity
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/users/recipients")).json() == []
            number = transfer_card_number("A2")
            assert len(number) == 16 and number.isdigit()
            found = await client.post("/api/v1/users/recipients/card", json={"card_number": number})
            assert found.status_code == 200 and found.json()[0]["account_id"] == "A2"
            assert "139" not in found.json()[0]["label"]
            phone_found = await client.post("/api/v1/users/recipients/phone", json={"phone": "13900000002"})
            assert phone_found.status_code == 200
            assert number[-4:] not in phone_found.json()[0]["label"]
            assert (await client.post("/api/v1/users/recipients/card", json={"card_number": number[-4:]})).status_code == 422
            assert (await client.post("/api/v1/users/recipients/card", json={"card_number": "6214****0002"})).status_code == 422
            created = await client.post("/api/v1/users/contacts", json={
                "phone": "+86 139-0000-0002", "account_id": "A2", "nickname": "房东",
            })
            assert created.status_code == 201, created.text
            contact = created.json()
            contact_id = contact["contact_id"]
            assert contact["phone_masked"] == "139****0002"
            assert contact["card_masked"] is None
            assert "phone" not in contact and "account_number" not in contact
            saved = (await client.get("/api/v1/users/recipients")).json()
            assert len(saved) == 1 and saved[0]["contact_id"] == contact_id
            assert saved[0]["nickname"] == "房东"
            resolved = (await client.post(f"/api/v1/users/contacts/{contact_id}/resolve")).json()[0]
            assert resolved["account_id"] == "A2"
            assert resolved["card_number"] is None
            assert resolved["phone_number"] == "13900000002"
            upgraded = await client.post("/api/v1/users/contacts", json={
                "name": "李四", "card_number": number,
            })
            assert upgraded.status_code == 201
            assert upgraded.json()["contact_id"] == contact_id
            assert upgraded.json()["card_masked"].endswith(number[-4:])
            assert (await client.post("/api/v1/users/contacts", json={
                "phone": "13900000002", "account_id": "A2",
            })).status_code == 409
            assert (await client.post("/api/v1/users/contacts", json={
                "phone": "13900000002", "account_id": "A1",
            })).status_code == 422

            identity["user_id"] = "U2"
            assert (await client.get("/api/v1/users/contacts")).json() == []
            assert (await client.get("/api/v1/users/recipients")).json() == []
            assert (await client.patch(f"/api/v1/users/contacts/{contact_id}", json={"nickname": "其他"})).status_code == 404
            assert (await client.delete(f"/api/v1/users/contacts/{contact_id}")).status_code == 404
            assert (await client.post(f"/api/v1/users/contacts/{contact_id}/resolve")).status_code == 404

            identity["user_id"] = "U1"
            assert (await client.patch(f"/api/v1/users/contacts/{contact_id}", json={"nickname": "朋友"})).json()["nickname"] == "朋友"
            recipient = await db.get(UserORM, "U2")
            recipient.status = "inactive"
            await db.commit()
            assert (await client.post(f"/api/v1/users/contacts/{contact_id}/resolve")).status_code == 409
            assert (await client.delete(f"/api/v1/users/contacts/{contact_id}")).status_code == 204
            assert (await client.get("/api/v1/users/contacts")).json() == []
            assert (await client.get("/api/v1/users/recipients")).json() == []
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ai_name_or_alias_requires_saved_contact(db):
    assert (await transfer_directory(db, subject_user_id="U1", recipient_name="李四",
                                     contacts_only=True))["recipients"] == []
    assert len((await transfer_directory(db, subject_user_id="U1", recipient_phone="13900000002",
                                         contacts_only=True))["recipients"]) == 1
    db.add(ContactORM(contact_id="C1", user_id="U1", name="李四", nickname="老李",
                      phone="13900000002", account_number="2", bank="AI 模拟银行", transfer_count=0))
    await db.commit()
    for name in ("李四", "老李"):
        result = await transfer_directory(db, subject_user_id="U1", recipient_name=name,
                                          contacts_only=True)
        assert result["recipients"][0]["account_id"] == "A2"
    assert (await transfer_directory(db, subject_user_id="U1", recipient_name="老李"))["recipients"][0]["account_id"] == "A2"
    assert (await transfer_directory(db, subject_user_id="U2", recipient_name="老李"))["recipients"] == []
    assert (await transfer_directory(db, subject_user_id="U1", recipient_name="老李",
                                     recipient_phone="13900000002"))["recipients"][0]["account_id"] == "A2"
    recipient = await db.get(UserORM, "U2")
    recipient.name = "新名字"
    await db.commit()
    assert (await transfer_directory(db, subject_user_id="U1", recipient_name="老李"))["recipients"] == []


@pytest.mark.asyncio
async def test_saved_name_lookup_uses_owner_contacts(db):
    async def session_override():
        yield db

    app.dependency_overrides[get_db] = session_override
    identity = {"user_id": "U1"}
    app.dependency_overrides[get_current_user] = lambda: identity
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/api/v1/users/recipients/saved-name", json={"name": "老李"})).json() == []
            db.add(ContactORM(contact_id="C1", user_id="U1", name="李四", nickname="老李",
                              phone="13900000002", account_number="2", bank="AI 模拟银行", transfer_count=0))
            await db.commit()
            saved = await client.post("/api/v1/users/recipients/saved-name", json={"name": "老李"})
            assert saved.status_code == 200
            assert saved.json()[0]["account_id"] == "A2"
            assert saved.json()[0]["name"] == "李四"
            identity["user_id"] = "U2"
            assert (await client.post("/api/v1/users/recipients/saved-name", json={"name": "老李"})).json() == []
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_contact_can_be_added_by_full_simulation_card(db, monkeypatch):
    sessions = async_sessionmaker(db.bind, expire_on_commit=False)
    monkeypatch.setattr(contacts, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(data_service, "AsyncSessionLocal", sessions)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "U1"}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post("/api/v1/users/contacts", json={
                "card_number": transfer_card_number("A2"), "account_id": "A2", "nickname": "老李",
            })
            assert created.status_code == 201, created.text
            assert created.json()["phone_masked"] is None
            assert created.json()["card_masked"].endswith(transfer_card_number("A2")[-4:])
            assert (await client.get("/api/v1/users/recipients")).json()[0]["nickname"] == "老李"
            assert (await client.get("/api/v1/users/recipients")).json()[0]["phone"] is None
            resolved = (await client.post(f"/api/v1/users/contacts/{created.json()['contact_id']}/resolve")).json()[0]
            assert resolved["phone_number"] is None
            assert resolved["card_number"] == transfer_card_number("A2")
            own = await client.get("/api/v1/accounts/U1/accounts")
            assert own.status_code == 200
            assert own.json()[0]["transfer_card_number"] == transfer_card_number("A1")
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_manual_contact_checks_name_phone_and_card_before_saving(db, monkeypatch):
    sessions = async_sessionmaker(db.bind, expire_on_commit=False)
    monkeypatch.setattr(contacts, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(data_service, "AsyncSessionLocal", sessions)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "U1"}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            card = transfer_card_number("A2")
            wrong_name = await client.post("/api/v1/users/contacts", json={
                "name": "错误姓名", "phone": "13900000002", "card_number": card,
            })
            assert wrong_name.status_code == 409
            assert (await client.get("/api/v1/users/contacts")).json() == []
            phone_only = await client.post("/api/v1/users/contacts", json={
                "name": "李四", "phone": "13900000002",
            })
            assert phone_only.status_code == 201
            assert phone_only.json()["card_masked"] is None
            assert (await client.post(f"/api/v1/users/contacts/{phone_only.json()['contact_id']}/resolve")).json()[0]["card_number"] is None
            created = await client.post("/api/v1/users/contacts", json={
                "name": "李四", "phone": "13900000002", "card_number": card,
                "nickname": "同事",
            })
            assert created.status_code == 201, created.text
            assert created.json()["contact_id"] == phone_only.json()["contact_id"]
            assert created.json()["nickname"] == "同事"
            assert created.json()["phone_masked"] == "139****0002"
            assert (await client.get("/api/v1/users/recipients")).json()[0]["user_id"] == "U2"
            assert (await client.get("/api/v1/users/contacts")).json()[0]["contact_id"] == created.json()["contact_id"]
    finally:
        app.dependency_overrides.clear()
