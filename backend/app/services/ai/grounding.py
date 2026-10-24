"""回答事实边界：模型仅选择证据顺序，业务正文由服务端生成。

证据绑定整份工具结果的内容指纹；对象、范围、币种和时间随正文一起展示。
不以数字集合匹配或模型自报置信度判断事实，不重试任何业务工具。
"""
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

VERSION = 'grounding-v1'
logger = logging.getLogger(__name__)
MAX_ROWS = 10

TOOL_LABELS = {
    'get_current_time': '当前时间', 'get_financial_overview': '账户概览',
    'get_bill_analysis': '账单分析', 'list_transactions': '流水查询', 'get_transaction': '流水详情',
    'list_contacts': '联系人查询', 'search_contacts': '联系人查询',
    'list_cards': '卡片查询', 'get_card': '卡片详情', 'list_card_transactions': '用卡记录',
    'preview_card_operation': '卡片操作预览', 'request_subscription_cancellation': '取消订阅',
    'list_subscriptions': '订阅查询', 'get_subscription': '订阅详情',
    'create_calendar_event': '日程', 'prepare_shop_purchase': '店铺订单', 'list_calendar_events': '日历查询', 'search_calendar_events': '日历搜索', 'get_calendar_event': '活动详情',
    'list_shop_products': '商品查询', 'get_shop_product': '商品报价',
    'list_shop_orders': '订单查询', 'get_shop_order': '订单详情',
    'get_subscription_insights': '订阅扣费核对', 'project_cashflow': '现金流情景',
    'list_recurring_records': '周期收支查询', 'get_budget_status': '预算查询',
    'list_forecast_reviews': '预测复盘', 'manage_transfer_request': '转账任务',
    'manage_collection_request': '收款草稿',
    'manage_wealth_request': '理财任务',
}


class AnswerSelection(BaseModel):
    """没有自由正文，不能通过额外字段夹带事实或授权。"""
    model_config = ConfigDict(extra='forbid', strict=True)
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    clarification: Literal['none', 'selection', 'period', 'request'] = 'none'


def literal(value: object, limit: int = 300) -> str:
    """用户文本仅作为引用数据展示；禁止生成 Markdown 链接、图片或换行指令。"""
    from ..bank.nova_conversations import redact
    text = re.sub(r'[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]', ' ', redact(str(value)))
    text = text[:limit] + ('…' if len(text) > limit else '')
    text = re.sub(r'([\\`*_{}\[\]()<>#!|])', r'\\\1', text)
    return f'“{text}”'


def money(value: object, currency: str = 'CNY') -> str:
    """金额只接受整数分；未知不转成零，不使用二进制浮点换算。"""
    if value is None:
        return '尚未核实'
    if type(value) is not int:
        raise ValueError('INVALID_MONEY_EVIDENCE')
    if currency not in {'CNY', 'USD', 'EUR', 'HKD', 'JPY', 'GBP'}:
        return '币种尚未核实'
    return f'{Decimal(value) / 100:,.2f} {currency}'


def amounts(data: dict, fields: dict[str, str], currency: str = 'CNY') -> str:
    return '；'.join(f'{label}：{money(data[key], currency)}' for key, label in fields.items() if key in data)


def page_notice(data: dict, shown: int) -> str:
    total = data.get('total')
    text = f'本次查询范围内共 {total} 项。' if type(total) is int else '以下仅为本次返回记录。'
    if total == 0:
        text += '本次未找到匹配记录，不代表其他范围也没有。'
    if data.get('has_more') or len(data.get('items', [])) > shown:
        text += f'当前仅展示本页前 {shown} 项，可继续查询。'
    return text


