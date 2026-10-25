"""Owner-scoped NOVA collection drafts; payment requests require a card action.

The model is the only interpreter of what the user said. The server verifies each
extracted field against the user's own words and server-owned candidates, drops
only the fields it cannot verify, and reports what was actually written.
"""
import hashlib
import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import update

from ...models.nova_task import NovaSessionORM
from ..bank.collection_directory import collection_accounts as _accounts, collection_contacts as _contacts
from ..bank.payment_request_service import create_payment_request_batch
from .llm import get_mode, llm
from .nlu import UnderstandingError, amount_fen
from .skill_registry import load_skill


CLARIFICATIONS = {
    "payer_scope": "你说的“每个人”是指哪一组人？请说明是全部已保存联系人，还是点名具体联系人。",
    "target_draft": "有多张待填写收款卡，请说明第几张或原付款人，不能猜测目标。",
    "intent": "请说明是继续已有收款草稿，还是新建收款申请。",
}
GROUP_CLARIFICATION = CLARIFICATIONS["payer_scope"]
FIELD_LABELS = {"payers": "付款人", "amount": "金额", "note": "备注",
                "receive_account": "收款账户", "target_card": "目标收款卡"}
RECENT_TURNS = 4


class PayerEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    change: Literal["set", "add", "remove"] = Field(
        description="set=指定/替换全部付款人；add=在原有付款人基础上添加；remove=从原有付款人中移除")
    scope: Literal["named", "all_saved_contacts"] = Field(
        description="named=用户点名的人；all_saved_contacts=本人已保存联系人全体（这是范围，不是姓名）")
    names: list[str] = Field(default_factory=list, max_length=20,
                             description="named 时用户说出的每个姓名或备注名，逐字照抄；all_saved_contacts 时为空")
    quote: str = Field(min_length=1, max_length=120, description="原话中指出这些付款人的片段，逐字摘录")


class CollectionInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["create", "update", "cancel", "clarify", "not_collection"]
    clarify_reason: Literal["payer_scope", "target_draft", "intent"] | None = None
    continues_clarification: bool = Field(
        default=False, description="本轮是在回答上一轮助手提出的收款澄清问题")
    target_card_id: str | None = Field(default=None, max_length=64)
    target_quote: str | None = Field(
        default=None, max_length=120, description="存在多张草稿时，原话中用于定位目标卡的片段，逐字摘录")
    payers: PayerEdit | None = None
    amount_quote: str | None = Field(
        default=None, max_length=40, description="原话中的金额数字片段，逐字摘录，如“200”“一百元”")
    amount_mode: Literal["per_person", "split_total"] | None = Field(
        default=None, description="per_person=每人收该金额；split_total=该金额为总额由付款人均分；未提及为null")
    note: str | None = Field(default=None, max_length=256, description="备注内容，逐字摘录")
    clear_note: bool = False
    receive_account_id: str | None = Field(default=None, max_length=32)
    receive_account_quote: str | None = Field(
        default=None, max_length=40, description="原话中指定收款账户的片段，逐字摘录")


INTERPRETER_PROMPT = (
    "你负责理解当前用户关于“本人向别人发起收款”的这一轮话语，输出严格Schema。"
    "你是唯一的语义理解者：服务端不会再用关键词或句式解析原话，只会核验你摘录的片段确实出自用户原话。\n"
    "判断 action：create=发起一笔新的收款；update=补充或修改已有草稿（姓名、金额、AA方式、备注、账户等任一项）；"
    "cancel=撤销尚未发出的草稿；clarify=无法确定时请用户澄清；not_collection=不是本人向别人收款。"
    "已有草稿且用户在补充或修改信息时用 update，即使原话只有一个名字或一个数字；只有明确要另起一笔时才 create。\n"
    "付款人用 payers 表示对付款人的变更：点名的人逐字放入 names；"
    "“全部联系人”等指本人已保存联系人全体时 scope=all_saved_contacts 且 names 为空；"
    "在原有付款人基础上增加用 add，去掉用 remove，换成或首次指定用 set。"
    "“每个人/大家/他们”若无法从草稿或上一轮澄清确定是哪组人，action=clarify 且 clarify_reason=payer_scope；"
    "若已有草稿，这类说法指草稿里已有的付款人，不改 payers。\n"
    "金额只摘录用户说出的数字片段到 amount_quote，不做换算；总额均分（AA、平摊、一共…大家分）用 split_total，"
    "每人各收用 per_person；用户未提及金额或方式时保持 null。\n"
    "多张草稿时，用户必须用序号、原付款人或其他原话特征指明目标，选中的卡填 target_card_id 并摘录 target_quote；"
    "无法唯一定位时 action=clarify、clarify_reason=target_draft。只有一张草稿时直接以它为目标。"
    "无法区分是补充旧草稿还是新建时 clarify_reason=intent。\n"
    "收款账户只能从 receive_accounts 中选择 account_id，并摘录原话片段。\n"
    "awaiting_clarification 非空表示上一轮助手提出了该澄清问题；若本轮是在回答它，"
    "continues_clarification=true，并结合 previous_user_message 理解完整需求，摘录片段可来自这两句用户原话。"
    "其他情况下所有摘录必须来自本轮 message。recent_conversation 只用于理解指代，不能把历史中的金额或姓名当作本轮新内容。\n"
    "未说出的字段一律为 null；不要凭联系人列表添加付款人；不发出申请、不推断授权。\n"
)


