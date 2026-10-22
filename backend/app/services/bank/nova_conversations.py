"""Bank-side conversation API. Plaintext is confined to this service boundary."""
from ..audit.spans import audited
import base64
import hashlib
import os
import re
from uuid import uuid4
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.nova_conversation import NovaConversationORM, NovaConversationMessageORM


class ConversationError(Exception):
    def __init__(self, message: str = "会话不存在", status_code: int = 404):
        self.message, self.status_code = message, status_code
        super().__init__(message)


def _key() -> bytes:
    secret = os.getenv("NOVA_CONVERSATION_KEY") or os.getenv("JWT_SECRET", "dev-secret-key-change-in-production")
    return hashlib.sha256(("nova-conversations-v1:" + secret).encode()).digest()


def _encrypt(value: str) -> str:
    nonce = os.urandom(12)
    return base64.urlsafe_b64encode(nonce + AESGCM(_key()).encrypt(nonce, value.encode(), None)).decode()


def _decrypt(value: str) -> str:
    raw = base64.urlsafe_b64decode(value)
    return AESGCM(_key()).decrypt(raw[:12], raw[12:], None).decode()


def redact(value: str) -> str:
    """Remove complete recipient identifiers before showing history or calling an LLM."""
    value = redact_proof(value)
    value = re.sub(r"(?<!\d)(?:\d[ -]?){15,18}\d(?!\d)", "[银行卡号已隐藏]", value)
    return re.sub(r"(?<!\d)(?:\+86[ -]?)?1[3-9](?:[ -]?\d){9}(?!\d)", "[手机号已隐藏]", value)


def redact_proof(value: str) -> str:
    """Keep fund codes intact while preventing chat from carrying MFA proofs."""
    return re.sub(r'((?:验证码|动态码|短信码|TOTP|OTP)\s*(?:是|为|[:：])?\s*)\d{6}(?!\d)',
                  r'\1[验证码已隐藏，请在可信验证卡输入]', value, flags=re.IGNORECASE)


@audited("assistant")
async def create(db: AsyncSession, user_id: str) -> dict:
    now = datetime.now(timezone.utc)
    row = NovaConversationORM(conversation_id="NOVA-" + uuid4().hex, user_id=user_id,
                              title_ciphertext=_encrypt("新对话"), created_at=now, updated_at=now)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _summary(row)


def _summary(row: NovaConversationORM) -> dict:
    return {"session_id": row.conversation_id, "title": redact(_decrypt(row.title_ciphertext)),
            "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()}


async def list_conversations(db: AsyncSession, user_id: str) -> list[dict]:
    rows = (await db.scalars(select(NovaConversationORM).where(NovaConversationORM.user_id == user_id)
                             .order_by(NovaConversationORM.updated_at.desc(), NovaConversationORM.conversation_id.desc())
                             .limit(100))).all()
    return [_summary(row) for row in rows]


async def get(db: AsyncSession, user_id: str, session_id: str) -> dict:
    row = await db.get(NovaConversationORM, session_id)
    if row is None or row.user_id != user_id:
        raise ConversationError()
    messages = (await db.scalars(select(NovaConversationMessageORM).where(
        NovaConversationMessageORM.conversation_id == session_id)
        .order_by(NovaConversationMessageORM.sequence, NovaConversationMessageORM.position))).all()
    return {**_summary(row), "messages": [
        {"role": item.role, "content": redact(_decrypt(item.content_ciphertext)),
         "request_id": item.request_id} for item in messages]}


async def append_turn(db: AsyncSession, user_id: str, session_id: str,
                      request_id: str, message: str, reply: str) -> dict:
    row = await db.get(NovaConversationORM, session_id)
    if row is None or row.user_id != user_id:
        raise ConversationError()
    existing = await db.scalar(select(NovaConversationMessageORM).where(
        NovaConversationMessageORM.conversation_id == session_id,
        NovaConversationMessageORM.request_id == request_id,
        NovaConversationMessageORM.role == "user"))
    if existing:
        if _decrypt(existing.content_ciphertext) != message:
            raise ConversationError("请求编号已用于其他消息", 409)
        return _summary(row)
    has_messages = await db.scalar(select(NovaConversationMessageORM.message_id).where(
        NovaConversationMessageORM.conversation_id == session_id).limit(1))
    if not has_messages:
        row.title_ciphertext = _encrypt(redact(message[:40]))
    sequence = (await db.scalar(select(func.coalesce(func.max(NovaConversationMessageORM.sequence), -1)).where(
        NovaConversationMessageORM.conversation_id == session_id))) + 1
    for position, (role, content) in enumerate((("user", message), ("assistant", reply))):
        db.add(NovaConversationMessageORM(message_id=uuid4().hex, conversation_id=session_id,
                                          request_id=request_id, role=role, sequence=sequence, position=position,
                                          content_ciphertext=_encrypt(content)))
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return _summary(row)


@audited("assistant")
async def remove(db: AsyncSession,user_id: str,session_id: str):
    """Purge conversation/cache only. Bank transactions and audit remain owned facts."""
    from ...integrations.wealth_authorization import lock_profile
    from ...models.wealth import WealthConversationORM
    from ...models.wealth_research import WealthChatRequestORM
    from ...models.nova_task import NovaSessionORM,NovaTaskMessageORM,NovaMessageReceiptORM
    await lock_profile(db,user_id)
    row=await db.get(NovaConversationORM,session_id,populate_existing=True)
    if not row or row.user_id!=user_id:raise ConversationError()
    cards=(await db.scalars(select(WealthConversationORM).where(WealthConversationORM.user_id==user_id))).all()
    related=[c for c in cards if c.data.get('nova_session_id')==session_id]
    cache_ids={s for c in related for s in c.data.get('action_session_ids',[])}
    cache_ids.update(c.session_id for c in related)
    for c in cards:
        if c.session_id in cache_ids:await db.delete(c)
    receipts=(await db.scalars(select(WealthChatRequestORM).where(WealthChatRequestORM.user_id==user_id))).all()
    for r in receipts:
        if r.response.get('session_id')==session_id or r.response.get('session_id') in cache_ids:await db.delete(r)
    await db.execute(delete(NovaTaskMessageORM).where(NovaTaskMessageORM.user_id==user_id,NovaTaskMessageORM.session_id==session_id))
    task_session=await db.get(NovaSessionORM,session_id)
    if task_session and task_session.user_id==user_id:await db.delete(task_session)
    transfer_receipts=(await db.scalars(select(NovaMessageReceiptORM).where(NovaMessageReceiptORM.user_id==user_id))).all()
    for r in transfer_receipts:
        if r.response.get('session_id')==session_id:await db.delete(r)
    await db.execute(delete(NovaConversationMessageORM).where(NovaConversationMessageORM.conversation_id==session_id))
    await db.delete(row)
    await db.commit()
