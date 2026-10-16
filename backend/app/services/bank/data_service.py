"""4号模拟银行持有的客户、账户与流水数据访问实现。

银行所有表只能在 ``backend.app.services.bank`` 包内被直接查询或修改。
外层 HTTP 路由只接收普通 DTO，不得接触银行会话或 ORM。
"""
from .account_scope import isolated_account_ids


from uuid import uuid4
from datetime import datetime, timedelta, timezone
from ...domain.time import as_utc

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...database.connection import AsyncSessionLocal
from ...models.account import AccountORM
from ...models.wealth_research import WealthContextORM
from ...models.transaction import TransactionORM
from ...models.user import UserORM
from ...models.user_phone_registry import UserPhoneRegistryORM
from ...models.sms_challenge import SmsChallengeORM
from ...security import sms
from ...security.passwords import hash_password, verify_password
from ...security.totp import (
    current_timestep,
    generate_secret,
    provisioning_uri,
    qr_data_uri,
    verify_code,
)
from .transfer_service import BankError, transfer_directory, _daily_used_fen
from .phone import mask_phone, normalize_phone
from .available_funds import available_cash, held_cash


def _account_dict(account: AccountORM) -> dict:
    return {
        "account_id": account.account_id,
        "user_id": account.user_id,
        "account_type": account.account_type,
        "card_number": account.card_number,
        "balance": account.balance,
        "daily_transfer_used": account.daily_transfer_used,
        "daily_transfer_limit": account.daily_transfer_limit,
        "status": account.status,
    }


def _user_dict(user: UserORM) -> dict:
    return {
        "user_id": user.user_id,
        "name": user.name,
        "phone": user.phone,
        "id_card": user.id_card,
        "risk_level": user.risk_level,
        "created_at": as_utc(user.created_at, legacy_utc=True),
        "status": user.status,
    }


def _transaction_dict(transaction: TransactionORM) -> dict:
    return {
        "tx_id": transaction.tx_id,
        "account_id": transaction.account_id,
        "type": transaction.type,
        "amount": transaction.amount,
        "counterparty": transaction.counterparty,
        "category": transaction.category,
        "note": transaction.note,
        "timestamp": as_utc(transaction.timestamp, legacy_utc=True),
        "status": transaction.status,
        "channel": transaction.channel,
    }


async def list_accounts(user_id: str, *, session: AsyncSession | None = None) -> list[dict]:
    """页面与 Agent 共用查询；可注入已认证请求的会话。"""
    if session is not None:
        return await _list_accounts(session, user_id)
    async with AsyncSessionLocal() as session:
        return await _list_accounts(session, user_id)


async def _list_accounts(session: AsyncSession, user_id: str) -> list[dict]:
    accounts = (
        await session.execute(
            select(AccountORM)
            .where(AccountORM.user_id == user_id,AccountORM.account_id.not_in(isolated_account_ids()))
            .order_by(AccountORM.account_type.desc(), AccountORM.account_id)
        )
    ).scalars().all()
    views = []
    now = datetime.now(timezone.utc)
    for account in accounts:
        view = _account_dict(account)
        view["available_fen"] = await available_cash(session, account.account_id, account.balance_fen)
        view["held_fen"] = await held_cash(session, account.account_id)
        view["daily_transfer_used"] = (
            await _daily_used_fen(session, account_ids=[account.account_id], now=now)
        ) / 100
        views.append(view)
    return views


async def list_balances(
    user_id: str, account_type: str | None = None
) -> list[dict]:
    statement = select(AccountORM).where(AccountORM.user_id == user_id,AccountORM.account_id.not_in(isolated_account_ids()))
    if account_type:
        statement = statement.where(AccountORM.account_type == account_type)
    async with AsyncSessionLocal() as session:
        accounts = (await session.execute(statement)).scalars().all()
        views = []
        now = datetime.now(timezone.utc)
        for account in accounts:
            view = _account_dict(account)
            view["available_fen"] = await available_cash(session, account.account_id, account.balance_fen)
            view["held_fen"] = await held_cash(session, account.account_id)
            view["daily_transfer_used"] = (
                await _daily_used_fen(session, account_ids=[account.account_id], now=now)
            ) / 100
            views.append(view)
    return views


async def list_recipients(subject_user_id: str) -> list[dict]:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(UserORM, AccountORM)
                .join(AccountORM, AccountORM.user_id == UserORM.user_id)
                .where(
                    UserORM.user_id != subject_user_id,
                    UserORM.status == "active",
                    AccountORM.status == "active",
                    AccountORM.account_id.not_in(isolated_account_ids()),
                )
                .order_by(
                    UserORM.name, AccountORM.account_type.desc(), AccountORM.account_id
                )
            )
        ).all()
    seen: set[str] = set()
    recipients: list[dict] = []
    for user, account in rows:
        if user.user_id in seen:
            continue
        seen.add(user.user_id)
        recipients.append(
            {
                "user_id": user.user_id,
                "name": user.name,
                "phone": f"{user.phone[:3]}****{user.phone[-4:]}",
                "account_id": account.account_id,
                "bank": "AI 模拟银行",
            }
        )
    return recipients


