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
from .nlu import NLUEngine, UnderstandingError, amount_fen, PHONE, scope_guard, offline_extract
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
            "recipient": slots.get("recipient"), "amount_fen": slots.get("amount_fen"),
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


def wants_transfer(message: str, pending: bool = False) -> bool:
    text = message.strip()
    if RECORD_QUERY.search(text) or READ_QUERY.search(text):
        return False
    if TRANSFER.search(text) or (pending and (MODIFY.search(text) or re.search(PHONE + "|" + CARD + r"|\d+(?:\.\d+)?\s*(?:元|块)?$", text))):
        return True
    if pending and re.fullmatch(r"[\u4e00-\u9fff]{2,4}", text) and text not in {"余额", "账单", "日历", "状态", "进度"}:
        return True
    return pending and text in {"确认", "执行", "取消", "算了", "状态", "进度"}


def _split(message: str) -> list[str]:
    text = message.strip()
    each = re.fullmatch(r"(?:请|帮我|麻烦)?\s*给\s*([^，,；;]+?)\s*和\s*([^，,；;]+?)\s*各转\s*(.+)", text)
    if each:
        return [f"给{each[1]}转{each[3]}", f"给{each[2]}转{each[3]}"]
    chunks = [part.strip() for part in re.split(r"[；;]|[，,](?=\s*(?:再?给|转给|向))", text) if part.strip()]
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


async def _resolve(db: AsyncSession, user_id: str, task: dict) -> None:
    slots = task["slots"]
    name, phone, card = slots.get("recipient"), slots.get("phone"), slots.get("card")
    missing = []
    task["candidates"] = []
    task["candidate_map"] = []
    if not name:
        missing.append("收款人姓名")
    if not slots.get("amount_fen"):
        missing.append("转账金额")
    if not name:
        task["missing_fields"], task["status"] = missing, "COLLECTING"
        return
    known = await _known(db, user_id, name)
    verified_name = known[0]["name"] if len({item["name"] for item in known}) == 1 else name
    if not known and not phone and not card:
        missing.append("完整手机号或完整银行卡号")
    if known and not phone and not card:
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
        recipients = directory["recipients"] if directory else await _card_match(db, user_id, card, verified_name)
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


async def _extract(nlu: NLUEngine, text: str, pending: bool) -> dict:
    card = re.search(CARD, text)
    clean = text.replace(card[0], "") if card else text
    phone = re.search(PHONE, clean)
    if card:
        clean = re.sub(r"银行卡号|卡号|银行卡", "", clean)
    # Existing NLU handles phone numbers, but explicit extraction also covers a reply with only a number.
    extraction_context = {"missing_recipient": pending, "skill_instructions": load_skill("transfer")}
    # Full recipient identifiers stay inside the bank application, even in model mode.
    result = (offline_extract(clean, extraction_context) if redact_sensitive(text) != text else
              await nlu.extract(clean, extraction_context))
    for key in ("recipient", "recipient_phone", "amount_text", "payer", "note"):
        value = getattr(result, key)
        if value is not None and value not in clean:
            raise UnderstandingError("提取的信息与本轮消息不一致，请重新描述")
    name_match = re.search(r"(?:给|向|转给)\s*([\u4e00-\u9fffA-Za-z]{2,20}?)(?=\s*(?:转|汇|打|手机号|手机|银行卡|卡号|\d))", clean)
    name = name_match[1] if name_match else result.recipient
    if name and re.fullmatch(PHONE, name):
        name = None
    if name and ("手机号" in name or "银行卡" in name or len(name) > 30):
        name = None
    intent = "transfer" if result.intent == "unknown" and TRANSFER.search(text) else result.intent
    return {"intent": intent, "recipient": name, "phone": phone[0] if phone else result.recipient_phone,
            "card": card[0] if card else None,
            "amount_fen": amount_fen(result.amount_text) if result.amount_text else None,
            "payer": result.payer, "note": result.note}


