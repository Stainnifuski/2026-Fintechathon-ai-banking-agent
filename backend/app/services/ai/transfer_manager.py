"""NOVA transfer skill: durable multi-task drafts backed by the existing transfer state machine."""
import hashlib
import json
import re
import time
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.account import AccountORM
from ...models.contact import ContactORM
from ...models.nova_task import NovaMessageReceiptORM, NovaSessionORM, NovaTaskMessageORM
from ...models.user import UserORM
from ...models.transfer_recipient_history import TransferRecipientHistoryCutoffORM
from ...integrations.bank_api import BankApi, BankApiError
from ..bank.contacts import _transfer_methods
from ..bank.transfer_cards import transfer_card_number
from ..bank.account_scope import isolated_account_ids
from ..bank.transfer_service import BankError, transfer_directory
from ..bank.nova_conversations import redact as redact_sensitive
from ..orchestration import TaskError, create_transfer_task, modify_task, task_snapshot
from ..orchestration import cancel_task
from .nlu import (
    Extraction, NLUEngine, UnderstandingError, amount_fen, NOTE_SEPARATOR, PHONE,
    scope_guard, offline_extract,
)
from .skill_registry import load_skill

SKILL_VERSION = "transfer-v1"
CARD = r"(?<!\d)\d{16}(?!\d)"
TRANSFER = re.compile(r"(?:给|向).+?(?:转|汇|打)|转给|汇给|打给|(?:转账|汇款|打款)(?:给|到)|(?:我要|帮我|请|想|需要|发起).{0,8}(?:转钱|转账|汇款|打款)|^转\s*\d+(?:\.\d+)?\s*(?:元|块)|^(?:转钱|转账|汇款|打款)$")
RECORD_QUERY = re.compile(r"(?:转账|汇款).{0,5}(?:记录|历史|流水|明细)|(?:记录|历史|流水|明细).{0,5}(?:转账|汇款)")
READ_QUERY = re.compile(r"转过|转了多少|转账.{0,5}(?:多少|总额|笔数)|(?:查|看|统计|查询).{0,12}(?:转账|汇款)")
STATUS_QUERY = re.compile(r"转账.{0,4}(?:状态|结果|进度|成功了吗)|这笔.{0,4}(?:成功了吗|状态)|^状态$|^进度$")
MODIFY = re.compile(r"改成|改为|修改|换成|不对|把.+?改")


class ManagerError(Exception):
    def __init__(self, message: str, code: str = "TRANSFER_TASK_ERROR", status_code: int = 409):
        self.message, self.code, self.status_code = message, code, status_code
        super().__init__(message)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _masked_message(message: str) -> str:
    return re.sub(PHONE, "<手机号>", re.sub(CARD, "<银行卡号>", message))


def _public_task(task: dict, snapshot: dict | None = None) -> dict:
    slots = task.get("slots", {})
    return {"chat_task_id": task["chat_task_id"], "status": task["status"],
            "origin_request_id": task.get("origin_request_id"),
            "recipient": slots.get("recipient"), "amount_fen": slots.get("amount_fen"),
            "form": {"recipient": slots.get("recipient") or "",
                     "amount_yuan": str(Decimal(slots["amount_fen"]) / 100) if slots.get("amount_fen") else "",
                     "amount_rule": "付款账户可用余额的一半" if slots.get("amount_ratio") == "half" or slots.get("amount_basis_available_fen") is not None else None,
                     "amount_basis_available_fen": slots.get("amount_basis_available_fen"),
                     "phone_masked": "••••" + slots["phone"][-4:] if slots.get("phone") else None,
                     "card_masked": "••••" + slots["card"][-4:] if slots.get("card") else None,
                     "payer_account_id": slots.get("from_account") or slots.get("payer") or "",
                     "note": slots.get("note") or ""},
            "missing_fields": task.get("missing_fields", []),
            "candidates": task.get("candidates", []),
            "transfer_task_id": task.get("transfer_task_id"), "task": snapshot,
            "skill_version": SKILL_VERSION}


async def _view(db: AsyncSession, row: NovaSessionORM) -> dict:
    state = row.state or {}
    tasks = []
    for task in state.get("tasks", []):
        snapshot = None
        if task.get("transfer_task_id"):
            snapshot = await task_snapshot(db, task["transfer_task_id"], row.user_id)
            if snapshot["phase"] == "FINISHED":
                task = {**task, "status": snapshot["outcome"]}
            else:
                task = {**task, "status": snapshot["phase"]}
        tasks.append(_public_task(task, snapshot))
    return {"session_id": row.session_id, "session_version": row.version,
            "focus_task_id": state.get("focus_task_id"), "tasks": tasks}


async def get_session(db: AsyncSession, user_id: str, session_id: str) -> dict:
    row = await db.get(NovaSessionORM, session_id)
    if row is None or row.user_id != user_id:
        raise ManagerError("聊天任务会话不存在", "SESSION_NOT_FOUND", 404)
    return await _view(db, row)


