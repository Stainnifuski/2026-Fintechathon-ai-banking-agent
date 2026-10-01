"""理财银行事实及独立应用记录；份额使用万分之一整数。"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class WealthProductORM(Base):
    """当前可售版本索引；历史规则不可覆盖。"""
    __tablename__ = "wealth_products"
    product_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="on_sale")


class WealthProductVersionORM(Base):
    """不可变虚构产品规则与披露。"""
    __tablename__ = "wealth_product_versions"
    __table_args__ = (UniqueConstraint("product_id", "version"),)
    version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("wealth_products.product_id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_level: Mapped[int] = mapped_column(Integer, CheckConstraint("risk_level BETWEEN 1 AND 5"), nullable=False)
    rules: Mapped[dict] = mapped_column(JSON, nullable=False)
    disclosure: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WealthNavORM(Base):
    """模拟营业日净值，唯一且不会因结算重试改变。"""
    __tablename__ = "wealth_navs"
    __table_args__ = (UniqueConstraint("version_id", "nav_date"),)
    nav_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("wealth_product_versions.version_id"), nullable=False)
    nav_date: Mapped[date] = mapped_column(Date, nullable=False)
    price: Mapped[str] = mapped_column(String(32), nullable=False)


class WealthAssessmentORM(Base):
    """3号适当性结果；原始答案仅存在受控记录，不写审计参数。"""
    __tablename__ = "wealth_assessments"
    assessment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    questionnaire_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score_version: Mapped[str] = mapped_column(String(64), nullable=False)
    answers: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WealthProfileORM(TimestampMixin, Base):
    """本人模拟财务画像与版本；外部资产永不成为付款账户。"""
    __tablename__ = "wealth_profiles"
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class WealthGoalORM(TimestampMixin, Base):
    """金额、期限、流动性和优先级独立于产品分类。"""
    __tablename__ = "wealth_goals"
    goal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)


class WealthPlanORM(TimestampMixin, Base):
    """5号计划快照；修改创建新版本并撤销旧确认。"""
    __tablename__ = "wealth_plans"
    plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    data: Mapped[dict] = mapped_column(JSON, nullable=False)


class WealthAuthorizationORM(Base):
    """3号可信逐笔授权，银行必须验证记录而非仅验证非空ID。"""
    __tablename__ = "wealth_authorizations"
    authorization_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    operation_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    param_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    command: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="AWAITING_CONFIRMATION")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WealthVerificationORM(Base):
    """逐笔验证证据；模拟点击必须显式标记，禁止作为真实强验证凭据。"""
    __tablename__ = "wealth_verifications"
    authorization_id: Mapped[str] = mapped_column(ForeignKey("wealth_authorizations.authorization_id"), primary_key=True)
    method: Mapped[str] = mapped_column(String(32), CheckConstraint("method IN ('TOTP','SIMULATED_CLICK')"), nullable=False)
    provider_version: Mapped[str] = mapped_column(String(32), nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    replacement_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WealthOrderORM(TimestampMixin, Base):
    """4号权威订单，原操作号及完整摘要唯一。"""
    __tablename__ = "wealth_orders"
    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("wealth_product_versions.version_id"), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), CheckConstraint("direction IN ('purchase','redeem')"), nullable=False)
    amount_fen: Mapped[int] = mapped_column(BigInteger, CheckConstraint("amount_fen >= 0"), nullable=False)
    units_scaled: Mapped[int] = mapped_column(BigInteger, CheckConstraint("units_scaled >= 0"), nullable=False)
    param_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    authorization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_id: Mapped[str | None] = mapped_column(String(64))
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pricing_date: Mapped[date] = mapped_column(Date, nullable=False)
    confirm_date: Mapped[date] = mapped_column(Date, nullable=False)
    cash_date: Mapped[date] = mapped_column(Date, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class WealthLotORM(Base):
    """份额批次及剩余成本；整批清空释放全部成本。"""
    __tablename__ = "wealth_lots"
    lot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("wealth_orders.operation_id"), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"), nullable=False)
    version_id: Mapped[str] = mapped_column(ForeignKey("wealth_product_versions.version_id"), nullable=False)
    units_scaled: Mapped[int] = mapped_column(BigInteger, CheckConstraint("units_scaled >= 0"), nullable=False)
    cost_fen: Mapped[int] = mapped_column(BigInteger, CheckConstraint("cost_fen >= 0"), nullable=False)
    acquired_date: Mapped[date] = mapped_column(Date, nullable=False)
    unlock_date: Mapped[date] = mapped_column(Date, nullable=False)


class WealthCashHoldORM(Base):
    """共享现金占款，不在未确认状态制造真实扣款。"""
    __tablename__ = "wealth_cash_holds"
    operation_id: Mapped[str] = mapped_column(ForeignKey("wealth_orders.operation_id"), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"), nullable=False, index=True)
    amount_fen: Mapped[int] = mapped_column(BigInteger, CheckConstraint("amount_fen > 0"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="HELD")


class WealthUnitHoldORM(Base):
    """逐批次份额占用，账户串行锁保护并发赎回。"""
    __tablename__ = "wealth_unit_holds"
    __table_args__ = (UniqueConstraint("operation_id", "lot_id"),)
    hold_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("wealth_orders.operation_id"), nullable=False)
    lot_id: Mapped[str] = mapped_column(ForeignKey("wealth_lots.lot_id"), nullable=False, index=True)
    units_scaled: Mapped[int] = mapped_column(BigInteger, CheckConstraint("units_scaled > 0"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="HELD")


class WealthSettlementORM(Base):
    """唯一结算事件及分录结果；事务与订单、审计一致。"""
    __tablename__ = "wealth_settlements"
    event_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("wealth_orders.operation_id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WealthClockORM(Base):
    """隔离演示业务时间，不参与JWT、问卷有效期和TOTP校验。"""
    __tablename__ = "wealth_clocks"
    clock_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    business_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calendar_version: Mapped[str] = mapped_column(String(64), nullable=False)


class WealthConversationORM(TimestampMixin, Base):
    """有限离线对话持久化，验证代码不进入此表。"""
    __tablename__ = "wealth_conversations"
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)


class WealthExecutionORM(TimestampMixin, Base):
    """5号网络提交观察，与银行权威订单状态分离。"""
    __tablename__ = "wealth_executions"
    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    authorization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class WealthSchemaVersionORM(Base):
    """可重复增量升级标记；不修改既有账户金额。"""
    __tablename__ = "wealth_schema_versions"
    schema_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
