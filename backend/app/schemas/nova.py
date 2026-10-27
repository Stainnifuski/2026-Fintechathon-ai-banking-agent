"""NOVA 页面上下文：仅接收导航、筛选和选择提示，不接收身份或余额。"""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .bills import BillQuery, Category


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BillFilters(BillQuery):
    model_config = ConfigDict(extra="forbid", strict=True)


class Selection(StrictModel):
    type: Literal["transaction"]
    id: str = Field(min_length=1, max_length=32)


class BillDetailContext(StrictModel):
    category: Category | None = None
    tx_ids: list[str] = Field(default_factory=list, max_length=10)
    page: int = Field(default=1, ge=1, le=10000)


class CalendarSelection(StrictModel):
    event_id: str = Field(min_length=1, max_length=40)
    starts_at: datetime = Field(strict=False)

    @field_validator("starts_at")
    @classmethod
    def aware_start(cls, value):
        if value.tzinfo is None:
            raise ValueError("选中活动时间须带时区")
        return value


class CalendarContext(StrictModel):
    visible_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    selected_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    timezone: str = Field(default="Asia/Shanghai", max_length=80)
    selected_event: CalendarSelection | None = None

    @field_validator("visible_month", "selected_date")
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value + "-01" if len(value) == 7 else value)
        return value

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("时区须为合法 IANA 时区") from None
        return value


class ShopSelection(StrictModel):
    type: Literal["product", "order"]
    id: str = Field(min_length=1, max_length=40)
    quantity: int | None = Field(default=None, ge=1, le=20)

    @model_validator(mode="after")
    def valid_selection(self):
        if self.type == "product" and len(self.id) > 32:
            raise ValueError("商品ID长度最多32")
        if self.type == "order" and self.quantity is not None:
            raise ValueError("订单数量须由服务端查询")
        return self


class ShopContext(StrictModel):
    shop: Literal["flower", "cake"]
    selected: ShopSelection | None = None


class CardContext(StrictModel):
    selected_card_id: str | None = Field(default=None, min_length=1, max_length=32)


class SubscriptionContext(StrictModel):
    selected_subscription_id: str | None = Field(default=None, min_length=1, max_length=40)


class BillWorkspaceSelection(StrictModel):
    type: Literal['transaction','budget','anomaly','recurring','merchant','category_rule','tag']
    id: str = Field(min_length=1, max_length=40)

    @model_validator(mode='after')
    def valid_id(self):
        if self.type == 'transaction' and len(self.id) > 32:
            raise ValueError('流水 ID 长度最多32')
        return self


class BillWorkspaceContext(StrictModel):
    section: Literal['overview','transactions','budgets','checks','forecast','organize']
    subsection: Literal['anomalies','subscriptions','recurring','cashflow','history','merchants','rules'] | None = None
    budget_month: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}$')
    account_id: str | None = Field(default=None, min_length=1, max_length=32)
    selected: BillWorkspaceSelection | None = None

    @model_validator(mode='after')
    def valid_scope(self):
        groups = {'checks':{'anomalies','subscriptions'}, 'forecast':{'recurring','cashflow','history'}, 'organize':{'merchants','rules'}}
        if self.subsection is not None and self.subsection not in groups.get(self.section,set()):
            raise ValueError('账单子功能与当前功能不匹配')
        if self.budget_month:
            month = date.fromisoformat(self.budget_month+'-01')
            if self.section != 'budgets' or not 2000 <= month.year <= 2099:
                raise ValueError('预算月份仅适用于预算管理，年份范围2000至2099')
        if self.account_id is not None and (self.section,self.subsection) != ('forecast','cashflow'):
            raise ValueError('独立预测账户仅适用于现金流预测')
        if self.selected:
            allowed = {'budget':self.section=='budgets','anomaly':self.subsection=='anomalies',
                       'recurring':self.subsection=='recurring','merchant':self.subsection=='merchants',
                       'tag':self.subsection=='rules','category_rule':self.subsection=='rules',
                       'transaction':self.section in {'overview','transactions'} or self.subsection in {'anomalies','subscriptions'}}
            if not allowed[self.selected.type]:
                raise ValueError('选中对象与当前账单功能不匹配')
        return self


class PageContext(StrictModel):
    version: Literal[1] = 1
    page: Literal["home", "transfer", "insights", "cards", "subscriptions", "wealth", "thirdParty"] = "home"
    cards: CardContext | None = None
    subscriptions: SubscriptionContext | None = None
    shop: ShopContext | None = None
    calendar: CalendarContext | None = None
    filters: BillFilters | None = None
    bill_workspace: BillWorkspaceContext | None = None
    details: BillDetailContext | None = None
    selected: Selection | None = None
    observed_as_of: datetime | None = Field(default=None, strict=False)

    @model_validator(mode="after")
    def check_page(self):
        if self.cards is not None and self.page != "cards":
            raise ValueError("卡片选择仅适用于 cards 页面")
        if self.subscriptions is not None and self.page != "subscriptions":
            raise ValueError("订阅选择仅适用于 subscriptions 页面")
        if self.shop is not None and (self.page != "thirdParty" or self.calendar is not None):
            raise ValueError("店铺上下文仅适用于第三方店铺页面，不能同时传递日历上下文")
        if self.calendar is not None and self.page != "thirdParty":
            raise ValueError("日历上下文仅适用于 thirdParty 页面")
        if self.page != "insights" and any(v is not None for v in (
                self.filters, self.details, self.selected, self.observed_as_of, self.bill_workspace)):
            raise ValueError("账单筛选和选择仅适用于 insights 页面")
        if self.bill_workspace and self.bill_workspace.selected and self.selected:
            item = self.bill_workspace.selected
            if item.type != 'transaction' or item.id != self.selected.id:
                raise ValueError('账单选中对象与流水选择不一致')
        if self.observed_as_of is not None and self.observed_as_of.tzinfo is None:
            raise ValueError("页面数据截止时间须带时区")
        if self.details and any(not 1 <= len(key) <= 32 for key in self.details.tx_ids):
            raise ValueError("流水 ID 长度须为 1–32")
        return self