async def change_task_form(db: AsyncSession, bank: BankApi, *, user_id: str, session_id: str,
                           chat_task_id: str, session_version: int, request_id: str,
                           recipient: str, amount_yuan: str, phone: str | None,
                           card: str | None, clear_phone: bool, clear_card: bool,
                           payer_account_id: str, note: str) -> dict:
    receipt_id = _digest([user_id, request_id])
    payload = ["form", session_id, chat_task_id, session_version, recipient, amount_yuan,
               phone, card, clear_phone, clear_card, payer_account_id, note]
    digest = _digest(payload)
    receipt = await db.get(NovaMessageReceiptORM, receipt_id)
    if receipt:
        if receipt.payload_digest != digest:
            raise ManagerError("相同请求编号不能用于不同修改", "MESSAGE_ID_CONFLICT")
        return await get_session(db, user_id, session_id)
    row = await db.get(NovaSessionORM, session_id)
    if row is None or row.user_id != user_id:
        raise ManagerError("聊天任务会话不存在", "SESSION_NOT_FOUND", 404)
    if row.version != session_version:
        raise ManagerError("会话已更新，请刷新后重试", "STALE_SESSION_VERSION")
    state = json.loads(json.dumps(row.state))
    target = next((item for item in state["tasks"] if item["chat_task_id"] == chat_task_id), None)
    if target is None or target["status"] in {"CANCELLED", "SUCCEEDED", "FAILED"}:
        raise ManagerError("任务已结束或不存在", "TASK_NOT_EDITABLE", 409)
    if target.get("transfer_task_id"):
        current = await task_snapshot(db, target["transfer_task_id"], user_id)
        if "modify" not in current["allowed_actions"]:
            raise ManagerError("当前转账计划不能修改", "TASK_NOT_EDITABLE", 409)
    slots = target["slots"]
    recipient = recipient.strip()
    amount_yuan = amount_yuan.strip()
    note = note.strip()
    if len(recipient) > 100 or len(note) > 256:
        raise ManagerError("姓名或备注过长", "FORM_INVALID", 422)
    if phone and not re.fullmatch(PHONE, phone.strip()):
        raise ManagerError("请填写完整的手机号", "INVALID_PHONE", 422)
    if card and not re.fullmatch(CARD, card.strip()):
        raise ManagerError("请填写完整的银行卡号", "INVALID_CARD", 422)
    if phone and card:
        raise ManagerError("请只填写一种收款号码", "RECIPIENT_METHOD_CONFLICT", 422)
    try:
        amount = amount_fen(amount_yuan) if amount_yuan else None
    except (ValueError, UnderstandingError) as error:
        raise ManagerError(str(error), "INVALID_AMOUNT", 422) from error
    if amount is not None and amount <= 0:
        raise ManagerError("转账金额须大于零", "INVALID_AMOUNT", 422)
    old = dict(slots)
    slots["recipient"] = recipient or None
    slots["amount_fen"] = amount
    old_amount_yuan = str(Decimal(old["amount_fen"]) / 100) if old.get("amount_fen") else ""
    if amount_yuan and amount_yuan != old_amount_yuan:
        slots.pop("amount_ratio", None)
        slots.pop("amount_basis_available_fen", None)
        slots.pop("amount_basis_at", None)
    slots["note"] = note or None
    if note:
        slots.pop("note_cleared", None)
    elif old.get("note"):
        slots["note_cleared"] = True
    if recipient != (old.get("recipient") or ""):
        for key in ("phone", "card", "to_account", "phone_source"):
            slots.pop(key, None)
        slots.pop("no_auto_recipient", None)
    if clear_phone:
        slots.pop("phone", None)
        slots.pop("phone_source", None)
        slots.pop("to_account", None)
        slots["no_auto_recipient"] = True
    if clear_card:
        slots.pop("card", None)
        slots.pop("to_account", None)
        slots["no_auto_recipient"] = True
    if phone:
        slots["phone"] = phone.strip()
        slots.pop("card", None)
        slots.pop("to_account", None)
        slots.pop("no_auto_recipient", None)
    if card:
        slots["card"] = card.strip()
        slots.pop("phone", None)
        slots.pop("phone_source", None)
        slots.pop("to_account", None)
        slots.pop("no_auto_recipient", None)
    if payer_account_id:
        slots["from_account"] = payer_account_id
        slots["payer"] = payer_account_id
    elif old.get("from_account"):
        slots.pop("from_account", None)
        slots.pop("payer", None)
    if slots == old:
        return await _view(db, row)
    changed = await db.execute(update(NovaSessionORM).where(
        NovaSessionORM.session_id == session_id, NovaSessionORM.version == session_version
    ).values(version=session_version + 1))
    if changed.rowcount != 1:
        raise ManagerError("会话已更新，请刷新后重试", "STALE_SESSION_VERSION")
    await _resolve(db, bank, user_id, target)
    if target["missing_fields"] and target.get("transfer_task_id"):
        current = await task_snapshot(db, target["transfer_task_id"], user_id)
        await cancel_task(db, bank, task_id=current["task_id"], user_id=user_id,
                          request_id=f"NOVA-CANCEL-{request_id}", state_version=current["state_version"])
        target.pop("transfer_task_id", None)
    if not target["missing_fields"] and slots.get("to_account") and slots.get("from_account"):
        params = dict(user_id=user_id, request_id=f"NOVA-FORM-{request_id}",
                      operation_id=f"NOVA-FORM-{request_id}",
                      from_account=slots["from_account"], to_account=slots["to_account"],
                      amount=Decimal(slots["amount_fen"]) / 100, note=slots.get("note"),
                      note_source=("manual" if slots.get("note") else
                                   "cleared" if slots.get("note_cleared") else "automatic"),
                      recipient_phone=slots.get("phone"), commit=False)
        if target.get("transfer_task_id"):
            current = await task_snapshot(db, target["transfer_task_id"], user_id)
            planned = await modify_task(db, bank, task_id=current["task_id"],
                                        state_version=current["state_version"], **params)
        else:
            planned = await create_transfer_task(db, bank, recipient_card_number=slots.get("card"), **params)
        target["transfer_task_id"] = planned["task_id"]
        target["status"] = planned["phase"]
        slots.pop("amount_ratio", None)
    state["focus_task_id"] = chat_task_id
    row.state, row.version = state, session_version + 1
    db.add(NovaMessageReceiptORM(receipt_id=receipt_id, user_id=user_id,
                                 payload_digest=digest, response={"session_id": session_id}))
    await db.commit()
    return await _view(db, row)