async def process_message(db: AsyncSession, bank: BankApi, nlu: NLUEngine, *, user_id: str,
                          message: str, request_id: str, session_id: str | None,
                          session_version: int, focus_task_id: str | None = None,
                          candidate_id: str | None = None, create_session: bool = False) -> dict:
    receipt_id = _digest([user_id, request_id])
    payload_digest = _digest([message, session_id, session_version, focus_task_id, candidate_id])
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
    chunks = _split(message)
    if len(chunks) > 8:
        raise ManagerError("一次最多处理8笔转账", "TRANSFER_BATCH_LIMIT", 422)
    pending = [task for task in tasks if task["status"] in {"COLLECTING", "NEEDS_SELECTION"}]
    if scope_guard(message):
        return {"reply": "目前聊天仅支持即时人民币转账；定时、周期、AA及外币业务请使用对应工作台。", "mode": nlu.mode,
                "tool_calls": [], **(await _view(db, row) if row else {"session_id": None, "session_version": 0, "tasks": [], "focus_task_id": None})}
    if message.strip() in {"确认", "执行", "确认转账"}:
        view = await _view(db, row) if row else {"session_id": None, "session_version": 0, "tasks": [], "focus_task_id": None}
        selected = next((task for task in view["tasks"] if task["chat_task_id"] ==
                         (focus_task_id or view["focus_task_id"])), None)
        if selected and selected["transfer_task_id"]:
            reply = "请在下方转账任务卡点击“前往转账页核对并确认”；文字回复不会执行转账。"
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
        response = {"reply": f"草稿任务 {target_id[:8]} 已取消，未创建转账计划。",
                    "mode": nlu.mode, "tool_calls": [], "session_id": row.session_id}
        db.add(NovaMessageReceiptORM(receipt_id=receipt_id, user_id=user_id,
                                     payload_digest=payload_digest, response=response))
        db.add(NovaTaskMessageORM(message_id=receipt_id, session_id=row.session_id,
                                  user_id=user_id, content_masked=_masked_message(message),
                                  chat_task_ids=[target_id]))
        await db.commit()
        return {**response, **await _view(db, row)}
    extracted = [] if candidate_id else [await _extract(nlu, chunk, bool(pending)) for chunk in chunks]
    if not candidate_id and all(item["intent"] in {"unknown", "status", "control"} and
                                not any(item.get(key) for key in ("recipient", "phone", "card", "amount_fen"))
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
        is_new = len(chunks) > 1 or (item.get("intent") == "transfer" and not MODIFY.search(message))
        if candidate_id:
            target = next((task for task in tasks if any(c["candidate_id"] == candidate_id
                                                         for c in task.get("candidate_map", []))), None)
            if target is None or target.get("candidate_expires_at", 0) < time.time():
                raise ManagerError("候选已过期，请重新提供收款人", "STALE_CANDIDATE")
            choice = next(c for c in target["candidate_map"] if c["candidate_id"] == candidate_id)
            target["slots"]["to_account" if choice["kind"] == "recipient" else "from_account"] = choice["account_id"]
            target["selected_account"] = choice["account_id"]
        elif is_new and not (target and target["status"] in {"COLLECTING", "NEEDS_SELECTION"}
                             and item.get("recipient") == target["slots"].get("recipient")
                             and "再" not in message):
            target = None
        elif target is None and len(pending) == 1:
            target = pending[0]
        elif target is None and pending:
            raise ManagerError("有多笔待补充转账，请先选择任务卡", "TASK_SELECTION_REQUIRED")
        if target is None:
            if len(tasks) >= 30:
                raise ManagerError("当前会话任务已满，请新建聊天窗口", "SESSION_TASK_LIMIT")
            target = {"chat_task_id": uuid4().hex, "status": "COLLECTING", "slots": {},
                      "missing_fields": [], "candidates": [], "skill_version": SKILL_VERSION}
            tasks.append(target)
        touched.append(target["chat_task_id"])
        if target.get("transfer_task_id") and not MODIFY.search(message):
            raise ManagerError("已有待确认计划；修改请明确说“改成…”，新转账请说“再给…”", "TASK_ALREADY_PLANNED")
        slots = target["slots"]
        previous = dict(slots)
        for key in ("recipient", "phone", "card", "amount_fen", "payer", "note"):
            if item.get(key) is not None:
                slots[key] = item[key]
        if item.get("recipient") and item["recipient"] != previous.get("recipient"):
            for key in ("phone", "card", "to_account", "phone_source"):
                if item.get(key) is None:
                    slots.pop(key, None)
        if item.get("phone") != previous.get("phone") and item.get("phone"):
            slots.pop("to_account", None)
        if item.get("card") != previous.get("card") and item.get("card"):
            slots.pop("to_account", None)
        if item.get("payer") != previous.get("payer"):
            slots.pop("from_account", None)
        await _resolve(db, user_id, target)
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
                          note=slots.get("note"), note_source="manual" if slots.get("note") else "automatic",
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
            messages.append(f"任务 {target['chat_task_id'][:8]} 已生成待确认计划，请核对任务卡。")
        else:
            messages.append(f"任务 {target['chat_task_id'][:8]} 请补充或选择：{'、'.join(target['missing_fields'])}")
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