async def register_customer(
    *,
    name: str,
    phone: str,
    password: str,
    risk_level: str,
) -> dict:
    try:
        phone = normalize_phone(phone)
    except ValueError as error:
        raise BankError(str(error), "INVALID_PHONE", 422) from error
    async with AsyncSessionLocal() as session:
        existing = (await session.execute(select(UserORM))).scalars().all()
        for user in existing:
            try:
                same_phone = normalize_phone(user.phone) == phone
            except ValueError:
                same_phone = False
            if same_phone:
                raise BankError("该手机号已注册", "PHONE_ALREADY_REGISTERED", 409)

        user_id = f"U{uuid4().hex[:10].upper()}"
        account_id = f"A{uuid4().hex[:10].upper()}"
        user = UserORM(
            user_id=user_id,
            name=name.strip(),
            phone=phone,
            id_card="未填写",
            password_hash=hash_password(password),
            risk_level=risk_level,
            status="active",
        )
        account = AccountORM(
            account_id=account_id,
            user_id=user_id,
            account_type="savings",
            card_number=f"6214****{account_id[-4:]}",
            balance=0.0,
            daily_transfer_used=0.0,
            daily_transfer_limit=50000.0,
            status="active",
        )
        session.add(user)
        await session.flush()
        session.add(account)
        session.add(UserPhoneRegistryORM(canonical_phone=phone, user_id=user_id))
        try:
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise BankError("该手机号已注册", "PHONE_ALREADY_REGISTERED", 409) from error
        await session.refresh(user)
        return _user_dict(user)


async def authenticate_customer(phone: str, password: str) -> dict:
    try:
        phone = normalize_phone(phone)
    except ValueError as error:
        raise BankError("手机号或密码错误", "INVALID_CREDENTIALS", 401) from error
    async with AsyncSessionLocal() as session:
        users = (await session.execute(select(UserORM))).scalars().all()
    matches = []
    for candidate in users:
        try:
            if normalize_phone(candidate.phone) == phone:
                matches.append(candidate)
        except ValueError:
            continue
    if len(matches) > 1:
        raise BankError("手机号数据冲突，请联系管理员", "PHONE_CONFLICT", 409)
    user = matches[0] if matches else None
    if user is None or not verify_password(password, user.password_hash):
        raise BankError("手机号或密码错误", "INVALID_CREDENTIALS", 401)
    if user.status != "active":
        raise BankError("用户已被停用", "CUSTOMER_INACTIVE", 403)
    return _user_dict(user)


async def get_customer(user_id: str) -> dict | None:
    async with AsyncSessionLocal() as session:
        user = await session.get(UserORM, user_id)
    return _user_dict(user) if user else None


async def totp_status(user_id: str) -> dict:
    """查询用户是否已绑定 TOTP 验证器。"""
    async with AsyncSessionLocal() as session:
        user = await session.get(UserORM, user_id)
        if user is None:
            raise BankError("用户不存在", "CUSTOMER_NOT_FOUND", 404)
        return {"enabled": user.totp_enabled}


async def begin_totp_enrollment(user_id: str) -> dict:
    """开始绑定：生成新密钥并置为未启用，返回扫码所需信息。

    每次调用都会重置密钥，因此重新绑定会作废旧的验证器条目。
    """
    async with AsyncSessionLocal() as session:
        user = await session.get(UserORM, user_id)
        if user is None:
            raise BankError("用户不存在", "CUSTOMER_NOT_FOUND", 404)
        secret = generate_secret()
        user.totp_secret = secret
        user.totp_enabled = False
        user.totp_last_timestep = None
        account_name = user.phone or user.name or user_id
        await session.commit()
    uri = provisioning_uri(secret, account_name=account_name)
    return {"secret": secret, "otpauth_uri": uri, "qr_data_uri": qr_data_uri(uri)}


async def confirm_totp_enrollment(user_id: str, code: str) -> dict:
    """确认绑定：校验一次验证码，通过后正式启用 TOTP。"""
    async with AsyncSessionLocal() as session:
        user = await session.get(UserORM, user_id)
        if user is None:
            raise BankError("用户不存在", "CUSTOMER_NOT_FOUND", 404)
        if not user.totp_secret:
            raise BankError("请先开始绑定流程", "TOTP_NOT_STARTED", 409)
        if not verify_code(user.totp_secret, code):
            raise BankError(
                "验证码错误，请确认验证器时间已同步后重试", "TOTP_INVALID", 401
            )
        user.totp_enabled = True
        user.totp_last_timestep = current_timestep()
        await session.commit()
    return {"enabled": True}