def scope_text(value: dict) -> str:
    """显示业务范围，不把接口字段或内部标识带入用户正文。"""
    labels = {'date': '账期', 'start': '开始时间', 'end_exclusive': '结束时间（不含）',
              'keyword': '关键词', 'last_four': '卡片尾号', 'days': '预测天数',
              'min_price_fen': '最低单价', 'max_price_fen': '最高单价'}
    parts = []
    for key, label in labels.items():
        if value.get(key) is not None:
            parts.append(label + ' ' + (money(value[key]) if key.endswith('_fen') else literal(value[key])))
    if value.get('period') in {'month', 'year'}:
        parts.append('月度' if value['period'] == 'month' else '年度')
    if value.get('category'):
        category = {'dining': '餐饮', 'shopping': '购物', 'transport': '交通', 'entertainment': '娱乐',
                    'subscription': '订阅', 'education': '教育', 'other': '其他'}.get(value['category'])
        parts.append('分类 ' + (category or literal(value['category'])))
    for key, label in (('tag_id', '已应用标签筛选'), ('tx_ids', '已应用流水选择')):
        if value.get(key):
            parts.append(label)
    if value.get('status'):
        parts.append('状态筛选 ' + literal(value['status']))
    for reduction in value.get('reductions', []):
        parts.append('假设减少分类 ' + literal(reduction['category']) + f' {reduction["percent"]}%')
    if value.get('excluded_subscription_ids'):
        parts.append(f'假设排除 {len(value["excluded_subscription_ids"])} 项订阅（尚未取消）')
    for event in value.get('one_off_events', [])[:MAX_ROWS]:
        parts.append(f'临时假设 {literal(event["label"])}，{literal(event["at"])}，{money(event["amount_fen"])}')
    return '；'.join(parts)


def transaction_text(row: dict) -> str:
    kinds = {'purchase': '消费', 'subscription': '订阅扣费', 'refund': '退款',
             'salary': '工资收入', 'transfer_in': '转入', 'transfer_out': '转出'}
    return (f'{literal(row.get("timestamp", "时间未提供"))}，'
            f'交易对方 {literal(row.get("merchant_name") or row.get("counterparty") or "未提供")}，'
            f'{kinds.get(row.get("type"), "交易")}金额 {money(row.get("amount_fen"))}；'
            '此工具仅返回本人已完成流水。')


def card_text(row: dict) -> str:
    status = {'ACTIVE': '正常', 'LOCKED': '已锁定', 'LOST': '已挂失', 'CANCELLED': '已注销'}
    suffix = re.sub(r'\D', '', str(row.get('card_number', '')))[-4:]
    title = f'卡片尾号 {suffix}' if len(suffix) == 4 else '本次返回卡片'
    text = (f'{title}，状态：{status.get(row.get("status"), "尚未核实")}；' + amounts(row, {
        'credit_limit_fen': '信用额度', 'available_credit_fen': '可用信用额度',
        'single_txn_limit_fen': '单笔交易限额', 'daily_txn_limit_fen': '单日交易限额',
        'credit_limit_cap_fen': '银行信用额度上限', 'used_credit_fen': '已用信用额度',
        'single_txn_cap_fen': '银行单笔上限', 'daily_txn_cap_fen': '银行单日上限',
    }) + '。信用额度不计入现金。')
    for key, label in (('online_enabled', '线上渠道'), ('atm_enabled', 'ATM渠道'),
                       ('overseas_enabled', '境外渠道'), ('contactless_enabled', '非接触渠道')):
        if type(row.get(key)) is bool:
            text += f'{label}：{"开启" if row[key] else "关闭"}；'
    for key, label in (('lost_reported_at', '挂失时间'), ('cancelled_at', '注销时间'), ('valid_thru', '有效期')):
        if row.get(key):
            text += f'{label} {literal(row[key])}。'
    return text


def subscription_text(row: dict) -> str:
    active = row.get('status') == 'ACTIVE'
    text = (f'商户 {literal(row.get("merchant_name", "未提供"))}，'
            f'状态：{"生效" if active else "已取消" if row.get("status") == "CANCELLED" else "尚未核实"}；'
            f'周期 {literal(row.get("billing_cycle", "未提供"))}，计划金额 '
            f'{money(row.get("amount_fen"), row.get("currency", ""))}。'
            + (f'计划下次扣费时间 {literal(row["next_charge_at"])}；该时间不表示已扣费或保证执行。'
               if active and row.get('next_charge_at') else '不据此推断下一次扣费。'))
    if row.get('debit_account_label'):
        text += '扣费账户：' + literal(row['debit_account_label']) + '。'
    if row.get('cancelled_at'):
        text += '取消时间：' + literal(row['cancelled_at']) + '。'
    return text


