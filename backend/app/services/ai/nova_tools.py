"""NOVA 业务工具适配：模型参数和服务端可信上下文分别传递。"""
from dataclasses import dataclass
from typing import Any

from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.time import utc_iso, utc_now
from ...schemas.bills import BillQuery, BillAnalysis, BillPage
from ...schemas.bill_insights import BillTransaction, CashflowScenario, CashflowProjection, SubscriptionInsights
from ..bank.bill_subscriptions import subscription_insights
from ..bank.bill_cashflow import project_cashflow
from ...schemas.nova import StrictModel, BillFilters, PageContext
from ..bank import bill_service, nova_reads, contacts as contact_service
from .calendar_tools import calendar_tools
from .shop_tools import shop_tools
from .card_subscription_tools import card_subscription_tools


@dataclass(frozen=True)
class ToolContext:
    user_id: str
    db: AsyncSession
    page: PageContext
    original_message: str | None = None
    request_id: str | None = None
    session_id: str | None = None
    session_version: int = 0
    focus_task_id: str | None = None
    candidate_id: str | None = None
    bank_api: Any | None = None
    conversation_history: list[dict[str, str]] | None = None
    current_tool_results: list[dict[str, Any]] | None = None
    goal_plan: dict[str, Any] | None = None
    wealth_task_id: str | None = None
    wealth_stage: str | None = None
    reference_results: list[dict[str, Any]] | None = None
    execution_step: bool = False
    serial_focus_only: bool = False
    task_switch_token: str | None = None
    accept_task_switch: bool | None = None
    task_switch_approved: bool = False
    active_entry_type: str | None = None
    active_entry_id: str | None = None


class OverviewArguments(StrictModel):
    pass


class AnalysisArguments(BillFilters):
    """省略的查询字段继承页面已应用条件；显式 null 清除对应筛选。"""


class TransactionsArguments(AnalysisArguments):
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=50)
    tx_ids: list[str] | None = Field(default=None, max_length=10)


class AnomaliesArguments(AnalysisArguments):
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=30)
    review_status: str | None = Field(default=None, pattern=r'^(pending|normal|resolved)$')
    anomaly_id: str | None = Field(default=None, min_length=1, max_length=40)


class RecurringArguments(StrictModel):
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=30, ge=1, le=30)
    account_id: str | None = Field(default=None, min_length=1, max_length=32)
    stream_id: str | None = Field(default=None, min_length=1, max_length=40)


class TransactionArguments(StrictModel):
    tx_id: str | None = Field(default=None, min_length=1, max_length=32)


class ContactListArguments(StrictModel):
    include_history: bool = True
    limit: int = Field(default=50, ge=1, le=50)


class ContactSearchArguments(ContactListArguments):
    query: str = Field(min_length=1, max_length=64, pattern=r"\S")


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
    workspace = context.page.bill_workspace
    if key is None and workspace and workspace.selected and workspace.selected.type == 'transaction':
        key = workspace.selected.id
    if key is None:
        return {"error": "MISSING_SELECTION", "message": "请指定流水 ID，或在账单页选择一笔流水"}
    data = BillTransaction.model_validate(await nova_reads.transaction_detail(context.db, context.user_id, key))
    return envelope("bills.transaction", data.model_dump(mode='json'))


async def anomalies(arguments: AnomaliesArguments, context: ToolContext) -> dict:
    report = BillAnalysis.model_validate(await bill_service.analyze(
        context.db, context.user_id, query_from(arguments, context))).model_dump(mode='json')
    rows = [a for a in report['anomalies'] if arguments.review_status is None or a['review_status'] == arguments.review_status]
    key = arguments.anomaly_id
    workspace = context.page.bill_workspace
    explicit_scope = arguments.model_fields_set & (set(BillQuery.model_fields) | {'review_status'})
    if 'anomaly_id' not in arguments.model_fields_set and not explicit_scope and workspace and workspace.selected and workspace.selected.type == 'anomaly':
        key = workspace.selected.id
    if key:
        from ..bank.transfer_service import BankError
        rows = [row for row in rows if row['anomaly_id'] == key]
        if not rows:
            raise BankError('所选异常不在当前查询范围，请重新核对筛选与状态', 'ANOMALY_NOT_FOUND', 404)
    start = (arguments.page-1)*arguments.page_size
    return envelope('bills.anomalies', dict(query=report['query'], as_of=report['as_of'],
        items=rows[start:start+arguments.page_size], total=len(rows), page=arguments.page,
        has_more=start+arguments.page_size<len(rows)), report['as_of'])