async def cancel_chat_task(db: AsyncSession, bank: BankApi, *, user_id: str, session_id: str,
                           chat_task_id: str, session_version: int, request_id: str) -> dict:
    receipt_id = _digest([user_id, request_id])
    digest = _digest(["cancel", session_id, chat_task_id, session_version])
    receipt = await db.get(NovaMessageReceiptORM, receipt_id)
    if receipt:
        if receipt.payload_digest != digest:
            raise ManagerError("相同请求编号不能用于不同操作", "MESSAGE_ID_CONFLICT")
        return await get_session(db, user_id, session_id)
    row = await db.get(NovaSessionORM, session_id)
    if row is None or row.user_id != user_id:
        raise ManagerError("聊天任务会话不存在", "SESSION_NOT_FOUND", 404)
    if row.version != session_version:
        raise ManagerError("会话已更新，请刷新后重试", "STALE_SESSION_VERSION")
    state = json.loads(json.dumps(row.state))
    target = next((item for item in state["tasks"] if item["chat_task_id"] == chat_task_id), None)
    if target is None:
        raise ManagerError("任务不存在", "TASK_NOT_FOUND", 404)
    if target.get("transfer_task_id"):
        current = await task_snapshot(db, target["transfer_task_id"], user_id)
        if "cancel" not in current["allowed_actions"]:
            raise ManagerError("当前任务无法取消", "TASK_NOT_CANCELLABLE", 409)
        result = await cancel_task(db, bank, task_id=current["task_id"], user_id=user_id,
                                   request_id=f"NOVA-CANCEL-{request_id}", state_version=current["state_version"])
        if result["outcome"] != "CANCELLED":
            raise ManagerError("取消请求已提交，请稍后查看任务状态", "CANCEL_PENDING", 409)
    changed = await db.execute(update(NovaSessionORM).where(
        NovaSessionORM.session_id == session_id, NovaSessionORM.version == session_version
    ).values(version=session_version + 1))
    if changed.rowcount != 1:
        raise ManagerError("会话已更新，请刷新后重试", "STALE_SESSION_VERSION")
    target["status"] = "CANCELLED"
    target["missing_fields"] = []
    target["candidates"] = []
    state["focus_task_id"] = next((item["chat_task_id"] for item in state["tasks"]
                                   if item["status"] not in {"CANCELLED", "SUCCEEDED", "FAILED"}), None)
    row.state, row.version = state, session_version + 1
    db.add(NovaMessageReceiptORM(receipt_id=receipt_id, user_id=user_id,
                                 payload_digest=digest, response={"session_id": session_id}))
    await db.commit()
    return await _view(db, row)


def wants_transfer(message: str, pending: bool = False) -> bool:
    text = message.strip()
    if RECORD_QUERY.search(text) or READ_QUERY.search(text):
        return False
    if TRANSFER.search(text) or (pending and (MODIFY.search(text) or re.search(
        PHONE + "|" + CARD + r"|一半|\d+(?:\.\d+)?\s*(?:元|块)?$|^(?:收款人|姓名|名字|金额|付款账户|备注|用途)(?:是|为|：|:|\s)", text))):
        return True
    if pending and re.fullmatch(r"[\u4e00-\u9fff]{2,4}", text) and text not in {"余额", "账单", "日历", "状态", "进度"}:
        return True
    return pending and text in {"确认", "执行", "取消", "算了", "状态", "进度"}


def _split(message: str) -> list[str]:
    text = message.strip()
    each = re.fullmatch(
        r"(?:请|帮我|麻烦)?\s*给\s*(.+?)\s*各(?:转账|转|汇款|汇|打款|打)\s*(.+)",
        text,
    )
    if each:
        recipients = [
            item.strip() for item in re.split(r"\s*(?:[、，,]|和|及|与)\s*", each[1])
            if item.strip()
        ]
        if 2 <= len(recipients) <= 8 and all(
            re.fullmatch(r"[\u4e00-\u9fffA-Za-z]{2,20}", item) for item in recipients
        ):
            return [f"给{recipient}转{each[2]}" for recipient in recipients]
    # 逗号后的“给”也可能只是同一笔转账的后置收款人，例如
    # “按某金额，从第二张卡里，给赵六转钱”。只有后续分句自身带明确金额时才按多笔拆分。
    explicit_amount = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万]+(?:点[零〇一二三四五六七八九]+)?)\s*(?:元|块钱|块)"
    separator = r"[；;]|[，,](?=\s*(?:再?给|转给|向)[^，,；;]*" + explicit_amount + r")"
    chunks = [part.strip() for part in re.split(separator, text) if part.strip()]
    return chunks


