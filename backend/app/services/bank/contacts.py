"""Owner-scoped transfer shortcuts; bank directory remains the payment authority."""

from uuid import uuid4
from datetime import datetime, timezone

from sqlalchemy import and_, select

from ...database.connection import AsyncSessionLocal
from ...models.account import AccountORM
from ...models.contact import ContactORM
from ...models.user import UserORM
from ...models.transaction import TransactionORM
from ...models.transfer_recipient_history import TransferRecipientHistoryCutoffORM
from ...models.workflow import TransferOperationORM, TransferPlanORM
from .account_scope import isolated_account_ids
from .transfer_cards import transfer_card_number
from .phone import mask_phone, normalize_phone
from .transfer_service import BankError, transfer_directory
from .data_service import lookup_recipient_card


def _view(contact: ContactORM) -> dict:
    return {
        "contact_id": contact.contact_id,
        "name": contact.name,
        "nickname": contact.nickname,
        "phone_masked": mask_phone(contact.phone) if contact.phone_known else None,
        "card_masked": f"**** **** **** {contact.account_number[-4:]}" if contact.card_known else None,
        "bank": contact.bank,
    }


def _nickname(value: str | None) -> str | None:
    result = value.strip() if value is not None else ""
    if len(result) > 64 or any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise BankError("备注名最多64字且不能包含控制字符", "INVALID_CONTACT_NICKNAME", 422)
    return result or None


async def list_contacts(owner_id: str) -> list[dict]:
    async with AsyncSessionLocal() as db:
        rows = (await db.scalars(select(ContactORM).where(ContactORM.user_id == owner_id)
                                 .order_by(ContactORM.name, ContactORM.contact_id))).all()
        return [_view(row) for row in rows]


async def create_contact(owner_id: str, phone: str | None, account_id: str | None,
                         nickname: str | None, card_number: str | None = None,
                         name: str | None = None) -> dict:
    manual = name is not None
    if manual:
        name = name.strip()
        if not name or any(ord(char) < 32 or ord(char) == 127 for char in name):
            raise BankError("请输入有效的联系人姓名", "INVALID_CONTACT_NAME", 422)
        if not phone and not card_number:
            raise BankError("请输入手机号或完整模拟银行卡号", "CONTACT_LOOKUP_REQUIRED", 422)
    elif bool(phone) == bool(card_number) or not account_id:
        raise BankError("请选择手机号或模拟收款卡号查询", "CONTACT_LOOKUP_REQUIRED", 422)
    if phone:
        try:
            canonical = normalize_phone(phone)
        except ValueError as error:
            raise BankError(str(error), "INVALID_PHONE", 422) from error
    if card_number:
        matches = await lookup_recipient_card(owner_id, card_number or "")
        if not matches or (account_id and account_id not in {item["account_id"] for item in matches}):
            raise BankError("银行卡号没有对应的可用收款账户", "RECIPIENT_ACCOUNT_INVALID", 422)
        if not account_id:
            account_id = matches[0]["account_id"]
        async with AsyncSessionLocal() as lookup_db:
            account = await lookup_db.get(AccountORM, account_id)
            user = await lookup_db.get(UserORM, account.user_id)
            card_phone = normalize_phone(user.phone)
            if phone and canonical != card_phone:
                raise BankError("手机号与银行卡号不属于同一人", "RECIPIENT_MISMATCH", 409)
            canonical = card_phone
    alias = _nickname(nickname)
    async with AsyncSessionLocal() as db:
        directory = await transfer_directory(db, subject_user_id=owner_id, recipient_phone=canonical)
        if not directory["recipients"]:
            raise BankError("联系人当前没有可用收款账户", "CONTACT_UNAVAILABLE", 404)
        if not account_id:
            account_id = directory["recipients"][0]["account_id"]
        if account_id not in {row["account_id"] for row in directory["recipients"]}:
            raise BankError("请选择已核验的收款账户", "RECIPIENT_ACCOUNT_INVALID", 422)
        existing = await db.scalar(select(ContactORM).where(
            ContactORM.user_id == owner_id, ContactORM.phone == canonical))
        account = await db.get(AccountORM, account_id)
        recipient = next(row for row in directory["recipients"] if row["account_id"] == account_id)
        recipient_user = await db.get(UserORM, recipient["recipient_user_id"])
        if manual and recipient_user.name != name:
            raise BankError("姓名与手机号或银行卡号不匹配", "RECIPIENT_MISMATCH", 409)
        if existing:
            if not manual:
                raise BankError("该收款人已在我的联系人中", "CONTACT_EXISTS", 409)
            existing.phone_known = existing.phone_known or bool(phone)
            existing.card_known = existing.card_known or bool(card_number)
            if card_number:
                existing.account_number = card_number
            if alias is not None:
                existing.nickname = alias
            await db.commit()
            return _view(existing)
        # The stored card is display history only. Resolve current account again before use.
        row = ContactORM(contact_id=uuid4().hex, user_id=owner_id,
                         name=recipient_user.name, nickname=alias,
                         phone=canonical, account_number=card_number or account.card_number,
                         phone_known=bool(phone), card_known=bool(card_number),
                         bank="AI 模拟银行", transfer_count=0)
        db.add(row)
        await db.commit()
        return _view(row)


