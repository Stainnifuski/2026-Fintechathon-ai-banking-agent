"""卡片管理增强：锁卡、注销虚拟卡、挂失补卡、渠道开关、按方向定级、模拟用卡交易、未结束操作与审计。"""

from datetime import datetime, timedelta, timezone

import pyotp
import pytest
from sqlalchemy import func, select

from tests.integration.test_cards import card, client, db, enroll_totp, headers, preview
from backend.app.models import AccountORM, AuditLogORM, CardORM, CardOperationORM, TransactionORM, UserORM
from backend.app.services.bank import card_service


async def confirm(client, operation_id: str, digest: str):
    return await client.post(f"/api/v1/cards/operations/{operation_id}/confirm", headers=headers(),
                             json={"digest": digest})


async def complete(client, db, operation_id: str, **body) -> dict:
    """走完整授权：预览 → 确认 → （红色）TOTP。"""
    view = (await preview(client, operation_id, **body)).json()
    assert view.get("status") == "AWAITING_CONFIRMATION", view
    done = (await confirm(client, operation_id, view["digest"])).json()
    if done["status"] == "AWAITING_MFA":
        user = await db.get(UserORM, "U1")
        secret = user.totp_secret if user.totp_enabled else await enroll_totp(db)
        if user.totp_last_timestep is not None:
            user.totp_last_timestep -= 1
            await db.commit()
        done = (await client.post(f"/api/v1/cards/operations/{operation_id}/verify", headers=headers(),
                                  json={"proof": pyotp.TOTP(secret).now()})).json()
    assert done["status"] == "COMPLETED", done
    return done


async def purchase(client, card_id: str, operation_id: str, amount, channel: str = "pos",
                   merchant: str = "便利店", user_id: str = "U1"):
    return await client.post(f"/api/v1/cards/{card_id}/transactions", headers=headers(user_id),
                             json={"operation_id": operation_id, "amount": str(amount),
                                   "merchant": merchant, "channel": channel})


@pytest.mark.asyncio
async def test_user_lock_is_yellow_and_declines_purchases_until_unlocked(client, db) -> None:
    view = (await preview(client, "op-lock-1", action="card_lock", card_id="C1")).json()
    assert (view["permission_level"], view["requires_mfa"]) == ("yellow", False)
    assert view["changes"] == [{"field": "status", "label": "卡片状态", "before": "ACTIVE",
                                "after": "LOCKED", "unit": None}]
    await confirm(client, "op-lock-1", view["digest"])
    locked = await card(db, "C1")
    assert (locked.status, locked.lock_reason) == ("LOCKED", card_service.USER_LOCK_REASON)

    declined = (await purchase(client, "C1", "buy-locked-1", 10)).json()
    assert declined["authorization"]["approved"] is False
    assert declined["authorization"]["decline_code"] == "CARD_LOCKED"
    assert (await db.get(AccountORM, "A1", populate_existing=True)).balance_fen == 100_000
    assert (await preview(client, "op-lock-2", action="card_lock", card_id="C1")).json()["detail"]["code"] == "CARD_NOT_ACTIVE"

    unlock = (await preview(client, "op-unlock-own", action="card_unlock", card_id="C1")).json()
    assert unlock["permission_level"] == "red"


