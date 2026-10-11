"""3号卡片操作授权策略；不访问卡片、账户等银行ORM，级别由服务端固定表判定。"""

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.audit_log import AuditLogORM
from ..models.card import CardOperationORM

# 与 security/common.py 的 PERMISSION_MAP 保持一致（测试校验）；后端镜像只含 backend/，
# 不能在运行时导入仓库根目录的 security 包。未列出的动作按最高级别处理。
# 表中为该动作的最高级别；方向型动作在只降低风险时（调低额度、关闭渠道）降为黄色。
CARD_PERMISSION_LEVELS = {
    "card_apply_virtual": "yellow",
    "card_lock": "yellow",
    "card_cancel_virtual": "yellow",
    "card_reissue": "yellow",
    "card_set_transaction_limits": "red",
    "card_adjust_credit_limit": "red",
    "card_set_channels": "red",
    "card_unlock": "red",
    "card_report_lost": "red",
}
CARD_RISK_REDUCING_ACTIONS = {"card_set_transaction_limits", "card_adjust_credit_limit", "card_set_channels"}

CONFIRMATION_TTL = timedelta(minutes=10)
MAX_FAILED_VERIFICATIONS = 5
MAX_OPEN_OPERATIONS_PER_CARD = 3

AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
AWAITING_MFA = "AWAITING_MFA"
AUTHORIZED = "AUTHORIZED"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"
EXPIRED = "EXPIRED"
OPEN_STATES = {AWAITING_CONFIRMATION, AWAITING_MFA}


class CardAuthorizationError(Exception):
    """授权拒绝；persist=True 表示状态已推进（如过期、锁定），调用方应提交而非回滚。"""

    def __init__(self, message: str, code: str, status_code: int = 409, *, persist: bool = False) -> None:
        self.message, self.code, self.status_code, self.persist = message, code, status_code, persist
        super().__init__(message)


def aware(value: datetime) -> datetime:
    """SQLite无时区值按UTC存储契约解释。"""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def operation_digest(*, action: str, card_id: str | None, card_state_version: int | None, params: dict) -> str:
    """确认绑定动作、卡片版本与完整参数；任一变化都需要重新预览。"""
    payload = {"action": action, "card_id": card_id, "card_state_version": card_state_version, "params": params}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def permission_level(action: str, *, raises_risk: bool) -> str:
    """服务端定级；未列出的动作按最高级别处理，降低风险的方向型变更只需本人确认。"""
    level = CARD_PERMISSION_LEVELS.get(action, "red")
    if action in CARD_RISK_REDUCING_ACTIONS and not raises_risk:
        return "yellow"
    return level


def _audit(db: AsyncSession, row: CardOperationORM, phase: str, result: str, chain: list[str],
           mfa_method: str | None = None) -> None:
    db.add(AuditLogORM(
        audit_id=hashlib.sha256(f"card-{phase}:{row.operation_id}".encode()).hexdigest(),
        session_id=row.operation_id, user_id=row.user_id, intent="card_management",
        permission_level=row.permission_level, action=f"{row.action}_{phase}",
        params={"operation_id": row.operation_id, "card_id": row.card_id, "digest": row.param_digest},
        user_confirmed=row.confirmed_at is not None, mfa_method=mfa_method,
        result=result, decision_chain=chain,
    ))


def _expire(db: AsyncSession, row: CardOperationORM) -> None:
    row.status, row.error_code = EXPIRED, "AUTHORIZATION_EXPIRED"
    _audit(db, row, "expire", "cancelled", ["authorization_ttl_elapsed", "not_submitted_to_bank"])


async def _sweep_open_operations(db: AsyncSession, user_id: str, card_id: str | None) -> int:
    """惰性清扫本人已过期的未结束操作，返回该卡仍有效的未结束操作数。"""
    rows = (await db.execute(select(CardOperationORM).where(
        CardOperationORM.user_id == user_id, CardOperationORM.status.in_(OPEN_STATES)
    ))).scalars().all()
    now, active = datetime.now(timezone.utc), 0
    for row in rows:
        if aware(row.expires_at) <= now:
            _expire(db, row)
        elif card_id is not None and row.card_id == card_id:
            active += 1
    return active


async def open_operation(db: AsyncSession, *, user_id: str, operation_id: str, action: str,
                         card_id: str | None, card_state_version: int | None, params: dict,
                         preview: dict) -> CardOperationORM:
    """以服务端权限表定级；同号同参返回原预览，同号异参拒绝。"""
    digest = operation_digest(action=action, card_id=card_id,
                              card_state_version=card_state_version, params=params)
    existing = await db.get(CardOperationORM, operation_id)
    if existing is not None:
        if existing.user_id != user_id or existing.param_digest != digest:
            raise CardAuthorizationError("原操作号参数冲突", "IDEMPOTENCY_CONFLICT")
        return existing
    if await _sweep_open_operations(db, user_id, card_id) >= MAX_OPEN_OPERATIONS_PER_CARD:
        raise CardAuthorizationError(
            f"该卡片已有{MAX_OPEN_OPERATIONS_PER_CARD}项待确认操作，请先完成或取消", "CARD_OPERATION_PENDING",
            persist=True)
    row = CardOperationORM(
        operation_id=operation_id, user_id=user_id, action=action, card_id=card_id,
        card_state_version=card_state_version, params=params, preview=preview,
        param_digest=digest,
        permission_level=permission_level(action, raises_risk=preview.get("raises_risk", True)),
        status=AWAITING_CONFIRMATION, failed_verifications=0,
        expires_at=datetime.now(timezone.utc) + CONFIRMATION_TTL,
    )
    db.add(row)
    await db.flush()
    return row