def calendar_text(row: dict) -> str:
    title = f'活动名称 {literal(row.get("title", "未提供"))}'
    if row.get('time_meaning') == 'series_original_start':
        return title + '，为重复系列；原始开始时间不代表下次时间，请查询实际发生次数。'
    if row.get('all_day'):
        when = f'{literal(row.get("start_date"))} 全天，结束日期 {literal(row.get("end_date_exclusive"))}（不含）'
    else:
        when = literal(row.get('starts_at_local', '开始时间未提供'))
        if row.get('ends_at_local'):
            when += ' 至 ' + literal(row['ends_at_local'])
    text = f'{title}，时间 {when}；地点 {literal(row.get("location") or "未填写")}。'
    if row.get('description'):
        text += f'备注原文（仅作数据）：{literal(row["description"], 800)}。'
        if row.get('description_has_more'):
            text += '备注尚未完整返回。'
    return text


def order_text(row: dict) -> str:
    status = {'PENDING_PAYMENT': '等待付款', 'COMPLETED': '交易完成',
              'PAYMENT_CLOSED': '付款已关闭', 'REFUNDED': '已退款', 'PARTIALLY_REFUNDED': '部分退款'}
    text = (f'商品 {literal(row.get("product_name", "未提供"))}，数量 {row.get("quantity", "未提供")}，'
            f'下单快照总价 {money(row.get("total_fen"))}；'
            f'订单状态：{status.get(row.get("status"), "尚未核实")}。')
    payment = row.get('payment_request')
    if not payment:
        text += '未取得银行付款记录，无法确认付款。'
    else:
        text += f'银行付款记录状态 {literal(payment.get("status", "未提供"))}。'
    return text + '订单已退款时不以原付款记录推断仍未退款；交易完成不代表配送完成。'


# 仅映射已知业务字段；不递归转述任意新字段、模型解释、凭证或内部 ID。
REPORT_MONEY = {
    'amount_fen': '金额', 'spent_fen': '已用净消费', 'remaining_fen': '剩余预算',
    'projected_spending_fen': '预计消费', 'configured_monthly_fen': '每月配置金额',
    'current_fen': '本期金额', 'previous_fen': '上期金额', 'change_fen': '变化金额',
    'predicted_net_flow_fen': '预测收支净变化', 'actual_net_flow_fen': '实际收支净变化',
    'error_fen': '已结束预测的误差',
}


def report_rows(data: dict) -> list[str]:
    lines = []
    for index, row in enumerate(data.get('items', [])[:MAX_ROWS], 1):
        label = row.get('merchant') or row.get('name') or row.get('label') or row.get('category')
        text = f'记录 {index}' + (f'，名称/分类 {literal(label)}' if label else '')
        text += '；' + amounts(row, REPORT_MONEY)
        for key, label in (('month', '月份'), ('start_at', '开始时间'), ('end_at', '结束时间'),
                           ('next_expected_at', '预计下次时间')):
            if row.get(key):
                text += f'；{label} {literal(row[key])}'
        if row.get('scope_changed'):
            text += '；账户范围已变化，不能与原预测直接比较'
        if row.get('complete') is False:
            text += '；预测尚未结束，不计算最终误差'
        for key, labels in (
            ('kind', {'income': '收入', 'expense': '支出'}),
            ('source', {'subscription': '银行订阅计划', 'manual': '手动计划', 'history': '历史估计'}),
            ('frequency', {'WEEKLY': '每周', 'MONTHLY': '每月', 'QUARTERLY': '每季度', 'YEARLY': '每年'}),
            ('status', {'ACTIVE': '生效', 'CANCELLED': '已取消', 'ok': '正常', 'warning': '接近预算',
                        'exceeded': '已超预算', 'projected_exceeded': '预计超预算', 'disabled': '未启用'}),
        ):
            if row.get(key) in labels:
                text += '；' + labels[row[key]]
        if 'enabled' in row:
            text += '；' + ('纳入预测' if row['enabled'] else '未纳入预测')
        lines.append(text + '。')
    return [page_notice(data, len(lines)), *lines]