@pytest.mark.asyncio
async def test_permission_level_follows_risk_direction(client, db) -> None:
    body = {"action": "card_set_transaction_limits", "card_id": "C1"}
    lower = (await preview(client, "op-dir-1", **body, single_txn_limit=5000, daily_txn_limit=40000)).json()
    raise_one = (await preview(client, "op-dir-2", **body, single_txn_limit=5000, daily_txn_limit=60000)).json()
    assert (lower["permission_level"], raise_one["permission_level"]) == ("yellow", "red")

    channels = {"action": "card_set_channels", "card_id": "C1", "online_enabled": True,
                "atm_enabled": True, "contactless_enabled": True}
    open_overseas = (await preview(client, "op-dir-3", **channels, overseas_enabled=True)).json()
    assert open_overseas["permission_level"] == "red"
    assert open_overseas["changes"] == [{"field": "overseas_enabled", "label": "境外交易",
                                         "before": False, "after": True, "unit": None}]
    unchanged = await preview(client, "op-dir-4", **channels, overseas_enabled=False)
    assert unchanged.json()["detail"]["code"] == "CHANNELS_UNCHANGED"
    missing = await preview(client, "op-dir-5", action="card_set_channels", card_id="C1", online_enabled=False)
    assert missing.status_code == 422

    c2 = await card(db, "C2")
    c2.status, c2.lock_reason, c2.used_credit_fen = "ACTIVE", None, 1_500_000
    await db.commit()
    credit = {"action": "card_adjust_credit_limit", "card_id": "C2"}
    assert (await preview(client, "op-dir-6", **credit, credit_limit=30000)).json()["permission_level"] == "red"
    assert (await preview(client, "op-dir-7", **credit, credit_limit=16000)).json()["permission_level"] == "yellow"
    below = await preview(client, "op-dir-8", **credit, credit_limit=14999.99)
    assert below.json()["detail"]["code"] == "CREDIT_LIMIT_BELOW_USED"


@pytest.mark.asyncio
async def test_closing_channels_needs_only_confirmation_and_is_enforced(client, db) -> None:
    await complete(client, db, "op-chan-1", action="card_set_channels", card_id="C1",
                   online_enabled=False, atm_enabled=True, overseas_enabled=False, contactless_enabled=True)
    assert (await card(db, "C1")).online_enabled is False
    online = (await purchase(client, "C1", "buy-online-1", 10, channel="online")).json()["authorization"]
    overseas = (await purchase(client, "C1", "buy-overseas-1", 10, channel="overseas")).json()["authorization"]
    pos = (await purchase(client, "C1", "buy-pos-1", 10, channel="pos")).json()["authorization"]
    assert [online["decline_code"], overseas["decline_code"], pos["approved"]] == [
        "CHANNEL_DISABLED", "CHANNEL_DISABLED", True]


@pytest.mark.asyncio
async def test_debit_purchase_enforces_limits_and_funds(client, db) -> None:
    c1 = await card(db, "C1")
    c1.single_txn_limit_fen, c1.daily_txn_limit_fen = 30_000, 50_000
    await db.commit()

    ok = (await purchase(client, "C1", "buy-ok-1", "250.50", merchant="咖啡店")).json()
    assert ok["authorization"]["approved"] is True
    tx = await db.get(TransactionORM, ok["authorization"]["tx_id"])
    assert (tx.account_id, tx.type, tx.amount_fen, tx.counterparty) == ("A1", "purchase", 25_050, "咖啡店")
    assert (await db.get(AccountORM, "A1", populate_existing=True)).balance_fen == 100_000 - 25_050

    replay = (await purchase(client, "C1", "buy-ok-1", "250.50", merchant="咖啡店")).json()
    assert replay["authorization"]["auth_id"] == ok["authorization"]["auth_id"]
    conflict = await purchase(client, "C1", "buy-ok-1", "1", merchant="咖啡店")
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"

    single = (await purchase(client, "C1", "buy-single", "300.01")).json()["authorization"]
    assert single["decline_code"] == "SINGLE_LIMIT_EXCEEDED"
    second = (await purchase(client, "C1", "buy-ok-2", 200)).json()["authorization"]
    assert second["approved"] is True
    daily = (await purchase(client, "C1", "buy-daily", 50)).json()["authorization"]
    assert daily["decline_code"] == "DAILY_LIMIT_EXCEEDED"

    c1 = await card(db, "C1")
    c1.daily_txn_limit_fen = 5_000_000
    c1.single_txn_limit_fen = 5_000_000
    await db.commit()
    funds = (await purchase(client, "C1", "buy-funds", 600)).json()["authorization"]
    assert funds["decline_code"] == "INSUFFICIENT_FUNDS"

    history = (await client.get("/api/v1/cards/C1/transactions", headers=headers())).json()
    assert {row["operation_id"] for row in history} == {"buy-ok-1", "buy-single", "buy-ok-2", "buy-daily", "buy-funds"}
    assert (await client.get("/api/v1/cards/C3/transactions", headers=headers())).status_code == 404
    assert (await purchase(client, "C3", "buy-foreign", 1)).status_code == 404
    assert (await purchase(client, "C1", "buy-zero", 0)).status_code == 422
    events = (await db.execute(select(AuditLogORM.result).where(AuditLogORM.action == "card_purchase"))).scalars().all()
    assert sorted(events) == ["blocked", "blocked", "blocked", "success", "success"]