async def _known(db: AsyncSession, user_id: str, name: str) -> list[dict]:
    contacts = (await db.scalars(select(ContactORM).where(
        ContactORM.user_id == user_id,
        (ContactORM.name == name) | (ContactORM.nickname == name)))).all()
    found = [{"name": row.name, "phone": row.phone if row.phone_known else None,
              "card": row.account_number if row.card_known else None, "account_hint": None,
              "source": "saved_contact"} for row in contacts]
    if found:
        return found
    cutoff = await db.get(TransferRecipientHistoryCutoffORM, user_id)
    history = await _transfer_methods(db, user_id, cutoff.cleared_at if cutoff else None)
    for row in history.values():
        if row["name"] == name:
            found.append({"name": row["name"], "phone": row["phone_number"],
                          "card": row["card_number"],
                          "account_hint": row["card_account_id"] or row["phone_account_id"],
                          "source": "transfer_history"})
    return found


async def _card_match(db: AsyncSession, user_id: str, card: str, name: str | None) -> list[dict]:
    accounts = (await db.scalars(select(AccountORM).where(
        AccountORM.status == "active", AccountORM.account_id.not_in(isolated_account_ids())))).all()
    result = []
    for account in accounts:
        if account.user_id == user_id or transfer_card_number(account.account_id) != card:
            continue
        owner = await db.get(UserORM, account.user_id)
        if owner and owner.status == "active" and (name is None or owner.name == name):
            result.append({"account_id": account.account_id, "recipient_name": owner.name,
                           "label": f"{owner.name} · 账户尾号{card[-4:]}"})
    return result


async def _resolve(db: AsyncSession, bank: BankApi, user_id: str, task: dict) -> None:
    slots = task["slots"]
    name, phone, card = slots.get("recipient"), slots.get("phone"), slots.get("card")
    missing = []
    task["candidates"] = []
    task["candidate_map"] = []
    if slots.get("amount_ratio") == "half":
        funding = await bank.transfer_funding_accounts(subject_user_id=user_id)
        accounts = funding["accounts"]
        payer = slots.get("from_account") or slots.get("payer")
        selected = next((row for row in accounts if row["account_id"] == payer), None)
        if selected is None and payer:
            selected = next((row for row in accounts if payer in row["label"]), None)
        if selected is None and not payer and len(accounts) == 1:
            selected = accounts[0]
        if selected is None:
            task["missing_fields"] = ["付款账户"] + (["收款人姓名"] if not name else [])
            if len(accounts) > 1:
                choices = [{"candidate_id": uuid4().hex, "label": row["label"],
                            "account_id": row["account_id"], "kind": "payer"} for row in accounts]
                task["candidate_map"] = choices
                task["candidate_expires_at"] = time.time() + 600
                task["candidates"] = [{key: choice[key] for key in ("candidate_id", "label", "kind")}
                                      for choice in choices]
                task["status"] = "NEEDS_SELECTION"
            else:
                task["status"] = "COLLECTING"
            slots.pop("amount_fen", None)
            return
        slots["from_account"] = selected["account_id"]
        slots["amount_basis_available_fen"] = selected["available_fen"]
        slots["amount_basis_at"] = funding["as_of"]
        slots["amount_fen"] = selected["available_fen"] // 2
        if slots["amount_fen"] <= 0:
            task["missing_fields"], task["status"] = ["付款账户可用余额不足"], "COLLECTING"
            return
    if not name:
        missing.append("收款人姓名")
    if not slots.get("amount_fen"):
        missing.append("转账金额")
    if not name:
        task["missing_fields"], task["status"] = missing, "COLLECTING"
        return
    history_recipient = None
    if slots.get("history_reference_tx_id") and slots.get("to_account"):
        account = await db.get(AccountORM, slots["to_account"])
        owner = await db.get(UserORM, account.user_id) if account is not None else None
        if (account is None or owner is None or account.user_id == user_id
                or account.status != "active" or owner.status != "active"):
            task["missing_fields"], task["status"] = (
                ["历史收款账户当前不可用"], "COLLECTING"
            )
            return
        slots["recipient"] = owner.name
        name = owner.name
        history_recipient = {
            "account_id": account.account_id,
            "recipient_name": owner.name,
            "label": f"{owner.name} · 账户尾号{transfer_card_number(account.account_id)[-4:]}",
        }
    known = await _known(db, user_id, name)
    verified_name = known[0]["name"] if len({item["name"] for item in known}) == 1 else name
    if not known and not phone and not card and history_recipient is None:
        missing.append("完整手机号或完整银行卡号")
    if known and not phone and not card and slots.get("no_auto_recipient") and history_recipient is None:
        missing.append("完整手机号或完整银行卡号")
    elif known and not phone and not card and history_recipient is None:
        if len({(item["phone"], item["card"], item["account_hint"]) for item in known}) > 1:
            missing.append("同名收款人的完整手机号或银行卡号")
        else:
            phone = known[0]["phone"]
            if phone:
                slots["phone"] = phone
                slots["phone_source"] = known[0]["source"]
            elif known[0]["card"]:
                card = known[0]["card"]
                slots["card"] = card
            else:
                missing.append("完整手机号或完整银行卡号")
    if missing:
        task["missing_fields"], task["status"] = missing, "COLLECTING"
        return
    try:
        directory = await transfer_directory(db, subject_user_id=user_id,
                                              recipient_phone=phone, recipient_name=name if phone else None) if phone else None
        recipients = (
            [history_recipient] if history_recipient is not None else
            directory["recipients"] if directory else
            await _card_match(db, user_id, card, verified_name)
        )
        if phone and card:
            card_ids = {row["account_id"] for row in await _card_match(db, user_id, card, verified_name)}
            recipients = [row for row in recipients if row["account_id"] in card_ids]
        if not recipients:
            missing.append("有效收款人（请核对姓名与完整号码）")
        elif slots.get("to_account") in {row["account_id"] for row in recipients}:
            pass
        elif len(recipients) > 1:
            choices = [{"candidate_id": uuid4().hex, "label": row["label"],
                        "account_id": row["account_id"], "kind": "recipient"} for row in recipients]
            task["candidate_map"] = choices
            task["candidate_expires_at"] = time.time() + 600
            task["candidates"] = [{key: choice[key] for key in ("candidate_id", "label", "kind")}
                                  for choice in choices]
            missing.append("选择收款账户")
        else:
            slots["to_account"] = recipients[0]["account_id"]
        payers = directory["payers"] if directory else (await transfer_directory(
            db, subject_user_id=user_id, recipient_name=name))["payers"]
        payer = slots.get("payer")
        choices = [row for row in payers if not payer or payer == row["account_id"] or payer in row["label"]]
        if slots.get("from_account") in {row["account_id"] for row in payers}:
            pass
        elif not payer and payers:
            slots["from_account"] = payers[0]["account_id"]
        elif len(choices) == 1:
            slots["from_account"] = choices[0]["account_id"]
        else:
            missing.append("付款账户")
            task["candidate_map"] = task.get("candidate_map", []) + [
                {"candidate_id": uuid4().hex, "label": row["label"],
                 "account_id": row["account_id"], "kind": "payer"} for row in payers]
            task["candidates"] = [{key: choice[key] for key in ("candidate_id", "label", "kind")}
                                  for choice in task["candidate_map"]]
            task["candidate_expires_at"] = time.time() + 600
    except (BankError, BankApiError) as error:
        missing.append(error.message)
    task["missing_fields"] = missing
    task["status"] = "NEEDS_SELECTION" if task["candidates"] else "COLLECTING" if missing else "READY_FOR_REVIEW"