async def list_contact_recipients(owner_id: str) -> list[dict]:
    """Only saved contacts appear in direct name selection and payment requests."""
    async with AsyncSessionLocal() as db:
        contacts = (await db.scalars(select(ContactORM).where(ContactORM.user_id == owner_id)
                                     .order_by(ContactORM.name, ContactORM.contact_id))).all()
        result = []
        for contact in contacts:
            directory = await transfer_directory(db, subject_user_id=owner_id,
                                                  recipient_phone=contact.phone)
            if not directory["recipients"]:
                continue
            candidate = directory["recipients"][0]
            recipient_user = await db.get(UserORM, candidate["recipient_user_id"])
            if recipient_user.name != contact.name:
                continue
            result.append({"user_id": candidate["recipient_user_id"], "contact_id": contact.contact_id,
                           "name": contact.name, "nickname": contact.nickname,
                           "phone": mask_phone(contact.phone) if contact.phone_known else None,
                           "card_masked": f"**** **** **** {contact.account_number[-4:]}" if contact.card_known else None,
                           "account_id": candidate["account_id"],
                           "bank": contact.bank})
        return result


async def rename_contact(owner_id: str, contact_id: str, nickname: str | None) -> dict:
    alias = _nickname(nickname)
    async with AsyncSessionLocal() as db:
        row = await db.get(ContactORM, contact_id)
        if row is None or row.user_id != owner_id:
            raise BankError("联系人不存在", "CONTACT_NOT_FOUND", 404)
        row.nickname = alias
        await db.commit()
        return _view(row)


async def delete_contact(owner_id: str, contact_id: str) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(ContactORM, contact_id)
        if row is None or row.user_id != owner_id:
            raise BankError("联系人不存在", "CONTACT_NOT_FOUND", 404)
        await db.delete(row)
        await db.commit()


async def resolve_contact(owner_id: str, contact_id: str) -> list[dict]:
    async with AsyncSessionLocal() as db:
        row = await db.get(ContactORM, contact_id)
        if row is None or row.user_id != owner_id:
            raise BankError("联系人不存在", "CONTACT_NOT_FOUND", 404)
        directory = await transfer_directory(db, subject_user_id=owner_id, recipient_phone=row.phone)
        if not directory["recipients"]:
            raise BankError("联系人当前没有可用收款账户", "CONTACT_UNAVAILABLE", 409)
        recipient_user = await db.get(UserORM, directory["recipients"][0]["recipient_user_id"])
        if recipient_user.name != row.name:
            raise BankError("联系人资料已变化，请重新核验后添加", "CONTACT_CHANGED", 409)
        used = (await _transfer_methods(db, owner_id)).get(recipient_user.user_id)
        phone_known = row.phone_known or bool(used and used["phone_number"])
        card_known = row.card_known or bool(used and used["card_number"])
        preferred_id = used["card_account_id"] if used and used["card_account_id"] else None
        if preferred_id is None:
            for item in directory["recipients"]:
                account = await db.get(AccountORM, item["account_id"])
                if account and row.account_number in (account.card_number,
                                                      transfer_card_number(account.account_id)):
                    preferred_id = account.account_id
                    break
        candidates = sorted(directory["recipients"],
                            key=lambda item: item["account_id"] != preferred_id)
        return [{"account_id": item["account_id"], "label": item["label"],
                 "name": row.name,
                 "card_number": transfer_card_number(item["account_id"])
                 if card_known and item["account_id"] == preferred_id else None,
                 "phone_number": row.phone if phone_known else None,
                 "card_account_id": preferred_id if card_known else None,
                 "phone_account_id": used["phone_account_id"] if used and used["phone_account_id"] else item["account_id"] if phone_known else None,
                 "preferred_mode": "card" if card_known else "phone" if phone_known else None}
                for item in candidates]