@pytest.mark.asyncio
async def test_credit_purchase_uses_available_credit(client, db) -> None:
    c2 = await card(db, "C2")
    c2.status, c2.lock_reason, c2.used_credit_fen, c2.credit_limit_fen = "ACTIVE", None, 0, 100_000
    await db.commit()
    ok = (await purchase(client, "C2", "buy-credit-1", 800)).json()
    assert ok["authorization"]["approved"] is True
    assert (ok["card"]["used_credit_fen"], ok["card"]["available_credit_fen"]) == (80_000, 20_000)
    assert (await db.get(AccountORM, "A2", populate_existing=True)).balance_fen == 0
    over = (await purchase(client, "C2", "buy-credit-2", "200.01")).json()["authorization"]
    assert over["decline_code"] == "CREDIT_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_expired_card_declines(client, db) -> None:
    c1 = await card(db, "C1")
    c1.valid_thru = "2020-01"
    await db.commit()
    result = (await purchase(client, "C1", "buy-expired", 1)).json()["authorization"]
    assert result["decline_code"] == "CARD_EXPIRED"


@pytest.mark.asyncio
async def test_virtual_card_cancel_frees_quota_and_is_terminal(client, db) -> None:
    issued = (await complete(client, db, "op-vcard-1", action="card_apply_virtual", account_id="A1"))["result"]
    assert issued["valid_thru"] and issued["card_number"].startswith("6259****")
    physical = await preview(client, "op-vc-phys", action="card_cancel_virtual", card_id="C1")
    assert physical.json()["detail"]["code"] == "NOT_VIRTUAL_CARD"

    view = (await preview(client, "op-vc-cancel", action="card_cancel_virtual", card_id=issued["card_id"])).json()
    assert view["permission_level"] == "yellow"
    await confirm(client, "op-vc-cancel", view["digest"])
    cancelled = await card(db, issued["card_id"])
    assert cancelled.status == "CANCELLED" and cancelled.cancelled_at is not None
    for index, body in enumerate([{"action": "card_unlock"}, {"action": "card_lock"}, {"action": "card_report_lost"}]):
        response = await preview(client, f"op-vc-after-{index}", card_id=issued["card_id"], **body)
        assert response.json()["detail"]["code"] == "CARD_CANCELLED"
    declined = (await purchase(client, issued["card_id"], "buy-cancelled", 1)).json()["authorization"]
    assert declined["decline_code"] == "CARD_CANCELLED"

    for index in range(card_service.MAX_VIRTUAL_CARDS_PER_ACCOUNT):
        await complete(client, db, f"op-vc-more-{index}", action="card_apply_virtual", account_id="A1")