async def _extract(
    nlu: NLUEngine,
    text: str,
    pending: bool,
    model_result: Extraction | None = None,
) -> dict:
    card = re.search(CARD, text) if model_result is None else None
    clean = text.replace(card[0], "") if card else text
    phone = re.search(PHONE, clean) if model_result is None else None
    if card and model_result is None:
        clean = re.sub(r"银行卡号|卡号|银行卡", "", clean)
    parameter_text = re.split(NOTE_SEPARATOR, clean, maxsplit=1)[0]
    relative_half = (
        getattr(model_result, "amount_ratio", None) == "half"
        if model_result is not None else
        bool(re.search(r"一半(?:的)?(?:钱|余额)?", parameter_text))
    )
    if relative_half:
        if re.search(r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万]+)\s*(?:元|块钱|块)", parameter_text):
            raise UnderstandingError("同时出现具体金额和余额一半，请只保留一种金额表达")
        # Relative amount is resolved from the selected bank account, never by the LLM.
        clean = re.sub(r"一半(?:的)?(?:钱|余额)?", "", clean)
        clean = re.sub(r"(?:把)?(?:我|我的)?(?:银行卡|账户|卡)(?:里|中|的)?", "", clean)
        clean = clean.strip(" ，,。！! ")
        result = model_result or offline_extract(clean, {"missing_recipient": pending})
    else:
        result = model_result
    # Existing NLU handles phone numbers, but explicit extraction also covers a reply with only a number.
    extraction_context = {"missing_recipient": pending, "skill_instructions": load_skill("transfer")}
    # Full recipient identifiers stay inside the bank application, even in model mode.
    if result is None:
        result = (offline_extract(clean, extraction_context) if redact_sensitive(text) != text else
                  await nlu.extract(clean, extraction_context))
    evidence_text = text if model_result is not None else clean
    for key in ("recipient", "recipient_phone", "amount_text", "payer", "note"):
        value = getattr(result, key)
        if value is not None and value not in evidence_text:
            raise UnderstandingError("提取的信息与本轮消息不一致，请重新描述")
    if model_result is not None:
        for value in (result.recipient_phone, getattr(result, "recipient_card", None)):
            if value is not None and value not in text:
                raise UnderstandingError("提取的收款号码与本轮消息不一致，请重新描述")
        if relative_half and not re.search(r"一半(?:的)?(?:钱|余额)?", parameter_text):
            raise UnderstandingError("提取的相对金额与本轮消息不一致，请重新描述")
    name_match = re.search(r"(?:给|向|转给)\s*([\u4e00-\u9fffA-Za-z]{2,20}?)(?=\s*(?:转|汇|打|手机号|手机|银行卡|卡号|\d))", clean)
    field_name = re.search(r"^(?:收款人|姓名|名字)(?:是|为|：|:|\s)+\s*([\u4e00-\u9fffA-Za-z]{2,20})$", clean.strip())
    name = (result.recipient if model_result is not None else
            name_match[1] if name_match else field_name[1] if field_name else result.recipient)
    if name and re.fullmatch(PHONE, name):
        name = None
    if name and ("手机号" in name or "银行卡" in name or len(name) > 30):
        name = None
    intent = "transfer" if result.intent == "unknown" and TRANSFER.search(text) else result.intent
    return {"intent": intent, "recipient": name, "phone": phone[0] if phone else result.recipient_phone,
            "card": card[0] if card else getattr(result, "recipient_card", None),
            "amount_fen": amount_fen(result.amount_text) if result.amount_text else None,
            "amount_ratio": "half" if relative_half else None,
            "payer": result.payer, "note": result.note,
            "clear_note": (getattr(result, "clear_note", False) if model_result is not None else
                           bool(re.search(r"清空备注|不要备注|删除备注|无备注", text)))}