async def verify_totp_for_operation(
    session: AsyncSession, user_id: str, code: str
) -> None:
    """转账强验证时校验 TOTP 验证码，含防重放。校验失败抛 BankError。

    使用调用方（编排层）的事务性会话，成功后写入的防重放时间步随
    该事务一并提交；校验失败时抛出异常，由调用方回滚。
    """
    user = await session.get(UserORM, user_id)
    if user is None:
        raise BankError("用户不存在", "CUSTOMER_NOT_FOUND", 404)
    if not user.totp_enabled or not user.totp_secret:
        raise BankError(
            "尚未绑定验证器，请先在安全设置中绑定 Google Authenticator",
            "TOTP_NOT_ENROLLED",
            409,
        )
    if not verify_code(user.totp_secret, code):
        raise BankError("验证码错误或已过期", "TOTP_INVALID", 401)
    step = current_timestep()
    if user.totp_last_timestep is not None and step <= user.totp_last_timestep:
        raise BankError("该验证码已被使用，请等待新验证码", "TOTP_REPLAY", 409)
    user.totp_last_timestep = step


async def _sms_phone(session: AsyncSession, user: UserORM) -> str:
    """短信只发往银行登记的本人手机号，从不接受请求方提供的号码。"""
    registered = await session.scalar(
        select(UserPhoneRegistryORM.canonical_phone).where(UserPhoneRegistryORM.user_id == user.user_id)
    )
    try:
        return registered or normalize_phone(user.phone)
    except ValueError as error:
        raise BankError("未登记可接收短信的手机号", "SMS_NO_PHONE", 409) from error


async def send_sms_challenge(
    session: AsyncSession, user_id: str, purpose: str, reference_id: str
) -> dict:
    """为一笔具体操作生成短信验证码并发送，返回挑战信息（不含验证码）。"""
    if not sms.sms_enabled():
        raise BankError("短信验证未启用，请使用验证器", "SMS_DISABLED", 403)
    if purpose not in sms.PURPOSE_LABELS:
        raise BankError("不支持的短信验证用途", "SMS_PURPOSE_INVALID", 422)
    user = await session.get(UserORM, user_id)
    if user is None:
        raise BankError("用户不存在", "CUSTOMER_NOT_FOUND", 404)
    if user.status != "active":
        raise BankError("用户已被停用", "CUSTOMER_INACTIVE", 403)
    phone = await _sms_phone(session, user)
    now = datetime.now(timezone.utc)

    sent_last_hour = await session.scalar(
        select(func.count()).select_from(SmsChallengeORM).where(
            SmsChallengeORM.user_id == user_id,
            SmsChallengeORM.created_at >= now - timedelta(hours=1),
        )
    )
    if sent_last_hour >= sms.MAX_SENDS_PER_HOUR:
        raise BankError("短信发送过于频繁，请稍后再试或使用验证器", "SMS_RATE_LIMITED", 429)
    previous = (await session.execute(
        select(SmsChallengeORM).where(
            SmsChallengeORM.user_id == user_id,
            SmsChallengeORM.purpose == purpose,
            SmsChallengeORM.reference_id == reference_id,
            SmsChallengeORM.consumed_at.is_(None),
        )
    )).scalars().all()
    for row in previous:
        wait = sms.RESEND_COOLDOWN_SECONDS - (now - as_utc(row.created_at, legacy_utc=True)).total_seconds()
        if wait > 0:
            raise BankError(f"请{int(wait) + 1}秒后再重新获取验证码", "SMS_COOLDOWN", 429)
    # 重新获取后旧验证码立即作废，同一操作任何时刻只有一条有效短信码。
    for row in previous:
        row.consumed_at = now

    challenge_id = f"SMS{uuid4().hex}"
    code = sms.generate_code()
    masked = mask_phone(phone)
    session.add(SmsChallengeORM(
        challenge_id=challenge_id, user_id=user_id, purpose=purpose, reference_id=reference_id,
        code_hash=sms.hash_code(challenge_id, code), phone_masked=masked, attempts=0,
        expires_at=now + timedelta(seconds=sms.CODE_TTL_SECONDS), created_at=now,
    ))
    try:
        await sms.get_sms_provider().send(phone, sms.render_message(purpose, code))
    except sms.SmsDeliveryError as error:
        raise BankError("短信发送失败，请稍后重试或使用验证器", "SMS_DELIVERY_FAILED", 503) from error
    await session.commit()
    return {
        "challenge_id": challenge_id,
        "phone_masked": masked,
        "expires_in": sms.CODE_TTL_SECONDS,
        "resend_after": sms.RESEND_COOLDOWN_SECONDS,
    }


