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
        ],
        "operations": [],
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
            {"tool": "manage_transfer_task", "description": "向别人付款：新建、补充、修改或撤销单张即时人民币转账草稿"},
        ],
    },
    {
        "domain": "collection", "name": "收款",
        "queries": [
            {"tool": "inspect_collection_request", "description": "查询当前对话的收款草稿与已保存联系人，核对收款对象"},
        ],
        "operations": [
            {"tool": "manage_collection_request", "description": "请求别人向本人付款：新建、补填、修改或撤销收款草稿，可选择每人金额或 AA 均分总额"},
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
            {"tool": "preview_card_operation", "description": "为申请虚拟卡、锁定或解锁、挂失或补卡、注销虚拟卡、调整限额或渠道生成表单或预览"},
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
            {"tool": "request_subscription_cancellation", "description": "打开取消订阅任务卡并选择要取消的订阅"},
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
            {"tool": "create_calendar_event", "description": "创建日程或打开预填日程表单"},
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
            {"tool": "manage_wealth_request", "description": "资产、持仓、整体收益、产品详情与比较"},
        ],
        "operations": [
            {"tool": "manage_wealth_request", "description": "目标推荐、配置规划、风险评估任务、申购或赎回草稿及未完成任务的修改与恢复"},
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