async def _transfer_methods(db, owner_id: str, cutoff: datetime | None = None) -> dict[str, dict]:
    statement = (select(TransactionORM)
                 .join(AccountORM, TransactionORM.account_id == AccountORM.account_id)
                 .where(AccountORM.user_id == owner_id,
                        TransactionORM.type == "transfer_out",
                        TransactionORM.status == "completed")
                 .order_by(TransactionORM.timestamp.desc(), TransactionORM.tx_id.desc()))
    if cutoff is not None:
        statement = statement.where(TransactionORM.timestamp > cutoff)
    transactions = (await db.scalars(statement)).all()
    operation_ids = [tx.operation_id for tx in transactions if tx.operation_id]
    phone_operations: set[str] = set()
    card_operations: set[str] = set()
    if operation_ids:
        rows = (await db.execute(select(TransferOperationORM.operation_id, TransferPlanORM.params)
                                 .join(TransferPlanORM, and_(
                                     TransferPlanORM.task_id == TransferOperationORM.task_id,
                                     TransferPlanORM.plan_version == TransferOperationORM.plan_version))
                                 .where(TransferOperationORM.operation_id.in_(operation_ids)))).all()
        phone_operations = {operation_id for operation_id, params in rows
                            if params.get("recipient_phone")}
        card_operations = {operation_id for operation_id, params in rows
                           if params.get("recipient_card_confirmed")}
    isolated = set((await db.scalars(isolated_account_ids())).all())
    result: dict[str, dict] = {}
    for tx in transactions:
        if tx.operation_id not in phone_operations and tx.operation_id not in card_operations:
            continue
        account = await db.get(AccountORM, tx.counterparty)
        if (account is None or account.user_id == owner_id or account.account_id in isolated
                or account.status != "active"):
            continue
        user = await db.get(UserORM, account.user_id)
        if user is None or user.status != "active":
            continue
        entry = result.setdefault(user.user_id, {
            "user_id": user.user_id, "account_id": account.account_id, "name": user.name,
            "phone_masked": None, "card_number": None,
            "phone_number": None, "card_account_id": None,
            "phone_account_id": None, "last_transfer_at": tx.timestamp,
        })
        if tx.operation_id in phone_operations:
            if entry["phone_number"] is None:
                entry["phone_number"] = user.phone
                entry["phone_masked"] = mask_phone(user.phone)
                entry["phone_account_id"] = account.account_id
        elif tx.operation_id in card_operations and entry["card_number"] is None:
            entry["card_number"] = transfer_card_number(account.account_id)
            entry["card_account_id"] = account.account_id
            entry["account_id"] = account.account_id
    return result


async def list_transfer_history(owner_id: str) -> list[dict]:
    """Recent actual outgoing recipients, scoped to the payer and currently active users."""
    async with AsyncSessionLocal() as db:
        cutoff = await db.get(TransferRecipientHistoryCutoffORM, owner_id)
        return list((await _transfer_methods(db, owner_id, cutoff.cleared_at if cutoff else None)).values())