async def verify_sms_for_operation(
    session: AsyncSession,
    user_id: str,
    code: str,
    *,
    challenge_id: str | None,
    purpose: str,
    reference_id: str,
) -> None:
    """校验短信验证码：须属于本人、对应同一操作、未过期且未使用。

    成功时消费挑战，随调用方事务提交。验证码错误时尝试次数单独提交后再抛错，
    否则调用方回滚会抹掉计数，使暴力猜测不受限制。
    """
    if not sms.sms_enabled():
        raise BankError("短信验证未启用，请使用验证器", "SMS_DISABLED", 403)
    if not challenge_id:
        raise BankError("请先获取短信验证码", "SMS_CHALLENGE_REQUIRED", 409)
    row = (await session.execute(
        select(SmsChallengeORM).where(SmsChallengeORM.challenge_id == challenge_id).with_for_update()
    )).scalar_one_or_none()
    if row is None or row.user_id != user_id:
        raise BankError("短信验证码不存在，请重新获取", "SMS_CHALLENGE_NOT_FOUND", 404)
    if row.purpose != purpose or row.reference_id != reference_id:
        raise BankError("该短信验证码不属于当前操作，请重新获取", "SMS_CHALLENGE_MISMATCH", 409)
    if row.consumed_at is not None:
        raise BankError("该短信验证码已使用或已作废，请重新获取", "SMS_USED", 409)
    if as_utc(row.expires_at, legacy_utc=True) <= datetime.now(timezone.utc):
        raise BankError("短信验证码已过期，请重新获取", "SMS_EXPIRED", 409)
    if row.attempts >= sms.MAX_ATTEMPTS:
        raise BankError("短信验证码错误次数过多，请重新获取", "SMS_LOCKED", 429)
    if not sms.code_matches(challenge_id, code, row.code_hash):
        row.attempts += 1
        await session.commit()
        raise BankError("短信验证码错误", "SMS_INVALID", 401)
    row.consumed_at = datetime.now(timezone.utc)


async def verify_second_factor(
    session: AsyncSession,
    user_id: str,
    proof: str,
    *,
    method: str = "totp",
    challenge_id: str | None = None,
    purpose: str,
    reference_id: str,
) -> None:
    """红色强验证统一入口：验证器 TOTP 或短信验证码，二者择一。"""
    if method == "sms":
        await verify_sms_for_operation(
            session, user_id, proof,
            challenge_id=challenge_id, purpose=purpose, reference_id=reference_id,
        )
    elif method == "totp":
        await verify_totp_for_operation(session, user_id, proof)
    else:
        raise BankError("不支持的验证方式", "MFA_METHOD_INVALID", 422)


async def sms_dev_inbox(session: AsyncSession, user_id: str) -> list[dict]:
    """模拟通道开发收件箱：仅演示环境读取本人最近短信。"""
    provider = sms.get_sms_provider()
    if not sms.dev_inbox_enabled() or not isinstance(provider, sms.MockSmsProvider):
        raise BankError("开发短信收件箱未启用", "SMS_DEV_INBOX_DISABLED", 404)
    user = await session.get(UserORM, user_id)
    if user is None:
        raise BankError("用户不存在", "CUSTOMER_NOT_FOUND", 404)
    phone = await _sms_phone(session, user)
    return [{"content": m.content, "sent_at": m.sent_at.isoformat()}
            for m in provider.messages_for(phone)]


async def list_transactions(user_id: str, limit: int = 50) -> list[dict]:
    async with AsyncSessionLocal() as session:
        records = (
            await session.execute(
                select(TransactionORM)
                .join(AccountORM)
                .where(AccountORM.user_id == user_id,
                    AccountORM.account_id.not_in(isolated_account_ids()))
                .order_by(TransactionORM.timestamp.desc())
                .limit(limit)
            )
        ).scalars().all()
    return [_transaction_dict(transaction) for transaction in records]


async def get_transaction(user_id: str, transaction_id: str) -> dict | None:
    async with AsyncSessionLocal() as session:
        transaction = (
            await session.execute(
                select(TransactionORM)
                .join(AccountORM)
                .where(
                    TransactionORM.tx_id == transaction_id,
                    AccountORM.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
    return _transaction_dict(transaction) if transaction else None



async def lookup_recipient_phone(subject_user_id: str, phone: str) -> list[dict]:
    """已认证用户的精确手机号查询，仅返回脱敏可收款账户候选。"""
    async with AsyncSessionLocal() as session:
        result = await transfer_directory(
            session, subject_user_id=subject_user_id, recipient_phone=phone
        )
    return [
        {"account_id": row["account_id"], "label": row["label"]}
        for row in result["recipients"]
    ]