def body(name: str, result: dict) -> list[str]:
    """按工具语义生成不可由模型改写的证据块。"""
    d = result.get('data', {})
    if name not in {'get_current_time', 'manage_transfer_request', 'manage_collection_request', 'manage_wealth_request'} and not isinstance(d, dict):
        raise ValueError('INVALID_EVIDENCE_SHAPE')
    if name == 'get_current_time':
        return [f'当前时间 {literal(result["local_time"])}，时区 {literal(result["timezone"])}。']
    if name == 'get_financial_overview':
        lines = ['普通储蓄账户现金小计：' + amounts(d.get('totals', {}), {
            'balance_fen': '余额', 'available_fen': '可用现金', 'held_fen': '冻结金额'}) + '。']
        summary = d.get('asset_summary') or {}
        if summary:
            lines.append('当前授权资产范围：' + amounts(summary, {
                'cash_balance_fen': '现金余额', 'available_cash_fen': '可用现金',
                'positions_market_value_fen': '持仓市值', 'redemption_receivable_fen': '赎回应收',
                'total_assets_fen': '总资产'}) + '。')
            if summary.get('as_of'):
                lines.append('资产快照时间：' + literal(summary['as_of']) + '。')
            if not summary.get('valuation_complete', False):
                lines.append('估值尚不完整；未知总资产或市值不能当作零。')
        for row in d.get('accounts', [])[:MAX_ROWS]:
            suffix = re.sub(r'\D', '', str(row.get('card_number_masked', '')))[-4:]
            kind = '储蓄账户' if row.get('account_type') == 'savings' else '信用账户（不计现金）' if row.get('account_type') == 'credit' else '账户'
            lines.append(f'{kind}尾号 {suffix or "未提供"}；' + amounts(row, {
                'balance_fen': '余额', 'available_fen': '可用金额', 'held_fen': '冻结金额'}) + '。')
        return lines + ['现金小计与授权总资产范围可能不同；总资产不等于可支付现金，信用额度不是现金。']
    if name == 'get_bill_analysis':
        lines = ['本期统计：' + amounts(d.get('totals', {}), {
            'inflow_fen': '流入', 'outflow_fen': '流出', 'net_inflow_fen': '净流入',
            'consumption_fen': '消费', 'refund_fen': '退款', 'net_consumption_fen': '净消费',
            'transfer_in_fen': '转入', 'transfer_out_fen': '转出'}) + '。']
        if 'change_fen' in d:
            lines.append('与上期消费比较：' + money(d['change_fen']) + '。')
        for row in d.get('categories', [])[:MAX_ROWS]:
            lines.append(f'分类 {literal(row.get("label", row.get("category", "未提供")))}；' + amounts(row, {
                'amount_fen': '消费', 'net_amount_fen': '净消费', 'change_fen': '较上期变化'}) + '。')
        for row in d.get('change_explanation', {}).get('drivers', [])[:3]:
            lines.append(f'商户 {literal(row.get("merchant", "未提供"))}；' + amounts(row, {
                'current_fen': '本期消费', 'previous_fen': '上期消费', 'change_fen': '变化金额'}) + '。')
        for row in d.get('trend', [])[:12]:
            lines.append(f'趋势日期 {literal(row["date"])}，消费 {money(row["consumption_fen"])}。')
        if len(d.get('trend', [])) > 12:
            lines.append('趋势仅展示前12项，可继续查询。')
        for row in d.get('anomalies', [])[:3]:
            lines.append(f'待核对提示：{literal(row.get("title", "未提供"))}；原因 {literal(row.get("reason", "未提供"))}。')
        if 'anomalies_total' in d:
            lines.append(f'待核对提示共 {d["anomalies_total"]} 项，最多展示3项。')
        if d.get('partial'):
            lines.append('本期尚未结束，当前仅为截至查询时间的数据。')
        lines.append('分类和异常明细可能截断；异常提示需核对，不认定欺诈或永久一次性支出。')
        return lines
    if name in {'list_transactions', 'get_transaction'}:
        rows = d.get('items', []) if name == 'list_transactions' else [d]
        return ([page_notice(d, min(len(rows), MAX_ROWS))] if name == 'list_transactions' else []) + [
            transaction_text(row) for row in rows[:MAX_ROWS]]
    if name in {'list_contacts', 'search_contacts'}:
        rows = d.get('items', [])[:MAX_ROWS]
        return [page_notice(d, len(rows)), *[
            f'{"历史转账人" if row.get("source") == "transfer_history" else "已保存联系人"}，'
            f'姓名 {literal(row.get("name", "未提供"))}；备注名 {literal(row.get("nickname") or "未填写")}；'
            f'已知手机号 {literal(row.get("phone_masked") or "未提供")}；'
            f'已知卡号 {literal(row.get("card_masked") or "未提供")}。' for row in rows]]
    if name in {'list_cards', 'get_card'}:
        rows = d.get('items', []) if name == 'list_cards' else [d]
        return ([page_notice(d, min(len(rows), MAX_ROWS))] if name == 'list_cards' else []) + [card_text(row) for row in rows[:MAX_ROWS]]
    if name == 'preview_card_operation':
        if d.get('executed') is not False:
            raise ValueError('PREVIEW_STATE_NOT_VERIFIED')
        if d.get('form_opened'):
            lines = [f'已在下方打开{literal(d.get("action_label", "卡片操作"))}表单，尚未生成预览，也未执行。']
            if d.get('bank_message'):
                lines.append(f'银行提示：{literal(d["bank_message"])}；请在表单中调整后再生成确认信息。')
            if d.get('missing_fields'):
                lines.append('请在表单中补充：' + '、'.join(literal(item) for item in d['missing_fields']) + '。')
            lines.append('生成确认信息后，由本人在任务卡中确认；需要验证码的操作还须本人输入。')
            return lines
        lines = ['已生成卡片操作预览，尚未执行；请在下方卡片任务卡中核对并由本人确认。']
        if d.get('requires_mfa'):
            lines.append('此操作还需要本人完成验证码强验证。')
        statuses = {'ACTIVE': '正常', 'LOCKED': '已锁定', 'LOST': '已挂失', 'CANCELLED': '已注销'}
        for change in d.get('changes', [])[:MAX_ROWS]:
            fmt = money if change.get('unit') == 'fen' else (
                (lambda value: literal(statuses.get(value, value))) if change.get('field') == 'status'
                # 渠道开关等布尔字段按开启/关闭展示。
                else (lambda value: literal({True: '开启', False: '关闭'}.get(value, value)))
                if isinstance(change.get('before'), bool) or isinstance(change.get('after'), bool) else literal)
            lines.append(f'变更字段 {literal(change.get("label", "未提供"))}：'
                         f'{fmt(change.get("before"))} → {fmt(change.get("after"))}。')
        return lines
    if name == 'list_card_transactions':
        suffix = re.sub(r'\D', '', str(d.get('card_number') or ''))[-4:]
        title = f'卡片尾号 {suffix}' if len(suffix) == 4 else '本卡'
        kind = {True: '已批准的', False: '被拒绝的'}.get((d.get('filters') or {}).get('approved'), '')
        lines = [f'{title}最近20笔用卡授权中，{kind}记录共 {int(d.get("total", 0))} 笔。']
        for row in d.get('items', [])[:MAX_ROWS]:
            lines.append(f'{literal(row.get("authorized_at", "时间未提供"))}，商户 {literal(row.get("merchant") or "未提供")}，'
                         f'金额 {money(row.get("amount_fen"))}，' +
                         ('已批准。' if row.get('approved') is True else f'被拒绝：{literal(row.get("message") or "原因未提供")}。'))
        return lines
    if name == 'request_subscription_cancellation':
        if d.get('executed') is not False:
            raise ValueError('CANCEL_STATE_NOT_VERIFIED')
        if d.get('form_opened'):
            return ['已在下方打开取消订阅任务卡，尚未取消；请在卡片中选择要取消的订阅。',
                    '查看取消详情后由本人确认；NOVA 不能代为确认。']
        return [f'已在下方显示取消订阅任务卡：{literal(d.get("merchant_name") or "未提供")}，'
                f'{money(d.get("amount_fen"), d.get("currency", "CNY"))} / {literal(d.get("billing_cycle") or "周期未提供")}，'
                f'计划下次扣费 {literal(d.get("next_charge_at") or "未提供")}（UTC）。尚未取消。',
                '请在任务卡中查看取消详情并由本人确认；确认后停止后续模拟扣费，历史流水保留，不代表取消真实商户合同。']
    if name in {'list_subscriptions', 'get_subscription'}:
        rows = d.get('items', []) if name == 'list_subscriptions' else [d]
        lines = ([page_notice(d, min(len(rows), MAX_ROWS))] if name == 'list_subscriptions' else [])
        if d.get('summary'):
            lines.append('筛选后全部分页的生效、每月、人民币订阅预计月费：'
                         + money(d['summary'].get('monthly_commitment_fen')) + '，不等于实际扣款。')
        return lines + [subscription_text(row) for row in rows[:MAX_ROWS]]
    if name == 'prepare_shop_purchase':
        if d.get('created'):
            order = d['order']
            return [f'已提交订单：{literal(order["product_name"])} × {int(order["quantity"])}，'
                    f'单价 {money(order["unit_price_fen"])}，合计 {money(order["total_fen"])}。',
                    '店铺已向银行发起付款申请，尚未扣款。请在下方订单卡片选择付款账户并由本人确认付款；需要时还须本人输入验证码。']
        lines = ['已在下方打开订单表单，尚未下单，也未付款。']
        if d.get('missing_fields'):
            lines.append('请在表单中补充：' + '、'.join(literal(item) for item in d['missing_fields']) + '。')
        if d.get('notice'):
            lines.append(literal(d['notice']))
        lines.append('提交订单后，付款仍须本人在订单卡片中确认。')
        return lines
    if name == 'create_calendar_event':
        if d.get('created'):
            event = d['event']
            when = literal(event['starts_at_local']) + (f' 至 {literal(event["ends_at_local"])}' if event.get('ends_at_local') else '')
            lines = [f'已创建日程 {literal(event["title"])}，时间 {when}。']
            if event.get('location'):
                lines.append(f'地点：{literal(event["location"])}。')
            lines.append('如有误，可在下方日程卡片中删除。')
            return lines
        lines = ['已在下方打开日程表单，尚未创建。']
        if d.get('missing_fields'):
            lines.append('请在表单中补充：' + '、'.join(literal(item) for item in d['missing_fields']) + '。')
        if d.get('notice'):
            lines.append(literal(d['notice']) + '')
        lines.append('核对后点击“创建日程”。')
        return lines
    if name in {'list_calendar_events', 'search_calendar_events', 'get_calendar_event'}:
        rows = d.get('items', [])[:MAX_ROWS]
        lines = [page_notice(d, len(rows)), *[calendar_text(row) for row in rows]]
        if name == 'search_calendar_events' and d.get('total', 0) > 1:
            lines.append('存在多个匹配，请先选择具体活动。')
        return lines + ['以上为本人已保存日历；没有已保存安排不表示一定空闲。']
    if name in {'list_shop_products', 'get_shop_product'}:
        rows = d.get('items', []) if name == 'list_shop_products' else [d['product']]
        lines = ([page_notice(d, min(len(rows), MAX_ROWS))] if name == 'list_shop_products' else [])
        lines += [f'商品名称 {literal(row["name"])}；单价 {money(row["price_fen"])}。' for row in rows[:MAX_ROWS]]
        if name == 'get_shop_product':
            lines.append(f'{d["requested_quantity"]} 件报价 {money(d["estimated_total_fen"])}，未下单、未付款。')
        return lines + ['商品目录不证明库存、配送、营业时间或优惠。']
    if name in {'list_shop_orders', 'get_shop_order'}:
        rows = d.get('items', []) if name == 'list_shop_orders' else [d]
        return ([page_notice(d, min(len(rows), MAX_ROWS))] if name == 'list_shop_orders' else []) + [order_text(row) for row in rows[:MAX_ROWS]]
    if name == 'project_cashflow':
        return [amounts(d, {
            'opening_available_fen': '期初可用现金', 'planned_outflow_fen': '计划流出',
            'estimated_income_fen': '估计收入', 'estimated_variable_spending_fen': '估计可变消费',
            'baseline_end_fen': '基准期末估计', 'scenario_end_fen': '假设情景期末估计',
            'scenario_saving_fen': '假设情景差额'}) + '。',
            '这是只读估计，历史高低线不是置信区间；缺失项目不表示未来为零，情景差额不表示已节省或到账。']
    if name in {'get_budget_status', 'list_forecast_reviews', 'list_recurring_records', 'get_subscription_insights'}:
        return report_rows(d) + ['计划、历史估计和实际扣款分别核对；此查询不执行取消、扣费或资金操作。']
    if name in {'manage_transfer_request', 'manage_collection_request', 'manage_wealth_request'}:
        # 专用服务端流程生成文案，模型自由问题已在子流程入口替换。
        return [result.get('reply') or '请核对可信任务卡；聊天不代为确认、验证或执行交易。']
    raise ValueError('UNSUPPORTED_EVIDENCE_SOURCE')


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    tool: str
    source: str
    as_of: str | None
    content_hash: str
    text: str
    outcome: str

    def public(self) -> dict:
        return {key: getattr(self, key) for key in ('evidence_id', 'tool', 'source', 'as_of', 'content_hash', 'outcome')}


