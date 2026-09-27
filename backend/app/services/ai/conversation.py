"""Persisted proposal bridge. Only orchestration creates/modifies tasks; never executes."""
import hashlib
import json
import re
import time
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from ...integrations.bank_api import BankApi, BankApiError
from sqlalchemy.exc import IntegrityError

from ...models.conversation import ChatReceiptORM, ConversationORM
from ...schemas.tasks import ChatReply, ChatTaskCreate
from ..orchestration import TaskError, create_transfer_task, modify_task, task_snapshot
from .nlu import UnderstandingError, amount_fen, NUM, PHONE, NLUEngine


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def public_candidates(state: dict) -> list[dict]:
    if state.get("candidate_expires", 0) < time.time(): return []
    return [{k: c[k] for k in ("candidate_id", "kind", "label")} for c in state.get("candidates", [])]


async def read_conversation(db: AsyncSession, session_id: str, user_id: str) -> dict:
    row = await db.get(ConversationORM, session_id)
    if row is None or row.user_id != user_id:
        raise TaskError("会话不存在", 404, "SESSION_NOT_FOUND")
    state = row.state
    task = await task_snapshot(db, state["task_id"], user_id) if state.get("task_id") else None
    return ChatReply(session_id=session_id, session_version=row.version,
        kind="status" if task and not state.get("editing") else "clarification", mode=state.get("mode", "offline"),
        message=task["message"] if task and not state.get("editing") else state.get("message", "请描述即时转账"),
        candidates=public_candidates(state), task=task).model_dump(mode="json")