async def list_contacts(arguments: ContactListArguments, context: ToolContext) -> dict:
    data = await contact_service.read_contact_shortcuts(
        context.user_id, include_history=arguments.include_history, limit=arguments.limit)
    return envelope("contacts", data)


async def search_contacts(arguments: ContactSearchArguments, context: ToolContext) -> dict:
    data = await contact_service.read_contact_shortcuts(
        context.user_id, query=arguments.query, include_history=arguments.include_history,
        limit=arguments.limit)
    return envelope("contacts.search", data)


async def subscription_analysis(arguments: AnalysisArguments, context: ToolContext) -> dict:
    now = utc_now()
    query = query_from(arguments, context).model_copy(update={'category': None})
    data = SubscriptionInsights.model_validate(await subscription_insights(
        context.db, context.user_id, query, now=now)).model_dump(mode='json')
    data['query'] = bill_service.bounds(query, now)['query']
    total = len(data['items'])
    data['items'] = data['items'][:20]
    data.update(total=total, has_more=total > 20)
    return envelope('bills.subscription_insights', data, data['as_of'])


async def cashflow(arguments: CashflowScenario, context: ToolContext) -> dict:
    request = arguments
    if 'account_id' not in arguments.model_fields_set:
        workspace = context.page.bill_workspace
        if workspace and workspace.section == 'forecast' and workspace.subsection == 'cashflow':
            request = arguments.model_copy(update={'account_id': workspace.account_id})
        elif context.page.filters:
            request = arguments.model_copy(update={'account_id': context.page.filters.account_id})
    data = CashflowProjection.model_validate(await project_cashflow(context.db, context.user_id, request)).model_dump(mode='json')
    total = len(data['events'])
    data['events'] = data['events'][:20]
    data.update(events_total=total, events_has_more=total > 20)
    return envelope('bills.cashflow', data, data['as_of'])


async def recurring_records(arguments: RecurringArguments, context: ToolContext) -> dict:
    from ..bank.bill_recurring import list_recurring
    data = await list_recurring(context.db, context.user_id)
    key = arguments.stream_id
    workspace = context.page.bill_workspace
    if 'stream_id' not in arguments.model_fields_set and 'account_id' not in arguments.model_fields_set and workspace and workspace.selected and workspace.selected.type == 'recurring':
        key = workspace.selected.id
    if key:
        from ..bank.transfer_service import BankError
        data['items'] = [row for row in data['items'] if row['stream_id'] == key]
        if not data['items']:
            raise BankError('周期记录不存在或无权访问', 'RECURRING_NOT_FOUND', 404)
    account_id = arguments.account_id
    if 'account_id' not in arguments.model_fields_set and context.page.filters:
        account_id = context.page.filters.account_id
    if account_id:
        from ..bank.bill_foundation import account_ids
        from ..bank.transfer_service import BankError
        if account_id not in await account_ids(context.db, context.user_id):
            raise BankError('账户不存在', 'ACCOUNT_NOT_FOUND', 404)
        data['items'] = [r for r in data['items'] if r['account_id'] == account_id]
    total = len(data['items'])
    start = (arguments.page-1)*arguments.page_size
    data['items'] = data['items'][start:start+arguments.page_size]
    for item in data['items']:
        item['evidence_total'] = len(item['evidence_tx_ids'])
        item['evidence_tx_ids'] = item['evidence_tx_ids'][-5:]
        for field in ('last_observed_at', 'next_expected_at','anchor_at'):
            if item[field]:
                item[field] = bill_service.aware(item[field]).isoformat()
    data.update(total=total, page=arguments.page, has_more=start+arguments.page_size<total,
                query={'account_id':account_id})
    return envelope('bills.recurring', data)


class BudgetArguments(StrictModel):
    month: str | None = Field(default=None,pattern=r'^\d{4}-\d{2}$')
    budget_id: str | None = Field(default=None,min_length=1,max_length=40)


async def budget_status(arguments:BudgetArguments,context:ToolContext)->dict:
    from ..bank.bill_budgets import summary
    from datetime import datetime
    workspace = context.page.bill_workspace
    key = arguments.budget_id
    if 'budget_id' not in arguments.model_fields_set and arguments.month is None and workspace and workspace.selected and workspace.selected.type == 'budget':
        key = workspace.selected.id
    reference = None
    if key:
        from ..bank.nova_bills import budget_reference
        reference = await budget_reference(context.db,context.user_id,key)
    month=arguments.month or (reference['month'] if reference else None) or (workspace.budget_month if workspace and workspace.section=='budgets' else None) or (context.page.filters.date if context.page.filters and context.page.filters.period=='month' else None) or utc_now().astimezone(bill_service.TZ).strftime('%Y-%m')
    data=await summary(context.db,context.user_id,month)
    if key:
        data['items'] = [row for row in data['items'] if row['budget_id'] == key]
    data['as_of']=data['as_of'].isoformat()
    return envelope('bills.budgets',data)