class CollectionError(Exception):
    def __init__(self, message: str, code: str = "COLLECTION_ERROR", status_code: int = 409):
        self.message, self.code, self.status_code = message, code, status_code
        super().__init__(message)


def _awaiting_clarification(context) -> tuple[str, str] | None:
    """Return (question, the user request it asked about) for our latest recent collection question."""
    history = (context.conversation_history or [])[-RECENT_TURNS:]
    for index in range(len(history) - 1, 0, -1):
        if history[index].get("role") == "assistant" and history[index].get("content") in CLARIFICATIONS.values():
            if history[index - 1].get("role") == "user":
                return history[index]["content"], history[index - 1].get("content", "")
            return None
    return None


def _sources(context, decision: CollectionInterpretation | None = None) -> list[str]:
    """User utterances a quote may come from."""
    sources = [context.original_message]
    pending = _awaiting_clarification(context)
    if pending and (decision is None or decision.continues_clarification):
        sources.append(pending[1])
    return sources


def _quoted(quote: str | None, sources: list[str]) -> bool:
    return bool(quote and quote.strip() and any(quote.strip() in text for text in sources))


async def _interpret(context, contacts: list[dict], cards: list[dict],
                     accounts: list[dict]) -> CollectionInterpretation:
    if get_mode() != "model":
        raise CollectionError("收款需要启用模型理解你的需求，当前离线模式无法办理。",
                              "COLLECTION_MODEL_REQUIRED", 503)
    pending = _awaiting_clarification(context)
    history = [item for item in (context.conversation_history or [])
               if item.get("role") in {"user", "assistant"}][-RECENT_TURNS:]
    payload = {
        "message": context.original_message,
        "awaiting_clarification": pending[0] if pending else None,
        "previous_user_message": pending[1] if pending else None,
        "recent_conversation": history,
        "open_collection_cards": [{
            "index": index, "collection_id": card["collection_id"],
            "payers": [person["label"] for person in contacts
                       if person["user_id"] in card.get("selected_payer_user_ids", [])],
            "unsaved_names": [name for name in card.get("requested_names", [])
                              if not any(name in (p["name"], p["nickname"]) for p in contacts)],
            "all_saved_contacts": card.get("target_scope") == "all_saved_contacts",
            "amount_fen": card.get("amount_fen"), "split_equally": card.get("split_equally", False),
            "note": card.get("note", ""), "receive_account_id": card.get("receive_account_id"),
        } for index, card in enumerate(cards, 1)],
        "saved_contacts": [{"name": item["name"], "nickname": item["nickname"]} for item in contacts],
        "receive_accounts": accounts,
    }
    try:
        result = await llm.generate_structured(
            messages=[{"role": "system", "content": INTERPRETER_PROMPT + load_skill("collection")},
                      {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            schema=CollectionInterpretation, max_tokens=600,
        )
        return CollectionInterpretation.model_validate(result.data)
    except (ValidationError, ValueError, TypeError) as error:
        raise CollectionError("暂时无法理解这条收款需求，请换个说法重试。",
                              "COLLECTION_UNDERSTANDING_FAILED", 422) from error
    except Exception as error:
        raise CollectionError("收款理解服务暂时不可用，请稍后重试。",
                              "COLLECTION_MODEL_UNAVAILABLE", 503) from error


def _verify(decision: CollectionInterpretation, sources: list[str], cards: list[dict],
            accounts: list[dict]) -> tuple[CollectionInterpretation, list[str]]:
    """Drop each field that is not grounded in the user's words or server candidates."""
    rejected = []
    data = decision.model_dump()
    payers = decision.payers
    if payers is not None:
        names = [name.strip() for name in payers.names if name.strip()]
        valid = _quoted(payers.quote, sources) and (
            (payers.scope == "all_saved_contacts" and not names) or
            (payers.scope == "named" and names and all(_quoted(name, sources) for name in names)))
        if valid:
            data["payers"] = {**payers.model_dump(), "names": list(dict.fromkeys(names))}
        else:
            data["payers"] = None
            rejected.append("payers")
    if decision.amount_quote is not None:
        try:
            if not _quoted(decision.amount_quote, sources) or amount_fen(decision.amount_quote) <= 0:
                raise UnderstandingError("ungrounded")
        except UnderstandingError:
            data["amount_quote"] = None
            rejected.append("amount")
    if decision.note is not None and not _quoted(decision.note, sources):
        data["note"] = None
        rejected.append("note")
    if decision.receive_account_id is not None and not (
            decision.receive_account_id in {item["account_id"] for item in accounts}
            and _quoted(decision.receive_account_quote, sources)):
        data["receive_account_id"] = None
        rejected.append("receive_account")
    open_ids = {card["collection_id"] for card in cards}
    if decision.target_card_id is not None and (
            decision.target_card_id not in open_ids
            or (len(cards) > 1 and not _quoted(decision.target_quote, sources))):
        data["target_card_id"] = None
        rejected.append("target_card")
    return CollectionInterpretation.model_validate(data), rejected


async def inspect_collection_request(context) -> dict:
    """Read the current directory, then let the model resolve the user's words without writing."""
    if not (context and context.user_id and context.request_id and context.original_message):
        raise CollectionError("缺少收款会话上下文", "COLLECTION_CONTEXT_REQUIRED", 400)
    contacts = await _contacts(context.db, context.user_id)
    row = await context.db.get(NovaSessionORM, context.session_id) if context.session_id else None
    if row and row.user_id != context.user_id:
        raise CollectionError("会话不属于当前用户", "COLLECTION_FORBIDDEN", 403)
    cards = [card for card in (row.state.get("collection_cards", []) if row else [])
             if card.get("status") == "DRAFT"]
    accounts = await _accounts(context.db, context.user_id)
    raw = await _interpret(context, contacts, cards, accounts)
    decision, rejected = _verify(raw, _sources(context, raw), cards, accounts)
    return {"request_id": context.request_id, "contact_snapshot": _contact_snapshot(contacts),
            "source_digest": _source_digest(context),
            "decision": decision.model_dump(), "rejected_fields": rejected,
            "saved_contacts": [{"name": item["name"], "nickname": item["nickname"]}
                               for item in contacts]}


def _source_digest(context) -> str:
    return hashlib.sha256(json.dumps(_sources(context), ensure_ascii=False).encode()).hexdigest()


def _inspected(context, contacts: list[dict]) -> tuple[CollectionInterpretation, list[str]]:
    for entry in reversed(context.current_tool_results or []):
        if entry.get("name") != "inspect_collection_request":
            continue
        result = entry.get("result") or {}
        if (result.get("request_id") == context.request_id
                and result.get("contact_snapshot") == _contact_snapshot(contacts)
                and result.get("source_digest") == _source_digest(context)
                and not result.get("error")):
            return (CollectionInterpretation.model_validate(result["decision"]),
                    list(result.get("rejected_fields", [])))
    raise CollectionError("请先查询并确认本轮提到的联系人范围", "COLLECTION_INSPECTION_REQUIRED", 422)


def _normalized_card(card: dict, contacts: list[dict]) -> dict:
    """Older drafts may predate target_scope; derive it from stored structure only."""
    result = dict(card)
    if not result.get("target_scope"):
        result["target_scope"] = "named" if result.get("requested_names") else "unspecified"
    return result


def _yuan(fen: int) -> str:
    return f"{fen // 100}.{fen % 100:02d}元"


def _apply_payers(card: dict, edit: PayerEdit, contacts: list[dict]) -> None:
    all_ids = list(dict.fromkeys(person["user_id"] for person in contacts))
    names = list(edit.names)
    selected, _, _ = _match(names, contacts)
    prior_names = list(card.get("requested_names", []))
    prior_ids = list(card.get("selected_payer_user_ids", []))
    if edit.scope == "all_saved_contacts":
        if edit.change == "remove":
            card.update(target_scope="unspecified", requested_names=[], selected_payer_user_ids=[])
        elif edit.change == "add":
            card.update(target_scope="named", selected_payer_user_ids=list(dict.fromkeys([*prior_ids, *all_ids])))
        else:
            card.update(target_scope="all_saved_contacts", requested_names=[], selected_payer_user_ids=all_ids)
        return
    if edit.change == "set":
        requested, ids = names, selected
    elif edit.change == "add":
        requested = list(dict.fromkeys([*prior_names, *names]))
        ids = list(dict.fromkeys([*prior_ids, *selected]))
    else:
        removed = set(selected)
        requested = [name for name in prior_names if name not in names and not any(
            person["user_id"] in removed and name in (person["name"], person["nickname"])
            for person in contacts)]
        ids = [user_id for user_id in prior_ids if user_id not in removed]
    card.update(target_scope="named" if requested or ids else "unspecified",
                requested_names=requested, selected_payer_user_ids=ids)


def _changes(before: dict, after: dict, contacts: list[dict], accounts: list[dict]) -> list[str]:
    """Describe what was actually written, from stored structure only."""
    label = {person["user_id"]: person["label"] for person in contacts}
    lines = []
    added = [user_id for user_id in after["selected_payer_user_ids"] if user_id not in before["selected_payer_user_ids"]]
    removed = [user_id for user_id in before["selected_payer_user_ids"] if user_id not in after["selected_payer_user_ids"]]
    if added:
        lines.append("添加付款人" + "、".join(label.get(user_id, "联系人") for user_id in added))
    if removed:
        lines.append("移除付款人" + "、".join(label.get(user_id, "联系人") for user_id in removed))
    new_names = [name for name in after["requested_names"] if name not in before["requested_names"]
                 and not any(name in (p["name"], p["nickname"]) for p in contacts)]
    if new_names:
        lines.append("待补全联系人" + "、".join(new_names))
    if after.get("amount_fen") != before.get("amount_fen") and after.get("amount_fen") is not None:
        lines.append("金额改为" + _yuan(after["amount_fen"]))
    if after.get("split_equally") != before.get("split_equally"):
        lines.append("改为AA均分" if after["split_equally"] else "改为每人分别收款")
    if after.get("note") != before.get("note"):
        lines.append("备注已清空" if not after["note"] else "备注已更新")
    if after.get("receive_account_id") != before.get("receive_account_id"):
        tail = next((item["card_masked"] for item in accounts
                     if item["account_id"] == after["receive_account_id"]), "")
        lines.append(f"收款账户改为尾号{tail}" if tail else "收款账户已更换")
    return lines


def _rejected_notice(rejected: list[str]) -> str:
    if not rejected:
        return ""
    return ("以下内容未能从你的原话中确认，未作修改：" + "、".join(FIELD_LABELS[item] for item in rejected)
            + "。请换个说法补充。")


def _target_card(decision: CollectionInterpretation, open_cards: list[dict]) -> dict:
    if not open_cards:
        raise CollectionError("当前没有可修改或撤销的收款草稿", "COLLECTION_DRAFT_REQUIRED", 422)
    if len(open_cards) == 1:
        return open_cards[0]
    target = next((card for card in open_cards if card["collection_id"] == decision.target_card_id), None)
    if target is None:
        raise CollectionError(CLARIFICATIONS["target_draft"], "COLLECTION_SELECTION_REQUIRED", 422)
    return target


async def create_collection_draft(context) -> dict:
    if not (context and context.session_id and context.request_id and context.original_message):
        raise CollectionError("缺少收款会话上下文", "COLLECTION_CONTEXT_REQUIRED", 400)
    row = await context.db.get(NovaSessionORM, context.session_id)
    if row and row.user_id != context.user_id:
        raise CollectionError("会话不属于当前用户", "COLLECTION_FORBIDDEN", 403)
    if row is None and context.session_version != 0:
        raise CollectionError("会话已更新，请刷新后重试", "STALE_SESSION_VERSION")
    if row and row.version != context.session_version:
        raise CollectionError("会话已更新，请刷新后重试", "STALE_SESSION_VERSION")
    if row:
        existing = next((card for card in row.state.get("collection_cards", [])
                         if card["origin_request_id"] == context.request_id), None)
        if existing:
            return {"reply": "请在收款卡片中核对并继续填写。",
                    "session_id": row.session_id, "session_version": row.version,
                    "collection_cards": await collection_cards(context.db, context.user_id, row.session_id)}
    open_cards = [card for card in (row.state.get("collection_cards", []) if row else [])
                  if card.get("status") == "DRAFT" and not card.get('agent_released')]
    contacts = await _contacts(context.db, context.user_id)
    decision, rejected = _inspected(context, contacts)
    if decision.action == 'create' and open_cards and not context.execution_step and not context.task_switch_approved:
        from .task_flow import propose
        pending = await propose(context, '新的收款')
        if pending:
            return pending
    if decision.action == "not_collection":
        raise CollectionError("这不是向别人发起收款的请求", "COLLECTION_INTENT_REQUIRED", 422)
    if decision.action == "clarify":
        reason = decision.clarify_reason or "intent"
        raise CollectionError(CLARIFICATIONS[reason], "COLLECTION_SELECTION_REQUIRED"
                              if reason == "target_draft" else "COLLECTION_INTENT_UNCLEAR", 422)
    accounts = await _accounts(context.db, context.user_id)
    amount = amount_fen(decision.amount_quote) if decision.amount_quote else None
    notice = _rejected_notice(rejected)
    if decision.action in {"update", "cancel"}:
        target = _target_card(decision, open_cards)
        state = json.loads(json.dumps(row.state))
        before = _normalized_card(next(item for item in state["collection_cards"]
                                       if item["collection_id"] == target["collection_id"]), contacts)
        card = json.loads(json.dumps(before))
        if decision.action == "cancel":
            card["status"] = "CANCELLED"
        else:
            if decision.payers is not None:
                _apply_payers(card, decision.payers, contacts)
            if amount is not None:
                card["amount_fen"] = amount
            if decision.amount_mode is not None:
                card["split_equally"] = decision.amount_mode == "split_total"
            if decision.clear_note:
                card["note"] = ""
            elif decision.note is not None:
                card["note"] = decision.note
            if decision.receive_account_id:
                card["receive_account_id"] = decision.receive_account_id
        changes = [] if decision.action == "cancel" else _changes(before, card, contacts, accounts)
        if decision.action == "update" and not changes:
            raise CollectionError(
                notice or "没有识别到需要修改的内容，这张收款草稿保持不变。请说明要补充或修改的付款人、金额、AA方式、备注或收款账户。",
                "COLLECTION_NO_CHANGE", 422)
        state["collection_cards"] = [card if item["collection_id"] == card["collection_id"] else item
                                     for item in state["collection_cards"]]
        changed = await context.db.execute(update(NovaSessionORM).where(
            NovaSessionORM.session_id == row.session_id,
            NovaSessionORM.version == context.session_version
        ).values(version=context.session_version + 1))
        if changed.rowcount != 1:
            raise CollectionError("会话已更新，请刷新后重试", "STALE_SESSION_VERSION")
        row.state, row.version = state, context.session_version + 1
        await context.db.commit()
        reply = ("已撤销这张收款草稿，尚未发出的申请不会发送。" if decision.action == "cancel"
                 else "已更新这张收款草稿：" + "；".join(changes) + "。请在卡片中核对付款人、金额和收款账户。")
        return {"reply": reply + notice, "session_id": row.session_id, "session_version": row.version,
                "collection_cards": await collection_cards(context.db, context.user_id, row.session_id)}
    card = {"collection_id": uuid4().hex, "origin_request_id": context.request_id, "status": "DRAFT",
            "target_scope": "unspecified", "requested_names": [], "selected_payer_user_ids": [],
            "receive_account_id": decision.receive_account_id or (accounts[0]["account_id"] if accounts else ""),
            "amount_fen": amount, "split_equally": decision.amount_mode == "split_total",
            "note": "" if decision.clear_note else (decision.note or "")}
    if decision.payers is not None and decision.payers.change != "remove":
        _apply_payers(card, decision.payers.model_copy(update={"change": "set"}), contacts)
    _, missing, _ = _match(card["requested_names"], contacts)
    if row is None:
        row = NovaSessionORM(session_id=context.session_id, user_id=context.user_id,
                             version=0, state={"tasks": [], "focus_task_id": None})
        context.db.add(row)
        await context.db.flush()
    else:
        changed = await context.db.execute(update(NovaSessionORM).where(
            NovaSessionORM.session_id == row.session_id,
            NovaSessionORM.version == context.session_version
        ).values(version=context.session_version + 1))
        if changed.rowcount != 1:
            raise CollectionError("会话已更新，请刷新后重试", "STALE_SESSION_VERSION")
    state = json.loads(json.dumps(row.state))
    cards = state.setdefault("collection_cards", [])
    if len(cards) >= 30:
        raise CollectionError("当前对话的收款卡片已满，请新建对话", "COLLECTION_CARD_LIMIT")
    cards.append(card)
    row.state, row.version = state, context.session_version + 1
    await context.db.commit()
    return {"reply": ("请先补全未保存的联系人，再在收款卡片中核对并发出申请。" if missing else
                      "收款草稿已生成，请在卡片中核对付款人、金额和收款账户后发出申请。") + notice,
            "session_id": row.session_id, "session_version": row.version,
            "collection_cards": await collection_cards(context.db, context.user_id, row.session_id)}


async def open_collection_drafts(db, user_id: str, session_id: str | None) -> list[dict]:
    """Open-draft summaries the router needs to recognise a continuation."""
    if not session_id:
        return []
    row = await db.get(NovaSessionORM, session_id)
    if not row or row.user_id != user_id:
        return []
    return [{"index": index, "requested_names": card.get("requested_names", []),
             "payer_count": len(card.get("selected_payer_user_ids", [])),
             "amount_fen": card.get("amount_fen")}
            for index, card in enumerate((card for card in row.state.get("collection_cards", [])
                                          if card.get("status") == "DRAFT" and not card.get('agent_released')), 1)]

def _contact_snapshot(contacts: list[dict]) -> str:
    """Bind a read result to the owner-scoped directory used when writing the draft."""
    payload = sorted((item["contact_id"], item["user_id"], item["name"],
                      item["nickname"]) for item in contacts)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def _match(names: list[str], contacts: list[dict]) -> tuple[list[str], list[str], list[str]]:
    selected, missing, ambiguous = [], [], []
    for name in names:
        matches = {item["user_id"] for item in contacts
                   if name in (item["name"], item["nickname"])}
        if len(matches) == 1:
            selected.extend(matches)
        elif matches:
            ambiguous.append(name)
        else:
            missing.append(name)
    return list(dict.fromkeys(selected)), missing, ambiguous


async def _card_view(db, user_id: str, card: dict) -> dict:
    contacts = await _contacts(db, user_id)
    accounts = await _accounts(db, user_id)
    card = _normalized_card(card, contacts)
    matched, missing, ambiguous = _match(card["requested_names"], contacts)
    return {**card, "contacts": contacts, "accounts": accounts,
            "selected_payer_user_ids": card.get("selected_payer_user_ids", matched),
            "unknown_names": missing, "ambiguous_names": ambiguous}


async def collection_cards(db, user_id: str, session_id: str) -> list[dict]:
    row = await db.get(NovaSessionORM, session_id)
    if not row or row.user_id != user_id:
        return []
    return [await _card_view(db, user_id, card) for card in row.state.get("collection_cards", [])]


async def refresh_collection(db, *, user_id: str, session_id: str,
                             collection_id: str, session_version: int) -> dict:
    row = await db.get(NovaSessionORM, session_id)
    if not row or row.user_id != user_id:
        raise CollectionError("收款草稿不存在", "COLLECTION_NOT_FOUND", 404)
    if row.version != session_version:
        raise CollectionError("会话已更新，请刷新后重试", "STALE_SESSION_VERSION")
    state = json.loads(json.dumps(row.state))
    card = next((item for item in state.get("collection_cards", [])
                 if item["collection_id"] == collection_id), None)
    if not card or card["status"] != "DRAFT":
        raise CollectionError("收款草稿已结束", "COLLECTION_NOT_EDITABLE", 409)
    contacts = await _contacts(db, user_id)
    card = _normalized_card(card, contacts)
    state["collection_cards"] = [card if item["collection_id"] == card["collection_id"] else item
                                 for item in state["collection_cards"]]
    matched, _, _ = _match(card["requested_names"], contacts)
    card["selected_payer_user_ids"] = list(dict.fromkeys([
        *[item for item in card["selected_payer_user_ids"]
          if item in {contact["user_id"] for contact in contacts}], *matched,
    ]))
    changed = await db.execute(update(NovaSessionORM).where(
        NovaSessionORM.session_id == session_id, NovaSessionORM.version == session_version
    ).values(version=session_version + 1))
    if changed.rowcount != 1:
        raise CollectionError("会话已更新，请刷新后重试", "STALE_SESSION_VERSION")
    row.state, row.version = state, session_version + 1
    await db.commit()
    return {"session_id": session_id, "session_version": row.version,
            "collection_cards": await collection_cards(db, user_id, session_id)}


async def submit_collection(db, *, user_id: str, session_id: str,
                            collection_id: str, session_version: int,
                            payer_user_ids: list[str], receive_account_id: str,
                            amount: float, split_equally: bool, note: str) -> dict:
    # Hold the draft row through batch creation so a concurrent chat cancellation
    # cannot report "cancelled" while the request is being sent.
    row = await db.get(NovaSessionORM, session_id, with_for_update=True, populate_existing=True)
    if not row or row.user_id != user_id:
        raise CollectionError("收款草稿不存在", "COLLECTION_NOT_FOUND", 404)
    state = json.loads(json.dumps(row.state))
    card = next((item for item in state.get("collection_cards", [])
                 if item["collection_id"] == collection_id), None)
    if not card:
        raise CollectionError("收款草稿不存在", "COLLECTION_NOT_FOUND", 404)
    if card["status"] == "SENT":
        return {"session_id": session_id, "session_version": row.version,
                "collection_cards": await collection_cards(db, user_id, session_id)}
    if card["status"] != "DRAFT":
        raise CollectionError("这张收款草稿已撤销，不能发出申请", "COLLECTION_NOT_EDITABLE", 409)
    if row.version != session_version:
        raise CollectionError("会话已更新，请刷新后重试", "STALE_SESSION_VERSION")
    contacts = await _contacts(db, user_id)
    card = _normalized_card(card, contacts)
    state["collection_cards"] = [card if item["collection_id"] == card["collection_id"] else item
                                 for item in state["collection_cards"]]
    _, unknown, ambiguous = _match(card["requested_names"], contacts)
    unresolved = [name for name in ambiguous if len({item["user_id"] for item in contacts
                  if name in (item["name"], item["nickname"])
                  and item["user_id"] in payer_user_ids}) != 1]
    if unknown or unresolved:
        raise CollectionError("请先补全或选择所有点名的联系人", "COLLECTION_CONTACTS_INCOMPLETE", 422)
    allowed = {item["user_id"] for item in contacts}
    if not payer_user_ids or len(payer_user_ids) != len(set(payer_user_ids)) or not set(payer_user_ids) <= allowed:
        raise CollectionError("请从已保存联系人中选择付款人", "COLLECTION_PAYERS_INVALID", 422)
    if receive_account_id not in {item["account_id"] for item in await _accounts(db, user_id)}:
        raise CollectionError("请选择本人可用的收款账户", "COLLECTION_ACCOUNT_INVALID", 422)
    if len(note) > 256:
        raise CollectionError("备注最多256字", "COLLECTION_NOTE_INVALID", 422)
    key = hashlib.sha256(f"collection:{user_id}:{collection_id}".encode()).hexdigest()
    requests = await create_payment_request_batch(
        requester_user_id=user_id, payer_user_ids=payer_user_ids,
        receive_account_id=receive_account_id, amount=amount,
        split_equally=split_equally, note=note or None, idempotency_key=key,
    )
    changed = await db.execute(update(NovaSessionORM).where(
        NovaSessionORM.session_id == session_id, NovaSessionORM.version == session_version
    ).values(version=session_version + 1))
    if changed.rowcount != 1:
        raise CollectionError("申请已提交，请刷新卡片核对状态", "COLLECTION_SUBMIT_UNCERTAIN")
    card.update(status="SENT", selected_payer_user_ids=payer_user_ids,
                receive_account_id=receive_account_id, amount_fen=round(amount * 100),
                split_equally=split_equally, note=note,
                payment_request_ids=[item["payment_request_id"] for item in requests])
    row.state, row.version = state, session_version + 1
    await db.commit()
    return {"session_id": session_id, "session_version": row.version,
            "collection_cards": await collection_cards(db, user_id, session_id)}