async def process_message(db: AsyncSession, bank: BankApi, nlu: NLUEngine, *, user_id: str,
                          message: str, request_id: str, session_id: str | None,
                          session_version: int, focus_task_id: str | None = None,
                          candidate_id: str | None = None, create_session: bool = False,
                          trusted_fields: dict | None = None,
                          conversation_history: list[dict] | None = None,
                          page_context: dict | None = None,
                          current_tool_results: list[dict] | None = None,
                          goal_plan: dict | None = None) -> dict:
    receipt_id = _digest([user_id, request_id])
    payload_digest = _digest([
        message, session_id, session_version, focus_task_id, candidate_id, trusted_fields,
        conversation_history, page_context, current_tool_results, goal_plan,
    ])
    receipt = await db.get(NovaMessageReceiptORM, receipt_id)
    if receipt:
        if receipt.payload_digest != payload_digest:
            raise ManagerError("相同请求编号不能用于不同消息", "MESSAGE_ID_CONFLICT")
        row = await db.get(NovaSessionORM, receipt.response["session_id"])
        return {**receipt.response, **await _view(db, row)}
    row = await db.get(NovaSessionORM, session_id) if session_id else None
    if session_id and ((row is None and not create_session) or (row is not None and row.user_id != user_id)):
        raise ManagerError("聊天任务会话不存在", "SESSION_NOT_FOUND", 404)
    if row and row.version != session_version:
        raise ManagerError("会话已更新，请刷新后重试", "STALE_SESSION_VERSION")
    state = json.loads(json.dumps(row.state)) if row else {"tasks": [], "focus_task_id": None}
    tasks = state["tasks"]
    pending = [task for task in tasks if task["status"] in {"COLLECTING", "NEEDS_SELECTION"}]
    if scope_guard(message):
        return {"reply": "目前聊天仅支持即时人民币转账；定时、周期、AA及外币业务请使用对应工作台。", "mode": nlu.mode,
                "tool_calls": [], **(await _view(db, row) if row else {"session_id": None, "session_version": 0, "tasks": [], "focus_task_id": None})}
    if message.strip() in {"确认", "执行", "确认转账"}:
        view = await _view(db, row) if row else {"session_id": None, "session_version": 0, "tasks": [], "focus_task_id": None}
        selected = next((task for task in view["tasks"] if task["chat_task_id"] ==
                         (focus_task_id or view["focus_task_id"])), None)
        if selected and selected["transfer_task_id"]:
            reply = "请在下方转账任务卡核对收款人、付款账户和金额后点击确认；文字回复不会执行转账。"
        elif selected:
            reply = "这笔还是草稿，请先补充：" + "、".join(selected["missing_fields"])
        else:
            reply = "当前没有可确认的转账任务，请先说明收款人和金额。"
        return {"reply": reply, "mode": nlu.mode, "tool_calls": [], **view}
    if row and STATUS_QUERY.search(message):
        view = await _view(db, row)
        selected = next((task for task in view["tasks"] if task["chat_task_id"] ==
                         (focus_task_id or view["focus_task_id"])), None)
        return {"reply": selected["task"]["message"] if selected and selected["task"] else
                f"当前任务状态：{selected['status']}" if selected else "请先选择转账任务卡。",
                "mode": nlu.mode, "tool_calls": [], **view}
    if message.strip() in {"取消", "算了"}:
        target_id = focus_task_id or state.get("focus_task_id")
        target = next((task for task in tasks if task["chat_task_id"] == target_id), None)
        if target is None:
            raise ManagerError("请先选择要取消的任务卡", "TASK_SELECTION_REQUIRED")
        if target.get("transfer_task_id"):
            return {"reply": "这笔已生成转账计划，请在任务卡上取消。", "mode": nlu.mode,
                    "tool_calls": [], **await _view(db, row)}
        changed = await db.execute(update(NovaSessionORM).where(
            NovaSessionORM.session_id == row.session_id, NovaSessionORM.version == session_version
        ).values(version=session_version + 1))
        if changed.rowcount != 1:
            raise ManagerError("会话已更新，请刷新后重试", "STALE_SESSION_VERSION")
        target["status"] = "CANCELLED"
        target["missing_fields"] = []
        target["candidate_map"] = []
        target["candidates"] = []
        row.state = state
        row.version = session_version + 1
        response = {"reply": "这笔转账草稿已取消，未创建转账计划。",
                    "mode": nlu.mode, "tool_calls": [], "session_id": row.session_id}
        db.add(NovaMessageReceiptORM(receipt_id=receipt_id, user_id=user_id,
                                     payload_digest=payload_digest, response=response))
        db.add(NovaTaskMessageORM(message_id=receipt_id, session_id=row.session_id,
                                  user_id=user_id, content_masked=_masked_message(message),
                                  chat_task_ids=[target_id]))
        await db.commit()
        return {**response, **await _view(db, row)}
    task_decisions = []
    if candidate_id:
        chunks, extracted = [message], []
    elif nlu.mode == "model":
        safe_history = [
            {
                "role": item.get("role"),
                "content": redact_sensitive(str(item.get("content", ""))),
            }
            for item in (conversation_history or [])
            if item.get("role") in {"user", "assistant"}
        ]
        active_tasks = []
        for task in tasks:
            current_status = task["status"]
            if task.get("transfer_task_id"):
                snapshot = await task_snapshot(db, task["transfer_task_id"], user_id)
                current_status = (
                    snapshot["outcome"] if snapshot["phase"] == "FINISHED"
                    else snapshot["phase"]
                )
            if current_status in {"CANCELLED", "SUCCEEDED", "FAILED"}:
                continue
            slots = task.get("slots", {})
            active_tasks.append({
                "chat_task_id": task["chat_task_id"],
                "is_focus": task["chat_task_id"] == state.get("focus_task_id"),
                "status": current_status,
                "recipient": slots.get("recipient"),
                "amount_fen": slots.get("amount_fen"),
                "payer_account_id": slots.get("from_account") or slots.get("payer"),
                "note": slots.get("note"),
                "phone_last4": slots.get("phone", "")[-4:] or None,
                "card_last4": slots.get("card", "")[-4:] or None,
                "missing_fields": task.get("missing_fields", []),
            })
        turn = await nlu.extract_tasks(message, {
            "focus_task_id": focus_task_id or state.get("focus_task_id"),
            "pending_tasks": active_tasks,
            "trusted_history_reference": ({
                "resolved": True,
                "reuse_recipient": trusted_fields["history_reference"]["reuse_recipient"],
                "reuse_amount": trusted_fields["history_reference"]["reuse_amount"],
            } if trusted_fields and trusted_fields.get("history_reference") else None),
            "conversation_history": safe_history,
            "page_context": page_context or {},
            "current_tool_results": current_tool_results or [],
            "goal_plan": goal_plan,
            "skill_instructions": load_skill("transfer"),
        })
        task_decisions = list(turn.tasks)
        chunks = [message] * len(task_decisions)
        extracted = [
            await _extract(nlu, message, bool(pending), decision)
            for decision in task_decisions
        ]
        if turn.clarification_required and not extracted:
            return {"reply": "当前有多笔可能的转账任务，请先选择任务卡或明确收款人。",
                    "mode": nlu.mode, "tool_calls": [],
                    **(await _view(db, row) if row else {
                        "session_id": None, "session_version": 0,
                        "tasks": [], "focus_task_id": None,
                    })}
    else:
        chunks = _split(message)
        if len(chunks) > 8:
            raise ManagerError("一次最多处理8笔转账", "TRANSFER_BATCH_LIMIT", 422)
        extracted = [await _extract(nlu, chunk, bool(pending)) for chunk in chunks]
    if trusted_fields and len(extracted) != 1:
        raise ManagerError("由查询结果补全的字段一次只能用于一笔转账", "TRUSTED_FIELDS_AMBIGUOUS", 422)
    if extracted and trusted_fields:
        if trusted_fields.get("amount_fen") is not None:
            extracted[0]["amount_fen"] = trusted_fields["amount_fen"]
            extracted[0]["amount_ratio"] = None
            extracted[0]["amount_source_tx_id"] = trusted_fields.get("amount_source_tx_id")
        if trusted_fields.get("payer_account_id") is not None:
            extracted[0]["payer"] = trusted_fields["payer_account_id"]
        history = trusted_fields.get("history_reference")
        if history is not None:
            extracted[0]["history_reference_tx_id"] = history["tx_id"]
            if history.get("reuse_recipient"):
                extracted[0]["recipient"] = history["recipient"]
                extracted[0]["to_account"] = history["to_account"]
    if not candidate_id and all(item["intent"] in {"unknown", "status", "control"} and
                                not any(item.get(key) for key in ("recipient", "phone", "card", "amount_fen", "amount_ratio"))
                                for item in extracted):
        return {"reply": "请说明要发起、补充或修改哪笔转账。", "mode": nlu.mode, "tool_calls": [],
                **(await _view(db, row) if row else {"session_id": None, "session_version": 0, "tasks": [], "focus_task_id": None})}
    if row is None:
        row = NovaSessionORM(session_id=session_id or "NOVA-" + uuid4().hex, user_id=user_id, version=0, state={})
        db.add(row)
        await db.flush()
    else:
        changed = await db.execute(update(NovaSessionORM).where(
            NovaSessionORM.session_id == row.session_id, NovaSessionORM.version == session_version
        ).values(version=session_version + 1))
        if changed.rowcount != 1:
            raise ManagerError("会话已更新，请刷新后重试", "STALE_SESSION_VERSION")
    messages = []
    touched = []
    for index, item in enumerate(extracted if not candidate_id else [{}]):
        target_id = focus_task_id or state.get("focus_task_id")
        target = next((task for task in tasks if task["chat_task_id"] == target_id), None)
        decision = task_decisions[index] if task_decisions else None
        is_new = (
            decision.task_action == "create" if decision is not None else
            len(chunks) > 1 or (item.get("intent") == "transfer" and not MODIFY.search(message))
        )
        if candidate_id:
            target = next((task for task in tasks if any(c["candidate_id"] == candidate_id
                                                         for c in task.get("candidate_map", []))), None)
            if target is None or target.get("candidate_expires_at", 0) < time.time():
                raise ManagerError("候选已过期，请重新提供收款人", "STALE_CANDIDATE")
            choice = next(c for c in target["candidate_map"] if c["candidate_id"] == candidate_id)
            target["slots"]["to_account" if choice["kind"] == "recipient" else "from_account"] = choice["account_id"]
            target["selected_account"] = choice["account_id"]
        elif decision is not None and decision.task_action == "update":
            target = next((
                task for task in tasks
                if task["chat_task_id"] == decision.chat_task_id
                and task["status"] not in {"CANCELLED", "SUCCEEDED", "FAILED"}
            ), None)
            if target is None:
                raise ManagerError("模型指定的转账任务不存在或已结束",
                                   "TASK_SELECTION_REQUIRED")
        elif decision is not None and is_new:
            target = None
        elif is_new and not (target and target["status"] in {"COLLECTING", "NEEDS_SELECTION"}
                             and "再" not in message and
                             (focus_task_id or item.get("recipient") == target["slots"].get("recipient"))):
            target = None
        elif target is None and len(pending) == 1:
            target = pending[0]
        elif target is None and pending:
            raise ManagerError("有多笔待补充转账，请先选择任务卡", "TASK_SELECTION_REQUIRED")
        if target is None:
            if len(tasks) >= 30:
                raise ManagerError("当前会话任务已满，请新建聊天窗口", "SESSION_TASK_LIMIT")
            target = {"chat_task_id": uuid4().hex, "origin_request_id": request_id,
                      "status": "COLLECTING", "slots": {},
                      "missing_fields": [], "candidates": [], "skill_version": SKILL_VERSION}
            tasks.append(target)
        touched.append(target["chat_task_id"])
        if target.get("transfer_task_id") and (
            decision.task_action != "update" if decision is not None else not MODIFY.search(message)
        ):
            raise ManagerError("已有待确认计划；修改请明确说“改成…”，新转账请说“再给…”", "TASK_ALREADY_PLANNED")
        slots = target["slots"]
        previous = dict(slots)
        for key in ("recipient", "phone", "card", "to_account", "amount_fen",
                    "amount_source_tx_id", "history_reference_tx_id", "payer", "note"):
            if item.get(key) is not None:
                slots[key] = item[key]
        if item.get("clear_note"):
            slots["note"] = None
            slots["note_cleared"] = True
        elif item.get("note") is not None:
            slots.pop("note_cleared", None)
        if item.get("amount_ratio"):
            slots["amount_ratio"] = item["amount_ratio"]
        elif item.get("amount_fen") is not None:
            slots.pop("amount_ratio", None)
            slots.pop("amount_basis_available_fen", None)
            slots.pop("amount_basis_at", None)
        if item.get("recipient") and item["recipient"] != previous.get("recipient"):
            for key in ("phone", "card", "to_account", "phone_source"):
                if item.get(key) is None:
                    slots.pop(key, None)
            slots.pop("no_auto_recipient", None)
        if item.get("phone") != previous.get("phone") and item.get("phone"):
            slots.pop("to_account", None)
            slots.pop("no_auto_recipient", None)
        if item.get("card") != previous.get("card") and item.get("card"):
            slots.pop("to_account", None)
            slots.pop("no_auto_recipient", None)
        if item.get("payer") != previous.get("payer"):
            slots.pop("from_account", None)
        await _resolve(db, bank, user_id, target)
        if candidate_id:
            chosen_key = "to_account" if choice["kind"] == "recipient" else "from_account"
            slots[chosen_key] = choice["account_id"]
            target["missing_fields"] = [field for field in target["missing_fields"]
                                        if field != ("选择收款账户" if choice["kind"] == "recipient" else "付款账户")]
            if not target["missing_fields"]:
                target["status"] = "READY_FOR_REVIEW"
        if not target["missing_fields"] and slots.get("to_account") and slots.get("from_account"):
            params = dict(user_id=user_id, request_id=f"NOVA-{request_id}-{index}",
                          operation_id=f"NOVA-{request_id}-{index}",
                          from_account=slots["from_account"], to_account=slots["to_account"],
                          amount=Decimal(slots["amount_fen"]) / 100,
                          note=slots.get("note"),
                          note_source=("manual" if slots.get("note") else
                                       "cleared" if slots.get("note_cleared") else "automatic"),
                          recipient_phone=slots.get("phone"), commit=False)
            if target.get("transfer_task_id"):
                current = await task_snapshot(db, target["transfer_task_id"], user_id)
                snapshot = await modify_task(db, bank, task_id=target["transfer_task_id"],
                                             state_version=current["state_version"], **params)
            else:
                snapshot = await create_transfer_task(db, bank,
                                                      recipient_card_number=slots.get("card"), **params)
            target["transfer_task_id"] = snapshot["task_id"]
            target["status"] = snapshot["phase"]
            slots.pop("amount_ratio", None)
            messages.append(f"第{index + 1}笔转账已生成待确认计划，请核对任务卡。")
        else:
            messages.append(f"第{index + 1}笔转账请补充或选择：{'、'.join(target['missing_fields'])}")
        state["focus_task_id"] = target["chat_task_id"]
    state["tasks"] = tasks
    row.state = state
    row.version = session_version + 1
    response = {"reply": "\n".join(messages), "mode": nlu.mode, "tool_calls": [],
                "session_id": row.session_id}
    db.add(NovaMessageReceiptORM(receipt_id=receipt_id, user_id=user_id,
                                 payload_digest=payload_digest, response=response))
    db.add(NovaTaskMessageORM(message_id=receipt_id, session_id=row.session_id,
                              user_id=user_id, content_masked=_masked_message(message),
                              chat_task_ids=touched))
    await db.commit()
    return {**response, **await _view(db, row)}