def evidence_for(index: int, item: dict) -> Evidence | None:
    name, result = item['name'], item['result']
    # 计划登记和收款语义解析是内部步骤；用户可见结论来自随后的业务工具结果。
    if name in {'declare_goal_plan', 'inspect_collection_request'}:
        return None
    title = TOOL_LABELS.get(name, '工具查询')
    digest = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    outcome = 'success'
    if result.get('error'):
        outcome = 'failed'
        clarification = {
            'MISSING_SELECTION': '请先选择具体对象或补充名称。',
            'MISSING_SHOP': '请指定鲜花店或蛋糕店。',
            'INVALID_ARGUMENTS': '查询条件未通过校验，请补充明确的对象和范围。',
            'TOOL_NOT_ALLOWED': '本轮不允许调用该工具，请重新说明需求。',
            'PARAMETER_NOT_GROUNDED': '参数缺少本轮原文依据，请明确产品、数值、单位或情景假设。',
        }.get(result['error'], '暂时无法查询，请稍后重试；未取得可核验结果。')
        if name == 'preview_card_operation' and result.get('message') and result['error'] not in {
                'INVALID_ARGUMENTS', 'TOOL_NOT_ALLOWED', 'PARAMETER_NOT_GROUNDED'}:
            # 银行业务规则拒绝（如状态不允许、超出上限）原样告知；未生成任务卡，卡片未变更。
            clarification = f'银行未受理：{literal(result["message"])}。未生成任务卡，卡片未变更。'
        lines = [clarification]
    else:
        try:
            lines = body(name, result)
            if not lines or not any(lines):
                raise ValueError('EMPTY_EVIDENCE')
        except (KeyError, TypeError, ValueError, AttributeError):
            outcome = 'unavailable'
            lines = ['返回资料不完整，暂时无法提供可核验的结论；请在对应工作台核对。']
    scope = result.get('data') if isinstance(result.get('data'), dict) else {}
    prefix = []
    for key, label in (('query', '查询范围'), ('filters', '筛选条件'), ('month', '月份'),
                       ('start', '开始时间'), ('end_exclusive', '结束时间（不含）'), ('scenario', '情景假设')):
        if key in scope:
            # 范围只展示日期/分类等公开条件，内部对象 ID 不复述。
            value = scope[key]
            if isinstance(value, dict):
                if 'account_id' in value:
                    prefix.append('账户范围：仅指定账户。' if value['account_id'] else '账户范围：本人当前授权账户。')
                value = scope_text(value)
            if value:
                prefix.append(f'{label}：{value if isinstance(scope[key], dict) else literal(value)}。')
    kind = result.get('data_kind')
    if kind == 'simulated_bank' or name in {'manage_transfer_request', 'manage_collection_request', 'manage_wealth_request'}:
        prefix.append('数据类型：模拟银行数据。')
    elif kind == 'simulated_merchant':
        prefix.append(f'数据类型：模拟店铺，店铺 {literal(result.get("shop_name", "未提供"))}。')
    elif kind == 'user_calendar':
        prefix.append('数据类型：本人日历。')
    at = result.get('as_of') or result.get('server_time_utc')
    if at:
        prefix.append(f'查询截止时间：{literal(at)}。')
    text = f'**{title}**\n' + '\n'.join(prefix + lines)
    return Evidence(f'E{index}-{digest[:12]}', name, str(result.get('source', name)), at, digest, text, outcome)