@pytest.mark.asyncio
async def test_reissue_after_loss_inherits_settings_once(client, db) -> None:
    active = await preview(client, "op-re-active", action="card_reissue", card_id="C1")
    assert active.json()["detail"]["code"] == "CARD_NOT_LOST"
    c1 = await card(db, "C1")
    c1.single_txn_limit_fen, c1.overseas_enabled = 123_400, True
    await db.commit()
    await complete(client, db, "op-re-lost", action="card_report_lost", card_id="C1")

    view = (await preview(client, "op-reissue-1", action="card_reissue", card_id="C1")).json()
    assert view["permission_level"] == "yellow"
    done = (await confirm(client, "op-reissue-1", view["digest"])).json()
    new = done["result"]
    assert new["card_id"] != "C1" and new["status"] == "ACTIVE" and new["card_number"].startswith("6222****")
    assert (new["single_txn_limit_fen"], new["overseas_enabled"], new["account_id"]) == (123_400, True, "A1")
    old = await card(db, "C1")
    assert (old.status, old.replaced_by_card_id) == ("LOST", new["card_id"])
    replay = (await confirm(client, "op-reissue-1", view["digest"])).json()
    assert replay["result"]["card_id"] == new["card_id"]
    again = await preview(client, "op-reissue-2", action="card_reissue", card_id="C1")
    assert again.json()["detail"]["code"] == "CARD_ALREADY_REISSUED"
    assert await db.scalar(select(func.count()).select_from(CardORM).where(CardORM.user_id == "U1")) == 3


@pytest.mark.asyncio
async def test_open_operations_per_card_are_capped_and_expired_ones_swept(client, db) -> None:
    for index in range(3):
        assert (await preview(client, f"op-cap-{index}", action="card_lock", card_id="C1")).status_code == 201
    blocked = await preview(client, "op-cap-3", action="card_lock", card_id="C1")
    assert blocked.json()["detail"]["code"] == "CARD_OPERATION_PENDING"
    assert (await preview(client, "op-cap-other", action="card_unlock", card_id="C2")).status_code == 201

    row = await db.get(CardOperationORM, "op-cap-0")
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.commit()
    assert (await preview(client, "op-cap-4", action="card_lock", card_id="C1")).status_code == 201
    swept = await db.get(CardOperationORM, "op-cap-0", populate_existing=True)
    assert swept.status == "EXPIRED"
    assert await db.scalar(select(func.count()).select_from(AuditLogORM)
                           .where(AuditLogORM.action == "card_lock_expire")) == 1


@pytest.mark.asyncio
async def test_failures_and_cancellations_are_audited(client, db) -> None:
    secret = await enroll_totp(db)
    view = (await preview(client, "op-aud-1", action="card_report_lost", card_id="C1")).json()
    await confirm(client, "op-aud-1", view["digest"])
    wrong = "000000" if pyotp.TOTP(secret).now() != "000000" else "111111"
    for _ in range(2):
        await client.post("/api/v1/cards/operations/op-aud-1/verify", headers=headers(), json={"proof": wrong})
    await client.post("/api/v1/cards/operations/op-aud-1/cancel", headers=headers())

    view = (await preview(client, "op-aud-3", action="card_set_transaction_limits", card_id="C1",
                          single_txn_limit=2000, daily_txn_limit=8000)).json()
    c1 = await card(db, "C1")
    c1.state_version += 1
    await db.commit()
    await confirm(client, "op-aud-3", view["digest"])

    rows = (await db.execute(select(AuditLogORM.action, AuditLogORM.result, AuditLogORM.permission_level)
                             .where(AuditLogORM.intent == "card_management"))).all()
    assert ("card_report_lost_verify_failed_1", "failed", "red") in rows
    assert ("card_report_lost_verify_failed_2", "failed", "red") in rows
    assert ("card_report_lost_cancel", "cancelled", "red") in rows
    assert ("card_set_transaction_limits_bank_rejected", "failed", "yellow") in rows


@pytest.mark.asyncio
async def test_new_card_numbers_avoid_existing_numbers(db, monkeypatch) -> None:
    db.add(CardORM(card_id="CV1", user_id="U1", account_id="A1", card_type="DEBIT", is_virtual=True,
                   card_number="6259****0007", status="ACTIVE", single_txn_limit_fen=1, daily_txn_limit_fen=1,
                   single_txn_cap_fen=1, daily_txn_cap_fen=1))
    await db.commit()
    draws = iter([7, 7, 8])
    monkeypatch.setattr(card_service.secrets, "randbelow", lambda _: next(draws))
    assert await card_service._new_card_number(db, "6259") == "6259****0008"