async def process_message(db: AsyncSession, bank: BankApi, nlu: NLUEngine, *, user_id: str, request: ChatTaskCreate) -> dict:
    receipt_id = digest([user_id, request.request_id])
    payload_digest = digest(request.model_dump(mode="json"))
    receipt = await db.get(ChatReceiptORM, receipt_id)
    if receipt:
        if receipt.payload_digest != payload_digest:
            raise TaskError("相同消息编号不能用于不同内容", 409, "MESSAGE_ID_CONFLICT")
        response = dict(receipt.response)
        if response.get("task"):
            response["task"] = await task_snapshot(db, response["task"]["task_id"], user_id)
            response["message"] = response["task"]["message"]
        return response
    session_id = request.session_id or "CHAT-" + receipt_id[:32]
    row = await db.get(ConversationORM, session_id)
    if row and row.user_id != user_id:
        raise TaskError("会话不存在", 404, "SESSION_NOT_FOUND")
    if request.session_id and row is None:
        raise TaskError("会话不存在，请新建会话", 404, "SESSION_NOT_FOUND")
    version = row.version if row else 0
    if version != request.session_version:
        raise TaskError("会话已更新，请刷新后重试", 409, "STALE_SESSION_VERSION")
    state = json.loads(json.dumps(row.state)) if row else {}
    slots = state.setdefault("slots", {})
    task = await task_snapshot(db, state["task_id"], user_id) if state.get("task_id") else None
    if request.task_id and request.task_id != state.get("task_id"):
        raise TaskError("指定任务不属于当前会话", 409, "TASK_BINDING_MISMATCH")
    # No model calls while holding a write lock. Failed extraction never updates business state.
    result = None
    error_message = None
    if not request.candidate_id:
        try:
            result = await nlu.extract(request.message, {"missing_recipient": not slots.get("recipient")})
            for key in ("recipient", "recipient_phone", "amount_text", "payer", "note"):
                value = getattr(result, key)
                if value is not None and value not in request.message:
                    raise UnderstandingError("提取参数没有明确出现在本次消息中，请重新描述")
            parsed_amount = amount_fen(result.amount_text) if result.amount_text is not None else None
            parameter_text = re.split(r"[，,]?\s*(?:备注|用途)[：:\s]*", request.message, maxsplit=1)[0]
            explicit_amounts = re.findall(NUM + r"\s*(?:元|块钱|块)", parameter_text)
            if explicit_amounts and result.intent not in {"unsupported", "control", "status", "unknown"}:
                if parsed_amount != amount_fen(explicit_amounts[0]):
                    raise UnderstandingError("模型金额与原文不一致，请重新描述")
        except (UnderstandingError, ValueError) as error:
            error_message = str(error)
    # Database CAS serializes each session across processes, including completion + receipt.
    try:
        if row:
            changed = await db.execute(update(ConversationORM).where(
                ConversationORM.session_id == session_id, ConversationORM.version == version
            ).values(version=version + 1))
            if changed.rowcount != 1:
                raise TaskError("会话已更新，请刷新后重试", 409, "STALE_SESSION_VERSION")
        else:
            row = ConversationORM(session_id=session_id, user_id=user_id, version=1, state={})
            db.add(row)
            await db.flush()
        kind, message = "clarification", "请补充收款人和金额，备注可选"
        changed_slots = False
        if error_message:
            kind, message = "error", error_message
        elif request.candidate_id:
            candidate = next((c for c in state.get("candidates", []) if c["candidate_id"] == request.candidate_id), None)
            if not candidate or state.get("candidate_expires", 0) < time.time():
                raise TaskError("候选无效或已过期，请重新描述收款人", 409, "STALE_CANDIDATE")
            directory = await bank.transfer_directory(
                subject_user_id=user_id,
                recipient_name=slots.get("recipient_name"),
                recipient_phone=slots.get("recipient_phone"),
            )
            pool = directory["recipients" if candidate["kind"] == "recipient" else "payers"]
            if not any(c["account_id"] == candidate["account_id"] for c in pool):
                raise TaskError("候选已不可用，请重新查询", 409, "STALE_CANDIDATE")
            slots["to_account" if candidate["kind"] == "recipient" else "from_account"] = candidate["account_id"]
            state["candidates"] = []
            changed_slots = True
        elif result.intent == "unsupported":
            kind, message = "unsupported", "目前仅支持即时人民币转账；定时、周期、AA及外币请求未创建任务"
        elif result.intent in {"control", "status"}:
            kind = "status"
            message = task["message"] if task else "没有绑定转账任务，请先描述转账信息"
            if result.intent == "control": message += "。请在对应任务卡片上确认或取消，文字回复不会执行转账"
        elif result.intent == "unknown":
            message = "未能明确理解，请说明收款人和金额，例如：给李四转300元"
        else:
            if task:
                if result.intent != "modify" and not state.get("editing"):
                    raise TaskError("当前会话已有任务；修改请明确说“改成…”，新转账请新建会话", 409, "NEW_SESSION_REQUIRED")
                if not state.get("editing"):
                    if request.task_id != task["task_id"] or request.state_version != task["state_version"]:
                        raise TaskError("修改需要绑定当前任务和状态版本", 409, "STALE_STATE_VERSION")
                    state["editing"] = True
                    state["target_version"] = request.state_version
            recipient_phone = result.recipient_phone
            recipient_name = result.recipient
            if recipient_name and re.fullmatch(PHONE, recipient_name):
                recipient_phone, recipient_name = recipient_name, None
            if recipient_name is not None or recipient_phone is not None:
                state["another_account"] = slots.get("to_account") if "另一个" in request.message else None
                if recipient_name is not None:
                    slots["recipient_name"] = recipient_name
                elif recipient_phone is not None:
                    slots.pop("recipient_name", None)
                if recipient_phone is not None:
                    slots["recipient_phone"] = recipient_phone
                elif recipient_name is not None:
                    slots.pop("recipient_phone", None)
                slots["recipient"] = recipient_phone or recipient_name
                slots.pop("to_account", None)
                state["candidates"] = []
            if result.amount_text is not None: slots["amount_fen"] = parsed_amount
            if re.search(r"清空备注|不要备注|删除备注|无备注", request.message):
                slots["note"], slots["note_source"] = None, "cleared"
            elif result.note is not None:
                slots["note"], slots["note_source"] = result.note, "manual"
            elif "note_source" not in slots:
                purpose = re.search(
                    r"(?:把|为|支付|今天|这次)(?:今天|这次)?(聚餐|房租|学费|餐费)(?:的|款|费用)?",
                    request.message.split("备注", 1)[0],
                )
                slots["note"] = purpose.group(1) if purpose else "转账"
                slots["note_source"] = "automatic"
            if result.payer is not None:
                slots["payer"] = result.payer
                slots.pop("from_account", None)
                state["candidates"] = []
            changed_slots = True
        if changed_slots:
            if task and ("modify" not in task["allowed_actions"] or state.get("target_version") != task["state_version"]):
                raise TaskError("任务已受理或版本变化，不能修改本计划", 409, "PARAMETERS_IMMUTABLE")
            missing = []
            if not slots.get("recipient"): missing.append("收款人")
            if not slots.get("amount_fen"): missing.append("金额")
            if slots.get("recipient"):
                try:
                    directory = await bank.transfer_directory(
                        subject_user_id=user_id,
                        recipient_name=slots.get("recipient_name"),
                        recipient_phone=slots.get("recipient_phone"),
                    )
                except BankApiError as error:
                    raise TaskError(error.message, error.status_code, error.code) from error
                if not slots.get("from_account"):
                    payer = slots.get("payer")
                    # 模型可保留自然语言“账户”后缀；只在银行返回的集合内匹配。
                    payer_names = {payer, payer.removesuffix("账户").strip()} - {""} if payer else set()
                    choices = [c for c in directory["payers"] if not payer or
                               c["account_id"] in payer_names or any(name in c["label"] for name in payer_names)]
                    default = directory.get("default_payer") if not payer else None
                    if default and any(c["account_id"] == default for c in choices): slots["from_account"] = default
                    elif len(choices) == 1: slots["from_account"] = choices[0]["account_id"]
                    else: missing.append("付款账户"); _candidates(state, "payer", directory["payers"])
                if not slots.get("to_account"):
                    choices = directory["recipients"]
                    if len(choices) == 1 and choices[0]["account_id"] == state.get("another_account"):
                        choices = []
                    if len(choices) == 1: slots["to_account"] = choices[0]["account_id"]
                    elif choices: missing.append("同名收款人选择"); _candidates(state, "recipient", choices)
                    else: missing.append("有效收款人（未找到该姓名或手机号）")
            if missing:
                message = "请补充或选择：" + "、".join(missing)
            else:
                params = dict(user_id=user_id, request_id="CHAT-" + receipt_id[:48],
                    operation_id=request.operation_id, from_account=slots["from_account"],
                    to_account=slots["to_account"], amount=Decimal(slots["amount_fen"]) / 100,
                    note=slots.get("note"), note_source=slots.get("note_source"),
                    recipient_phone=slots.get("recipient_phone", ""), commit=False)
                if task:
                    task = await modify_task(db, bank, task_id=task["task_id"], state_version=state["target_version"], **params)
                    kind = "modification"
                else:
                    task = await create_transfer_task(db, bank, **params)
                    kind = "plan"
                state["task_id"] = task["task_id"]
                state["editing"] = False
                state["candidates"] = []
                message = task["message"]
        state["message"], state["mode"] = message, nlu.mode
        row.state = state
        response = ChatReply(session_id=session_id, session_version=version+1, kind=kind,
            message=message, mode=nlu.mode, candidates=public_candidates(state), task=task).model_dump(mode="json")
        db.add(ChatReceiptORM(receipt_id=receipt_id, payload_digest=payload_digest, response=response))
        await db.commit()
        return response
    except IntegrityError as error:
        await db.rollback()
        raise TaskError("请求正在处理或编号冲突，请用原请求重试", 409, "MESSAGE_CONFLICT") from error


def _candidates(state: dict, kind: str, choices: list[dict]) -> None:
    existing = state.get("candidates", [])
    if state.get("candidate_expires", 0) < time.time(): existing = []
    # Reuse IDs only while the exact account + label set remains current.
    old = [c for c in existing if c["kind"] == kind]
    if [(c["account_id"], c["label"]) for c in old] == [(c["account_id"], c["label"]) for c in choices]: return
    state["candidates"] = [c for c in existing if c["kind"] != kind] + [
        dict(c, kind=kind, candidate_id=uuid4().hex) for c in choices]
    state["candidate_expires"] = time.time() + 600
