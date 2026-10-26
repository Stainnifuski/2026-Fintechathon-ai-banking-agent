"""Owner-scoped NOVA collection drafts; payment requests require a card action."""
import hashlib
import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import update

from ...models.nova_task import NovaSessionORM
from ..bank.collection_directory import collection_accounts as _accounts, collection_contacts as _contacts
from ..bank.payment_request_service import create_payment_request_batch


CLARIFICATIONS = {
    "payer_scope": "你说的“每个人”是指哪一组人？请说明是全部已保存联系人，还是点名具体联系人。",
    "target_draft": "有多张待填写收款卡，请说明第几张或原付款人，不能猜测目标。",
    "intent": "请说明是继续已有收款草稿，还是新建收款申请。",
}
FIELD_LABELS = {"receive_account": "收款账户"}


class PayerEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    change: Literal["set", "add", "remove"] = Field(
        description="set=指定/替换全部付款人；add=在原有付款人基础上添加；remove=从原有付款人中移除")
    scope: Literal["named", "all_saved_contacts"] = Field(
        description="named=用户点名的人；all_saved_contacts=本人已保存联系人全体（这是范围，不是姓名）")
    names: list[str] = Field(default_factory=list, max_length=20,
                             description="named 时填写每位付款人的姓名或备注名；all_saved_contacts 时为空")

    @model_validator(mode="after")
    def valid_scope(self):
        names = [name.strip() for name in self.names]
        if any(not name or len(name) > 100 for name in names):
            raise ValueError("付款人姓名不能为空且不能超过100字")
        self.names = list(dict.fromkeys(names))
        if self.scope == "named" and not self.names:
            raise ValueError("点名付款人时须提供姓名")
        if self.scope == "all_saved_contacts" and self.names:
            raise ValueError("全部已保存联系人是范围，不能同时填写姓名")
        return self


class ManageCollectionRequestArguments(BaseModel):
    """One explicit collection card action, interpreted by the calling model."""

    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["create", "update", "cancel"]
    target_card_id: str | None = Field(default=None, max_length=64)
    payers: PayerEdit | None = None
    amount_fen: int | None = Field(default=None, ge=1)
    split_equally: bool | None = None
    note: str | None = Field(default=None, max_length=256)
    clear_note: bool = False
    receive_account_id: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def target_matches_action(self):
        if self.action == "create" and self.target_card_id is not None:
            raise ValueError("新建收款卡不能指定已有卡片")
        if self.action in {"update", "cancel"} and self.target_card_id is None:
            raise ValueError("修改或撤销须指定收款卡 ID")
        if self.clear_note and self.note is not None:
            raise ValueError("不能同时填写和清除备注")
        return self


async def manage_collection_request(arguments: ManageCollectionRequestArguments, context) -> dict:
    """Validate business references and update one collection draft."""
    if not (context and context.user_id and context.session_id and context.request_id):
        raise CollectionError("缺少收款会话上下文", "COLLECTION_CONTEXT_REQUIRED", 400)
    row = await context.db.get(NovaSessionORM, context.session_id)
    if row and row.user_id != context.user_id:
        raise CollectionError("会话不属于当前用户", "COLLECTION_FORBIDDEN", 403)
    accounts = await _accounts(context.db, context.user_id)
    decision = arguments
    rejected = []
    if arguments.receive_account_id is not None and arguments.receive_account_id not in {
            account["account_id"] for account in accounts}:
        decision = arguments.model_copy(update={"receive_account_id": None})
        rejected.append("receive_account")
    return await create_collection_draft(context, decision=decision, rejected=rejected)


class CollectionError(Exception):
    def __init__(self, message: str, code: str = "COLLECTION_ERROR", status_code: int = 409):
        self.message, self.code, self.status_code = message, code, status_code
        super().__init__(message)


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
    return ("以下内容未通过业务核验，未作修改：" + "、".join(FIELD_LABELS[item] for item in rejected)
            + "。请核对后重试。")


def _target_card(decision: ManageCollectionRequestArguments, open_cards: list[dict]) -> dict:
    if not open_cards:
        raise CollectionError("当前没有可修改或撤销的收款草稿", "COLLECTION_DRAFT_REQUIRED", 422)
    target = next((card for card in open_cards if card["collection_id"] == decision.target_card_id), None)
    if target is None:
        raise CollectionError("指定的收款任务卡不存在、已结束或不属于当前会话。",
                              "COLLECTION_SELECTION_REQUIRED", 422)
    return target


async def create_collection_draft(context, *, decision: ManageCollectionRequestArguments,
                                  rejected: list[str] | None = None) -> dict:
    if not (context and context.user_id and context.session_id and context.request_id):
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
                  if card.get("status") == "DRAFT"]
    contacts = await _contacts(context.db, context.user_id)
    rejected = rejected or []
    accounts = await _accounts(context.db, context.user_id)
    amount = decision.amount_fen
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
            if decision.split_equally is not None:
                card["split_equally"] = decision.split_equally
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
            "amount_fen": amount, "split_equally": decision.split_equally is True,
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
                                          if card.get("status") == "DRAFT"), 1)]

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
