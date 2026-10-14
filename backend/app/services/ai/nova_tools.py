"""只读业务工具适配：模型参数和服务端身份分别传递。"""
from dataclasses import dataclass

from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.time import utc_iso, utc_now
from ...schemas.bills import BillQuery, BillAnalysis, BillPage
from ...schemas.bill_insights import CashflowScenario, CashflowProjection, SubscriptionInsights
from ..bank.bill_subscriptions import subscription_insights
from ..bank.bill_cashflow import project_cashflow
from ...schemas.nova import StrictModel, BillFilters, PageContext
from ..bank import bill_service, nova_reads
from .calendar_tools import calendar_tools
from .shop_tools import shop_tools
from .card_subscription_tools import card_subscription_tools


@dataclass(frozen=True)
class ToolContext:
    user_id: str
    db: AsyncSession
    page: PageContext


class OverviewArguments(StrictModel):
    pass


class AnalysisArguments(BillFilters):
    """省略的查询字段继承页面已应用条件；显式 null 清除对应筛选。"""


class TransactionsArguments(AnalysisArguments):
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=50)
    tx_ids: list[str] | None = Field(default=None, max_length=10)


class TransactionArguments(StrictModel):
    tx_id: str | None = Field(default=None, min_length=1, max_length=32)


def query_from(arguments: AnalysisArguments, context: ToolContext) -> BillQuery:
    base = context.page.filters.model_dump() if context.page.filters else BillQuery().model_dump()
    base.update(arguments.model_dump(exclude_unset=True, include=set(BillQuery.model_fields)))
    # 改成年度而没给年份时不能继承月份，反之也一样。
    if 'period' in arguments.model_fields_set and 'date' not in arguments.model_fields_set:
        if base['date'] and arguments.period == 'year':
            base['date'] = base['date'][:4]
        elif base['date'] and len(base['date']) == 4:
            base['date'] = None
    return BillQuery(**base)


def envelope(source: str, data: dict, as_of: str | None = None) -> dict:
    return {"source": source, "data_kind": "simulated_bank", "currency": "CNY",
            "amount_unit": "fen", "as_of": as_of or utc_iso(utc_now()), "data": data}


async def overview(arguments: OverviewArguments, context: ToolContext) -> dict:
    return envelope("accounts", await nova_reads.financial_overview(context.db, context.user_id))


async def analysis(arguments: AnalysisArguments, context: ToolContext) -> dict:
    data = BillAnalysis.model_validate(await bill_service.analyze(
        context.db, context.user_id, query_from(arguments, context))).model_dump(mode="json")
    # 精确汇总不截断；异常明细限制长度，明确提示还有多少未返回。
    total = len(data['anomalies'])
    data['anomalies'] = data['anomalies'][:20]
    data['anomalies_total'] = total
    data['anomalies_has_more'] = total > 20
    drivers = data['change_explanation']['drivers']
    data['change_explanation']['drivers_total'] = len(drivers)
    data['change_explanation']['drivers_has_more'] = len(drivers) > 10
    data['change_explanation']['drivers'] = drivers[:10]
    return envelope("bills.analysis", data, data['as_of'])


async def transactions(arguments: TransactionsArguments, context: ToolContext) -> dict:
    query = query_from(arguments, context)
    detail = context.page.details
    ids = arguments.tx_ids
    page = arguments.page
    if detail:
        if 'category' not in arguments.model_fields_set and detail.category:
            query.category = detail.category
        if 'tx_ids' not in arguments.model_fields_set:
            ids = detail.tx_ids
        if 'page' not in arguments.model_fields_set:
            page = detail.page
    if ids and any(not 1 <= len(key) <= 32 for key in ids):
        return {"error": "INVALID_ARGUMENTS", "message": "流水 ID 长度须为 1–32"}
    now = utc_now()
    data = BillPage.model_validate(await bill_service.transactions(
        context.db, context.user_id, query, page=page, page_size=arguments.page_size,
        tx_ids=ids, as_of=now)).model_dump(mode="json")
    data.update(query=bill_service.bounds(query, now)['query'],
                has_more=page * arguments.page_size < data['total'])
    return envelope("bills.transactions", data, utc_iso(now))


async def transaction(arguments: TransactionArguments, context: ToolContext) -> dict:
    key = arguments.tx_id
    if key is None and context.page.selected:
        key = context.page.selected.id
    if key is None:
        return {"error": "MISSING_SELECTION", "message": "请指定流水 ID，或在账单页选择一笔流水"}
    return envelope("bills.transaction", await nova_reads.transaction_detail(context.db, context.user_id, key))


async def subscription_analysis(arguments: AnalysisArguments, context: ToolContext) -> dict:
    data = SubscriptionInsights.model_validate(await subscription_insights(
        context.db, context.user_id, query_from(arguments, context))).model_dump(mode='json')
    total = len(data['items'])
    data['items'] = data['items'][:20]
    data.update(total=total, has_more=total > 20)
    return envelope('bills.subscription_insights', data, data['as_of'])


async def cashflow(arguments: CashflowScenario, context: ToolContext) -> dict:
    request = arguments
    if 'account_id' not in arguments.model_fields_set and context.page.filters:
        request = arguments.model_copy(update={'account_id': context.page.filters.account_id})
    data = CashflowProjection.model_validate(await project_cashflow(context.db, context.user_id, request)).model_dump(mode='json')
    total = len(data['events'])
    data['events'] = data['events'][:20]
    data.update(events_total=total, events_has_more=total > 20)
    return envelope('bills.cashflow', data, data['as_of'])


def business_tools() -> dict:
    entries = [
        ('get_subscription_insights', AnalysisArguments, subscription_analysis,
         '只读分析本人订阅扣费增加、疑似重复、计划与实际差异和关联订阅。沿用页面账期和账户，忽略消费分类；仅供核对，取消需本人到订阅页确认。'),
        ('project_cashflow', CashflowScenario, cashflow,
         '只读计算从现实现在起1至62天储蓄现金流及情景。可减少某消费类别0至100%或假设排除活跃订阅ID，不执行取消或扣款。省略账户沿用页面账户，null为全部储蓄。金额分，计划/工资及消费估计区分。历史高低线不是置信区间。'),
        ("get_financial_overview", OverviewArguments, overview,
         "查询本人账户余额、可用资金和占款；现金总额仅汇总 savings，与首页一致。无需参数。"),
        ("get_bill_analysis", AnalysisArguments, analysis,
         "查询本人月度或年度完整账单统计、分类、趋势、上期比较和异常。金额为分。省略字段继承页面已应用筛选，显式 null 清除筛选。date 月度 YYYY-MM/年度 YYYY；不传默认当前账期。"),
        ("list_transactions", TransactionsArguments, transactions,
         "分页查询本人已完成账单流水。省略字段继承页面筛选及明细下钻条件；清除筛选请显式传 category:null 和 tx_ids:[]。page_size 最大50，金额为分。"),
        ("get_transaction", TransactionArguments, transaction,
         "查询本人一笔已完成流水的详情；省略 tx_id 使用页面选中流水。不能访问其他用户流水。"),
    ]
    tools = {name: {"schema": {"type": "function", "function": {
        "name": name, "description": description, "parameters": model.model_json_schema()}},
        "arguments": model, "execute": function, "requires_identity": True}
        for name, model, function, description in entries}
    tools.update(calendar_tools())
    tools.update(shop_tools())
    tools.update(card_subscription_tools())
    return tools