async def owned_operation(db: AsyncSession, user_id: str, operation_id: str) -> CardOperationORM:
    row = await db.get(CardOperationORM, operation_id, with_for_update=True, populate_existing=True)
    if row is None or row.user_id != user_id:
        raise CardAuthorizationError("卡片操作不存在", "NOT_FOUND", 404)
    return row


def _require_open(db: AsyncSession, row: CardOperationORM, expected: str) -> None:
    if row.status in OPEN_STATES and aware(row.expires_at) <= datetime.now(timezone.utc):
        _expire(db, row)
        raise CardAuthorizationError("确认已过期，请重新预览", "AUTHORIZATION_EXPIRED", persist=True)
    if row.status != expected:
        raise CardAuthorizationError("卡片操作不在可执行该步骤的状态", "AUTHORIZATION_WRONG_STATE")


def record_confirmation(db: AsyncSession, row: CardOperationORM, digest: str) -> None:
    """本人确认预览摘要；黄色直接获得授权，红色进入强验证。"""
    _require_open(db, row, AWAITING_CONFIRMATION)
    if digest != row.param_digest:
        raise CardAuthorizationError("预览已改变，请核对后重新确认", "DIGEST_MISMATCH")
    row.confirmed_at = datetime.now(timezone.utc)
    row.status = AUTHORIZED if row.permission_level == "yellow" else AWAITING_MFA
    _audit(db, row, "confirm", "success", ["authenticated_subject", "server_permission_level",
                                           "preview_digest_bound", "personal_confirmation"])


def require_verification_pending(db: AsyncSession, row: CardOperationORM) -> None:
    _require_open(db, row, AWAITING_MFA)


def record_verification(db: AsyncSession, row: CardOperationORM, mfa_method: str = "totp") -> None:
    """仅在银行已校验TOTP或短信验证码后调用。"""
    _require_open(db, row, AWAITING_MFA)
    row.verified_at = datetime.now(timezone.utc)
    row.status = AUTHORIZED
    _audit(db, row, "verify", "success", ["authenticated_subject", "personal_confirmation",
                                          f"{mfa_method}_verified", "preview_digest_bound"], mfa_method)


def record_failed_verification(db: AsyncSession, row: CardOperationORM,
                               mfa_method: str = "totp") -> CardAuthorizationError | None:
    """失败计数独立提交并逐次审计；达到上限后终止本次授权。"""
    row.failed_verifications += 1
    _audit(db, row, f"verify_failed_{row.failed_verifications}", "failed",
           ["authenticated_subject", f"{mfa_method}_rejected"], mfa_method)
    if row.failed_verifications >= MAX_FAILED_VERIFICATIONS:
        row.status, row.error_code = FAILED, "VERIFICATION_LOCKED"
        _audit(db, row, "verify_locked", "blocked", ["verification_attempts_exhausted", "authorization_terminated"])
        return CardAuthorizationError("验证失败次数过多，本次操作已终止，请重新发起",
                                      "VERIFICATION_LOCKED", 403, persist=True)
    return None


def consume(row: CardOperationORM, result: dict) -> None:
    if row.status != AUTHORIZED:
        raise CardAuthorizationError("缺少本人确认或强验证", "AUTHORIZATION_REQUIRED", 403)
    row.status, row.result = COMPLETED, result


def mark_failed(db: AsyncSession, row: CardOperationORM, code: str) -> None:
    row.status, row.error_code = FAILED, code
    _audit(db, row, "bank_rejected", "failed", ["authorization_consumed", f"bank_rejected_{code.lower()}"])


def cancel(db: AsyncSession, row: CardOperationORM) -> None:
    if row.status == CANCELLED:
        return
    if row.status not in OPEN_STATES:
        raise CardAuthorizationError("卡片操作已结束，不能取消", "AUTHORIZATION_WRONG_STATE")
    _require_open(db, row, row.status)
    row.status = CANCELLED
    _audit(db, row, "cancel", "cancelled", ["authenticated_subject", "cancelled_before_bank_submission"])


def allowed_actions(row: CardOperationORM) -> list[str]:
    if row.status not in OPEN_STATES or aware(row.expires_at) <= datetime.now(timezone.utc):
        return []
    return ["confirm", "cancel"] if row.status == AWAITING_CONFIRMATION else ["verify", "cancel"]