def collect(trace: list[dict]) -> list[Evidence]:
    # 同一工具、同一参数的重复查询只展示最后一次；最后一次失败不能退回旧成功结果。
    last = {}
    for i, item in enumerate(trace):
        if 'arguments' in item and not item['name'].startswith('manage_'):
            last[(item['name'], item['arguments'])] = i
    return [e for i, item in enumerate(trace)
            if last.get((item['name'], item.get('arguments')), i) == i
            and (e := evidence_for(i, item)) is not None]


def ground_reply(reply: dict, message: str, request_id: str | None = None) -> dict:
    """覆盖所有回答出口；失败时仅降级正文，不重放工具，不改变任务状态。"""
    try:
        evidence = collect(reply.get('tool_calls', []))
    except (KeyError, TypeError, ValueError, AttributeError):
        logger.warning('NOVA grounding reason=EVIDENCE_UNAVAILABLE')
        return {**reply, 'reply': '返回资料不完整，暂时无法提供可核验结论。请在可信工作台核对原任务，避免重复提交。',
                'grounding': {'version': VERSION, 'status': 'partial',
                              'reason_codes': ['EVIDENCE_UNAVAILABLE'], 'sources': []}}
    reasons = []
    selection = None
    if reply.get('mode') == 'model' and not any(e.tool.startswith('manage_') for e in evidence):
        try:
            selection = AnswerSelection.model_validate_json(reply.get('reply') or '')
            allowed = {e.evidence_id for e in evidence}
            if any(key not in allowed for key in selection.evidence_ids):
                reasons.append('UNKNOWN_EVIDENCE_REFERENCE')
                selection = None
        except ValidationError:
            reasons.append('UNSTRUCTURED_MODEL_ANSWER')
    if evidence:
        # 可排序，不可隐藏失败、范围、免责声明或未处理的其他目标。
        order = selection.evidence_ids if selection else []
        rank = {key: i for i, key in enumerate(dict.fromkeys(order))}
        evidence.sort(key=lambda e: rank.get(e.evidence_id, len(rank)))
        text = '\n\n'.join(e.text for e in evidence)
        status = 'grounded' if all(e.outcome == 'success' for e in evidence) else 'partial'
        reasons.extend('TOOL_FAILED' if e.outcome == 'failed' else 'EVIDENCE_UNAVAILABLE'
                       for e in evidence if e.outcome != 'success')
    else:
        status = 'abstained'
        if re.fullmatch(r'\s*(你好|您好|嗨|hello|hi)[！!。？?\s]*', message, re.I):
            text = '你好，我是 NOVA。可以帮你查询账户、账单、卡片、订阅、日历和店铺信息，或建立待本人核对的任务。'
        elif reply.get('mode') == 'offline':
            # 离线无调用出口为固定能力/补充提示，不包含模型正文。
            text = '当前为离线模式。请说明具体查询对象和范围；复杂表达及其他地区时间需要启用模型。'
        else:
            text = {'selection': '请先选择具体对象，或补充名称；多个匹配时需要明确其中一项。',
                    'period': '请补充要查询的日期或账期。',
                    'request': '请补充具体查询对象和范围。'}.get(
                        selection.clarification if selection else 'none',
                        '尚未取得可核验的业务结果，请补充查询对象和范围，或到对应工作台核对。')
        reasons.append('NO_BUSINESS_EVIDENCE')
    if reply.get('goal_plan'):
        text += '\n\n以上仅列出本轮实际返回的结果；目标规划不表示全部步骤已完成，交易仍须本人核对与授权。'
    guard = {'version': VERSION, 'status': status, 'reason_codes': sorted(set(reasons)),
             'sources': [e.public() for e in evidence]}
    # 日志不保留原始问题、模型正文、姓名、金额、账号、验证码或工具结果。
    correlation = hashlib.sha256((request_id or '').encode()).hexdigest()[:12]
    logger.info('NOVA grounding correlation=%s status=%s reasons=%s sources=%d', correlation, status, guard['reason_codes'], len(evidence))
    return {**reply, 'reply': text, 'grounding': guard}
