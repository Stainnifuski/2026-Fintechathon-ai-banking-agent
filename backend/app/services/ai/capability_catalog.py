"""Public, read-only description of the NOVA tools available in this deployment."""

from copy import deepcopy


# A capability is exposed only while its backing tool is registered.
_CAPABILITIES = (
    {
        "domain": "accounts", "name": "账户与资产",
        "queries": [
            {"tool": "get_financial_overview", "description": "储蓄账户余额、可用资金、冻结资金及总资产概览"},
        ],
        "operations": [],
    },
    {
        "domain": "bills", "name": "账单与流水",
        "queries": [
            {"tool": "get_bill_analysis", "description": "月度和年度收支、分类、趋势、上期比较及待核对提示"},
            {"tool": "list_transactions", "description": "已完成流水列表及筛选结果"},
            {"tool": "get_transaction", "description": "单笔已完成流水详情"},
            {"tool": "get_bill_catalog", "description": "本人商户、标签、分类规则及商户整理历史"},
            {"tool": "list_bill_anomalies", "description": "账单异常及核对状态"},
            {"tool": "preview_bill_merchant_change", "description": "商户归并或拆分的只读范围预览"},
        ],
        "operations": [{"tool": "prepare_bill_change", "description": "生成待本人确认的账单修改任务卡"}],
    },
    {
        "domain": "planning", "name": "预算与现金流",
        "queries": [
            {"tool": "get_budget_status", "description": "总预算、分类预算、已用金额及剩余额度"},
            {"tool": "list_recurring_records", "description": "周期收入、支出及订阅计划"},
            {"tool": "list_forecast_reviews", "description": "已保存预测与实际收支的复盘"},
            {"tool": "project_cashflow", "description": "未来储蓄现金流和指定假设下的情景估计"},
        ],
        "operations": [],
    },
    {
        "domain": "contacts", "name": "联系人",
        "queries": [
            {"tool": "list_contacts", "description": "已保存联系人及历史转账人列表"},
            {"tool": "search_contacts", "description": "按姓名、备注名或已知号码搜索联系人"},
        ],
        "operations": [],
    },
    {
        "domain": "transfer", "name": "转账",
        "queries": [
            {"tool": "list_transactions", "description": "查询已完成转出流水和历史转账记录"},
        ],
        "operations": [
            {"tool": "manage_transfer_task", "description": "向别人付款：新建、补充、修改或撤销单张立即或定时人民币转账草稿"},
        ],
    },
    {
        "domain": "collection", "name": "收款",
        "queries": [
            {"tool": "manage_payment_requests", "description": "查看本人发出和收到的现有收款申请及其状态"},
        ],
        "operations": [
            {"tool": "manage_collection_request", "description": "请求别人向本人付款：新建、补填、修改或撤销收款草稿，可选择每人金额或 AA 均分总额"},
            {"tool": "manage_payment_requests", "description": "筛选现有收款申请，在任务卡中批量取消本人发出的待处理申请，或支付/拒绝别人发来的申请"},
        ],
    },
    {
        "domain": "cards", "name": "银行卡片",
        "queries": [
            {"tool": "list_cards", "description": "卡片列表、状态、额度、限额及渠道设置"},
            {"tool": "get_card", "description": "单张卡片详情"},
            {"tool": "list_card_transactions", "description": "单张卡最近用卡授权记录及被拒绝原因"},
        ],
        "operations": [
            {"tool": "preview_card_operation", "description": "打开、更新或取消本人未完成的卡片操作任务卡，支持锁定、挂失、补卡、注销虚拟卡及调整限额或渠道等"},
        ],
    },
    {
        "domain": "subscriptions", "name": "订阅",
        "queries": [
            {"tool": "list_subscriptions", "description": "订阅列表、商户、预计周期费用及状态"},
            {"tool": "get_subscription", "description": "单项订阅的金额、周期、扣费账户和计划日期"},
            {"tool": "get_subscription_insights", "description": "订阅计划金额与实际扣费的核对"},
        ],
        "operations": [
            {"tool": "manage_subscription_task", "description": "按订阅名称打开取消订阅任务卡，或关闭当前会话中未完成的订阅任务"},
        ],
    },
    {
        "domain": "calendar", "name": "日历",
        "queries": [
            {"tool": "list_calendar_events", "description": "指定日期或范围内的日程"},
            {"tool": "search_calendar_events", "description": "按名称或地点搜索日程"},
            {"tool": "get_calendar_event", "description": "单项日程及重复事件的实际发生时间"},
        ],
        "operations": [
            {"tool": "manipulate_calender_event", "description": "筛选日程并打开添加、批量修改或批量删除的待确认任务卡"},
        ],
    },
    {
        "domain": "shops", "name": "模拟鲜花店与蛋糕店",
        "queries": [
            {"tool": "list_shop_products", "description": "两家模拟店铺的商品目录和价格"},
            {"tool": "get_shop_product", "description": "商品详情及报价"},
            {"tool": "list_shop_orders", "description": "模拟店铺订单列表与付款状态"},
            {"tool": "get_shop_order", "description": "单笔模拟店铺订单详情"},
        ],
        "operations": [
            {"tool": "prepare_shop_purchase", "description": "选择商品并下单，或打开待填写订单表单"},
        ],
    },
    {
        "domain": "wealth", "name": "理财",
        "queries": [
            {"tool": "manage_wealth_positions", "description": "本人持仓查询与调整"},
            {"tool": "show_wealth_returns", "description": "本人整体理财收益"},
        ],
        "operations": [
            {"tool": "assess_wealth_risk", "description": "本人风险评估或重新评估"},
            {"tool": "plan_wealth", "description": "理财规划、目标推荐、产品查看与申购草稿"},
            {"tool": "manage_wealth_positions", "description": "持仓查询、调整及赎回草稿"},
        ],
    },
    {
        "domain": "time", "name": "当前时间",
        "queries": [
            {"tool": "get_current_time", "description": "现实当前时间及指定 IANA 时区的本地时间"},
        ],
        "operations": [],
    },
)


def describe_capabilities(registered_tools: set[str]) -> dict:
    businesses = []
    for item in _CAPABILITIES:
        queries = [deepcopy(entry) for entry in item["queries"]
                   if entry["tool"] in registered_tools]
        operations = [deepcopy(entry) for entry in item["operations"]
                      if entry["tool"] in registered_tools]
        if queries or operations:
            businesses.append({"domain": item["domain"], "name": item["name"],
                               "queries": queries, "operations": operations})
    return {"source": "bank_agent.capabilities", "data_kind": "service_catalog",
            "catalog_version": 2, "businesses": businesses}


def visible_business_tools(catalog: dict) -> set[str]:
    """Only user-facing business calls may appear in streamed progress."""
    return {entry['tool'] for business in catalog['businesses']
            for group in ('queries', 'operations') for entry in business[group]}
