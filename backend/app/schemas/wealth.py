"""理财模拟接口；现金为分、份额为精确字符串。"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..domain.wealth import units


class StrictRequest(BaseModel):
    """拒绝未定义字段，防止客户端夹带已验证标志。"""
    model_config = ConfigDict(extra="forbid")


class AssessmentSubmission(StrictRequest):
    questionnaire_version: str
    profile_version: int = Field(ge=1, strict=True)
    adult: bool = Field(strict=True)
    full_capacity: bool = Field(strict=True)
    answers: dict[str, int]

    @field_validator("answers", mode="before")
    @classmethod
    def strict_answers(cls, value: object) -> object:
        """Pydantic转换前禁止布尔值和字符串伪装选项编号。"""
        if not isinstance(value, dict) or any(type(item) is not int for item in value.values()):
            raise ValueError("问卷选项必须为整数")
        return value


class InvestmentCommand(StrictRequest):
    operation_id: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    direction: Literal["purchase", "redeem"]
    product_id: str = Field(min_length=1, max_length=32)
    product_version: int = Field(ge=1, strict=True)
    disclosure_version: int = Field(ge=1, strict=True)
    account_id: str = Field(min_length=1, max_length=32)
    amount_fen: int = Field(default=0, ge=0, le=2_000_000_000, strict=True)
    units: str = "0.0000"
    currency: Literal["CNY"] = "CNY"
    plan_id: str | None = Field(default=None, max_length=64)
    plan_version: int = Field(default=1, ge=1, strict=True)
    command_version: Literal[1, 2] = 1
    redemption_mode: Literal["standard", "quick"] = "standard"
    nav_quote_id: str | None = Field(default=None, max_length=96)
    quoted_cash_fen: int | None = Field(default=None, ge=1, le=2_000_000_000, strict=True)
    context_id: str | None = Field(default=None, max_length=64)
    context_epoch: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def direction_amount(self) -> "InvestmentCommand":
        """正式赎回只能以份额提交，禁止金额和份额混合。"""
        quantity = units(self.units, positive=False)
        if self.direction == "purchase" and (self.amount_fen <= 0 or quantity != 0):
            raise ValueError("申购必须提供金额且份额为零")
        if self.direction == "redeem" and (self.amount_fen != 0 or quantity <= 0):
            raise ValueError("赎回必须提供份额且金额为零")
        self.units = format(quantity, ".4f")
        if self.redemption_mode == "quick" and (self.direction != "redeem" or self.command_version != 2):
            raise ValueError("快赎仅支持v2赎回命令")
        if self.command_version == 1 and (self.nav_quote_id or self.quoted_cash_fen or self.context_id or self.context_epoch):
            raise ValueError("新增绑定字段必须使用v2命令")
        return self


class PersonalConfirmation(StrictRequest):
    digest: str = Field(min_length=64, max_length=64)
    disclosure_version: int = Field(ge=1, strict=True)
    accepted: bool = Field(strict=True)

    @field_validator("accepted")
    @classmethod
    def explicit_true(cls, value: bool) -> bool:
        """禁止1等转换成同意，且必须明确选择true。"""
        if value is not True:
            raise ValueError("必须本人明确同意")
        return value


class StrongVerification(StrictRequest):
    proof: str = Field(pattern=r"^\d{6}$")


class InternalInvestmentSubmission(StrictRequest):
    subject_user_id: str = Field(max_length=32)
    authorization_id: str = Field(max_length=64)
    command: InvestmentCommand


class ProfileUpdate(StrictRequest):
    version: int = Field(ge=1, strict=True)
    monthly_income_fen: int = Field(ge=0, le=2_000_000_000, strict=True)
    monthly_expenses_fen: int = Field(ge=0, le=2_000_000_000, strict=True)
    liabilities_fen: int = Field(ge=0, le=2_000_000_000, strict=True)
    external_assets: list["ExternalAsset"] = Field(default_factory=list, max_length=30)


class ExternalAsset(StrictRequest):
    asset_id: str = Field(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
    label: str = Field(min_length=1, max_length=64)
    category: Literal["cash", "bond", "mixed", "equity", "index", "other"]
    amount_fen: int = Field(ge=0, le=2_000_000_000, strict=True)
    currency: Literal["CNY"] = "CNY"


class GoalAllocation(StrictRequest):
    asset_ref: str = Field(min_length=1, max_length=100)
    amount_fen: int = Field(gt=0, le=2_000_000_000, strict=True)


class GoalInput(StrictRequest):
    goal_id: str | None = Field(default=None, max_length=64)
    version: int = Field(ge=1, strict=True)
    title: str = Field(min_length=1, max_length=64)
    amount_fen: int = Field(ge=0, le=2_000_000_000, strict=True)
    due_date: date
    liquidity: Literal["immediate", "flexible", "long_term"]
    priority: int = Field(ge=1, le=5, strict=True)
    allocations: list[GoalAllocation] = Field(default_factory=list, max_length=30)


class PlanInput(StrictRequest):
    kind: Literal["new_cash", "adjustment"] = "new_cash"
    account_id: str = Field(min_length=1, max_length=32)
    version: int = Field(default=1, ge=1, strict=True)
    budget_fen: int = Field(ge=0, le=2_000_000_000, strict=True)
    reserve_fen: int = Field(ge=0, le=2_000_000_000, strict=True)
    horizon_days: int = Field(ge=1, le=3650, strict=True)
    excluded_products: list[str] = Field(default_factory=list, max_length=60)
    need: Literal['cash','balanced','growth'] | None = None


class PlanVersion(StrictRequest):
    version: int = Field(ge=1, strict=True)


class WealthMessage(StrictRequest):
    message: str = Field(min_length=1, max_length=500)
    session_id: str | None = Field(default=None, max_length=64)
    version: int | None = Field(default=None, ge=1, strict=True)
    client_message_id: str | None = Field(default=None,min_length=8,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')


class WealthResearchQuery(StrictRequest):
    dataset_key: str | None = Field(default=None,max_length=96)
    query: str = Field(default='',max_length=200)
    category: str | None = Field(default=None,max_length=32)
    max_risk: int | None = Field(default=None,ge=1,le=5,strict=True)
    horizon_days: int | None = Field(default=None,ge=1,le=3650,strict=True)
    page: int = Field(default=1,ge=1,le=10000)
    page_size: int = Field(default=12,ge=1,le=60)
    product_ids: list[str] = Field(default_factory=list,max_length=4)
    period: Literal['1m','3m','1y','since']='1y'


class WealthContextChange(StrictRequest):
    context_id: str = Field(max_length=64)
    version: int = Field(ge=1)


class AdoptItem(StrictRequest):
    operation_id: str = Field(max_length=64)
    amount_fen: int | None = Field(default=None, gt=0, le=2_000_000_000, strict=True)
    units: str | None = None


class AdoptPlan(PlanVersion):
    items: list[AdoptItem] = Field(max_length=5)


class BoundConfirmation(PersonalConfirmation):
    authorization_id: str = Field(max_length=64)


class PlanConfirmation(PlanVersion):
    items: list[BoundConfirmation] = Field(min_length=1, max_length=5)