async def forecast_reviews(arguments:OverviewArguments,context:ToolContext)->dict:
    from ..bank.bill_forecasts import comparisons
    data=await comparisons(context.db,context.user_id)
    data['as_of']=data['as_of'].isoformat()
    for item in data['items']:
        for field in ('start_at','end_at'):
            if item.get(field):item[field]=item[field].isoformat()
    return envelope('bills.forecasts',data)


def business_tools() -> dict:
    entries = [
        ('list_bill_anomalies', AnomaliesArguments, anomalies,
         '分页查询本人账单异常及核对状态、备注、版本与证据指纹。继承页面已应用账期、账户、分类及标签。可筛选pending/normal/resolved。准备核对修改前必须查询；异常只作核对提示，不认定欺诈。'),
        ('get_budget_status', BudgetArguments, budget_status,'只读查询本人月度总预算及分类预算、净消费和预警。省略月份沿用工作台预算月份；省略budget_id沿用页面所选预算，显式null查看全部。明确month优先于页面选择。标签不改变预算范围。'),
        ('list_forecast_reviews',OverviewArguments,forecast_reviews,'只读查询本人保存的基准预测及实际收支净变化偏差。未结束不计算最终误差，不能用当前余额冒充过去期末余额。'),
        ('list_recurring_records', RecurringArguments, recurring_records,
         '分页只读查询本人周期收入、支出及订阅、历史证据、有效状态和版本。省略stream_id沿用页面所选周期，显式null查看全部。省略账户继承页面账户，null为本人全部账户。估计不是银行承诺；启用设置不会停止扣费。'),
        ('get_subscription_insights', AnalysisArguments, subscription_analysis,
         '只读分析本人订阅扣费增加、疑似重复、计划与实际差异和关联订阅。沿用页面账期和账户，忽略消费分类；仅供核对，取消需本人到订阅页确认。'),
        ('project_cashflow', CashflowScenario, cashflow,
         '只读计算从现实现在起1至62天储蓄现金流及情景。可增加临时收支假设one_off_events（带时区时间、非零整数分），减少某消费类别0至100%或假设排除活跃订阅ID，不执行取消或扣款。省略账户沿用页面账户，null为全部储蓄。金额分，计划/工资及消费估计区分。历史高低线不是置信区间。'),
        ("get_financial_overview", OverviewArguments, overview,
         "查询本人现金和总资产。totals 仅是普通 savings 现金小计；首页/理财页总资产必须使用 asset_summary，按其授权范围、快照及估值完整性解释。总资产=现金+持仓+赎回应收，冻结不重复加；总资产不是可支付现金，null 不是零。无需参数。"),
        ("get_bill_analysis", AnalysisArguments, analysis,
         "查询本人月度或年度完整账单统计、分类、趋势、上期比较和异常。金额为分。省略字段继承页面已应用筛选，显式 null 清除筛选。date 月度 YYYY-MM/年度 YYYY；不传默认当前账期。"),
        ("list_transactions", TransactionsArguments, transactions,
         "分页查询本人已完成账单流水。省略字段继承页面筛选及明细下钻条件；清除筛选请显式传 category:null 和 tx_ids:[]。page_size 最大50，金额为分。"),
        ("get_transaction", TransactionArguments, transaction,
         "查询本人一笔已完成流水的详情；省略 tx_id 使用页面选中流水。不能访问其他用户流水。"),
        ("list_contacts", ContactListArguments, list_contacts,
         "只读列出本人已保存联系人，以及可选的历史转账人。只返回用户已提供过的号码类型的脱敏信息；不返回完整手机号、卡号或其他用户的联系人。"),
        ("search_contacts", ContactSearchArguments, search_contacts,
         "只读按姓名、备注名或本人已知的手机号/银行卡号查询联系人和可选历史转账人。未知号码不能用于匹配；结果仅有脱敏号码。"),
    ]
    tools = {name: {"schema": {"type": "function", "function": {
        "name": name, "description": description, "parameters": model.model_json_schema()}},
        "arguments": model, "execute": function, "requires_identity": True}
        for name, model, function, description in entries}
    tools.update(calendar_tools())
    tools.update(shop_tools())
    tools.update(card_subscription_tools())
    from .bill_tools import bill_tools
    tools.update(bill_tools())
    return tools
