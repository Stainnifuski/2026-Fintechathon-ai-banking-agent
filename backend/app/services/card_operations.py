"""卡片操作预览编排：银行预检 + 授权开单，供卡片页接口与 NOVA 预览工具共用。

只生成待本人核对的预览；确认、强验证与银行受理只在受认证保护的卡片接口完成。
"""

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations import card_authorization as auth
from ..integrations.card_authorization import CardAuthorizationError
from ..models.card import CardOperationORM
from ..schemas.cards import CardOperationCreate, CardOperationView
from .bank import card_service as cards

ACTION_LABELS = {
    "card_apply_virtual": "申请虚拟卡",
    "card_set_transaction_limits": "设置交易限额",
    "card_adjust_credit_limit": "调整信用额度",
    "card_unlock": "解锁卡片",
    "card_report_lost": "卡片挂失",
    "card_lock": "锁定卡片",
    "card_cancel_virtual": "注销虚拟卡",
    "card_reissue": "挂失补卡",
    "card_set_channels": "设置交易渠道",
}


def operation_message(row: CardOperationORM) -> str:
    label = ACTION_LABELS[row.action]
    if row.status == auth.AWAITING_CONFIRMATION:
        if row.permission_level == "red":
            return f"{label}为高风险操作：请核对银行返回的变更，确认后还需输入验证器动态码"
        return f"请核对银行返回的变更，确认后{label}"
    return {
        auth.AWAITING_MFA: f"已确认{label}，请输入验证器6位动态码完成强验证",
        auth.AUTHORIZED: f"{label}已授权，正在提交银行",
        auth.COMPLETED: f"{label}已完成",
        auth.CANCELLED: f"{label}已取消，未提交银行",
        auth.EXPIRED: f"{label}确认已过期，未提交银行，请重新预览",
    }.get(row.status, f"{label}未执行（{row.error_code}）")


def operation_view(row: CardOperationORM) -> CardOperationView:
    return CardOperationView(
        operation_id=row.operation_id, action=row.action, status=row.status,
        permission_level=row.permission_level,
        requires_confirmation=row.permission_level in ("yellow", "red"),
        requires_mfa=row.permission_level == "red",
        digest=row.param_digest, card_id=row.card_id, card=row.preview.get("card"),
        changes=row.preview.get("changes", []), result=row.result, error_code=row.error_code,
        failed_verifications=row.failed_verifications, expires_at=row.expires_at,
        allowed_actions=auth.allowed_actions(row), message=operation_message(row),
    )


async def open_preview(db: AsyncSession, *, user_id: str, request: CardOperationCreate) -> CardOperationORM:
    """银行只读预检后开授权单；不提交，调用方负责提交或回滚。

    原号重放直接返回原预览，避免以新银行状态覆盖用户已核对的内容。
    """
    params = request.bank_params()
    existing = await db.get(CardOperationORM, request.operation_id)
    if existing is not None:
        row = await auth.open_operation(
            db, user_id=user_id, operation_id=request.operation_id, action=request.action,
            card_id=existing.card_id, card_state_version=existing.card_state_version,
            params=params, preview=existing.preview)
        if row.card_id != request.card_id:
            raise CardAuthorizationError("原操作号参数冲突", "IDEMPOTENCY_CONFLICT")
        return row
    preview = await cards.preview_card_operation(
        db, user_id=user_id, action=request.action, card_id=request.card_id, params=params)
    return await auth.open_operation(
        db, user_id=user_id, operation_id=request.operation_id, action=request.action,
        card_id=preview["card_id"], card_state_version=preview["card_state_version"],
        params=params, preview=jsonable_encoder(preview))