async def read_contact_shortcuts(owner_id: str, *, query: str | None = None,
                                 include_history: bool = True, limit: int = 50) -> dict:
    """NOVA read view: search only owner-visible data and never return raw numbers."""
    needle = query.strip().casefold() if query else None
    async with AsyncSessionLocal() as db:
        contacts = (await db.scalars(select(ContactORM).where(ContactORM.user_id == owner_id)
                                     .order_by(ContactORM.name, ContactORM.contact_id))).all()
        items = []
        for contact in contacts:
            searchable = [contact.name, contact.nickname or ""]
            if contact.phone_known:
                searchable.extend((contact.phone, mask_phone(contact.phone)))
            if contact.card_known:
                searchable.append(contact.account_number)
            if needle and not any(needle in value.casefold() for value in searchable):
                continue
            view = _view(contact)
            items.append({"source": "saved_contact", "name": view["name"],
                          "nickname": view["nickname"], "phone_masked": view["phone_masked"],
                          "card_masked": view["card_masked"]})
        if include_history:
            cutoff = await db.get(TransferRecipientHistoryCutoffORM, owner_id)
            history = (await _transfer_methods(db, owner_id,
                       cutoff.cleared_at if cutoff else None)).values()
            for item in history:
                searchable = [item["name"]]
                if item["phone_number"]:
                    searchable.extend((item["phone_number"], item["phone_masked"]))
                if item["card_number"]:
                    searchable.append(item["card_number"])
                if needle and not any(needle in value.casefold() for value in searchable):
                    continue
                items.append({"source": "transfer_history", "name": item["name"],
                              "nickname": None, "phone_masked": item["phone_masked"],
                              "card_masked": (f"**** **** **** {item['card_number'][-4:]}"
                                              if item["card_number"] else None)})
    return {"total": len(items), "has_more": len(items) > limit, "items": items[:limit]}


async def resolve_transfer_recipient(owner_id: str, name: str, mode: str,
                                     number: str | None, account_id: str | None = None) -> dict:
    """Resolve a transfer form at submission time and return a bank-owned error reason."""
    label = name.strip()
    value = (number or "").strip()
    if not label:
        raise BankError("请输入收款人户名或已保存的备注名", "RECIPIENT_NAME_REQUIRED", 422)
    if mode == "phone" and not value:
        raise BankError("请输入完整手机号", "RECIPIENT_PHONE_REQUIRED", 422)
    if value:
        candidates = (await lookup_recipient_card(owner_id, value) if mode == "card"
                      else await _phone_candidates(owner_id, value))
    else:
        candidates = []
    async with AsyncSessionLocal() as db:
        contacts = (await db.scalars(select(ContactORM).where(
            ContactORM.user_id == owner_id,
            (ContactORM.name == label) | (ContactORM.nickname == label),
        ))).all()
        aliases = {contact.phone for contact in contacts}
        if not value:
            if not contacts:
                raise BankError("未找到该备注名对应的已保存联系人", "CONTACT_NOT_FOUND", 404)
            for contact in contacts:
                directory = await transfer_directory(db, subject_user_id=owner_id,
                                                      recipient_phone=contact.phone)
                candidates.extend(directory["recipients"])
        matched = []
        for candidate in candidates:
            account = await db.get(AccountORM, candidate["account_id"])
            user = await db.get(UserORM, account.user_id) if account else None
            if user and (user.name == label or user.phone in aliases):
                if account_id and account.account_id != account_id:
                    continue
                matched.append({"account_id": account.account_id, "name": user.name,
                                "label": candidate["label"]})
        if len(matched) != 1:
            reason = ("户名或备注名与银行卡号/手机号不匹配，请核对后重试" if not matched and value
                      else "该收款人对应多个账户，请输入完整银行卡号")
            raise BankError(reason, "RECIPIENT_MISMATCH" if not matched else "RECIPIENT_AMBIGUOUS", 409)
        return matched[0]


async def _phone_candidates(owner_id: str, phone: str) -> list[dict]:
    from .data_service import lookup_recipient_phone
    return await lookup_recipient_phone(owner_id, phone)


async def clear_transfer_history(owner_id: str) -> None:
    async with AsyncSessionLocal() as db:
        cutoff = await db.get(TransferRecipientHistoryCutoffORM, owner_id)
        if cutoff is None:
            db.add(TransferRecipientHistoryCutoffORM(user_id=owner_id,
                                                     cleared_at=datetime.now(timezone.utc)))
        else:
            cutoff.cleared_at = datetime.now(timezone.utc)
        await db.commit()
