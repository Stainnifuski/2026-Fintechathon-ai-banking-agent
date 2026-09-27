"""3号授权与风险服务端口的本地 MVP 适配器。"""

from datetime import date
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import AuthorizationStatus, RiskReservationStatus
from ..models.workflow import AuthorizationORM, RiskReservationORM

YELLOW_LIMIT_FEN = 100_000


class AuthorizationError(Exception):
    def __init__(self, message: str, code: str = "AUTHORIZATION_REJECTED") -> None:
        self.message = message
        self.code = code
        super().__init__(message)


class CapabilityNotImplemented(Exception):
    def __init__(self, capability: str) -> None:
        self.capability = capability
        super().__init__(f"{capability} 接口已定义，但当前适配器尚未实现")

# 累计转账超过1000，为RED
async def precheck_risk(
    db: AsyncSession,
    *,
    user_id: str,
    amount_fen: int,
    daily_transfer_used_fen: int,
) -> str:
    """结合银行 API 返回的累计值与应用侧风险占用做分级。"""
    held = (
        await db.execute(
            select(RiskReservationORM).where(
                RiskReservationORM.user_id == user_id,
                RiskReservationORM.status == RiskReservationStatus.HELD,
            )
        )
    ).scalars().all()
    cumulative = daily_transfer_used_fen + sum(item.amount_fen for item in held)
    return "YELLOW" if cumulative + amount_fen <= YELLOW_LIMIT_FEN else "RED"


def new_authorization(
    *,
    operation_id: str,
    user_id: str,
    plan_version: int,
    param_digest: str,
    risk_level: str,
) -> AuthorizationORM:
    return AuthorizationORM(
        authorization_id=f"AUTH{uuid4().hex[:16].upper()}",
        operation_id=operation_id,
        user_id=user_id,
        plan_version=plan_version,
        status=AuthorizationStatus.PENDING_CONSENT,
        risk_level=risk_level,
        verification_level="CONSENT",
        param_digest=param_digest,
    )


def record_consent(authorization: AuthorizationORM) -> None:
    if authorization.status != AuthorizationStatus.PENDING_CONSENT:
        raise AuthorizationError("授权不在待确认状态")
    authorization.status = (
        AuthorizationStatus.VALID
        if authorization.risk_level == "YELLOW"
        else AuthorizationStatus.PENDING_VERIFICATION
    )


def validate_for_acceptance(
    authorization: AuthorizationORM, *, operation_digest: str, plan_version: int
) -> None:
    if authorization.status != AuthorizationStatus.VALID:
        raise AuthorizationError("授权尚未生效或已经失效")
    if authorization.param_digest != operation_digest or authorization.plan_version != plan_version:
        raise AuthorizationError("授权绑定的参数或计划版本不匹配")


def consume(authorization: AuthorizationORM) -> None:
    if authorization.status != AuthorizationStatus.VALID:
        raise AuthorizationError("授权不能被消费")
    authorization.status = AuthorizationStatus.CONSUMED


def create_risk_hold(
    *, operation_id: str, user_id: str, amount_fen: int
) -> RiskReservationORM:
    return RiskReservationORM(
        reservation_id=f"RSV{uuid4().hex[:16].upper()}",
        operation_id=operation_id,
        user_id=user_id,
        amount_fen=amount_fen,
        risk_date=date.today(),
        status=RiskReservationStatus.HELD,
    )


def verify_consent(authorization: AuthorizationORM, *, proof: str) -> None:
    """核验 MFA 证明，将授权从待验证状态推进为有效。

    当前 MVP：接受任意非空 proof，演示红色完整链路。
    正式版本：对接短信 / 人脸 / U盾 SDK，在此处替换校验逻辑。

    Args:
        authorization: 待验证的授权记录。
        proof: 用户提交的验证凭据（MVP 阶段为任意非空字符串）。

    Raises:
        AuthorizationError: 授权状态不对或凭据为空时抛出。
    """
    if authorization.status != AuthorizationStatus.PENDING_VERIFICATION:
        raise AuthorizationError(
            "授权不在待验证状态", "AUTHORIZATION_WRONG_STATE"
        )
    if not proof or not proof.strip():
        raise AuthorizationError("验证凭据不能为空", "PROOF_EMPTY")
    # TODO: 接入真实 MFA 服务后，在此处校验 proof 的格式与时效
    authorization.status = AuthorizationStatus.VALID
    authorization.verification_level = "MFA"
