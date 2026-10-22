"""模型提出的数值须能从本轮原文解析，不能从产品代码或历史回答借数字。"""
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from .nlu import amount_fen, UnderstandingError
from ...domain.time import utc_now

_NUMBER = r'(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万]+(?:点[零〇一二三四五六七八九]+)?)'


def _negated(message: str, start: int) -> bool:
    prefix = re.split(r'[，,；;。\n]|而是|但是|不过', message[:start])[-1]
    changes = list(re.finditer(r'改成|改为|改投', prefix))
    if changes and re.search(_NUMBER, prefix[:changes[-1].start()]):
        prefix = prefix[changes[-1].end():]
    return bool(re.search(r'不要|不想|不打算|不准备|不是|不再|别|不(?:投|买|申购|赎回|转)', prefix[-24:]))


def quoted_number(text: str) -> Decimal:
    """把单一原文金额或份额转换为精确数值；不接受范围和模糊量。"""
    raw = re.sub(r'(?:人民币|元|块钱|块|份|%)$', '', text.strip()).strip()
    if re.fullmatch(r'\d+(?:\.\d+)?[万千百]?', raw):
        multiplier = {'万': 10000, '千': 1000, '百': 100}.get(raw[-1], 1)
        return Decimal(raw[:-1] if multiplier != 1 else raw) * multiplier
    if raw in {'零', '〇'}:
        return Decimal(0)
    return Decimal(amount_fen(raw)) / 100


def explicit_numbers(message: str, unit: str = 'money') -> set[Decimal]:
    suffix = r'(?:万元|千元|百元|元|块钱|块|万|千)' if unit == 'money' else r'份' if unit == 'units' else r'%'
    normalized = message.replace('，', ',').replace('％', '%')
    values = set()
    for match in re.finditer(r'(?<![\d.])' + _NUMBER + r'\s*' + suffix, normalized):
        if _negated(normalized, match.start()):
            continue
        try:
            values.add(quoted_number(match.group()))
        except (ValueError, InvalidOperation, UnderstandingError):
            continue
    if unit == 'money':
        for match in re.finditer(
            r'(?:额度|限额|金额|预算|投入|投资|保留|预留|留|买入|申购|改成|改为|填|我有|总资产)'
            r'\s*(?:调到|降到|设为|改成|为|到|是)?\s*(' + _NUMBER + r')(?![\d.])', normalized):
            if _negated(normalized, match.start()):
                continue
            try:
                values.add(quoted_number(match[1]))
            except (ValueError, InvalidOperation, UnderstandingError):
                pass
    # 单独回答数值，允许由已选可信卡片提供字段语境；代码、日期和尾号不在此分支。
    if re.fullmatch(r'\s*' + _NUMBER + r'\s*', normalized):
        try:
            values.add(quoted_number(normalized))
        except (ValueError, InvalidOperation, UnderstandingError):
            pass
    return values


def require_value(value: str | None, message: str, *, unit: str = 'money', quote: str | None = None) -> None:
    if value is None:
        return
    if quote is not None and (not quote.strip() or quote not in message):
        raise ValueError('PARAMETER_QUOTE_INVALID')
    try:
        number = Decimal(value)
        if not number.is_finite() or number < 0 or number not in explicit_numbers(quote or message, unit):
            raise ValueError('PARAMETER_NOT_GROUNDED')
    except InvalidOperation:
        raise ValueError('PARAMETER_NOT_GROUNDED') from None


def require_cash_role(field: str | None, value: str | None, message: str, previous_field: str | None = None) -> None:
    """资产、留存与投资预算分别绑定；“我有两万”不能直接变成投资两万。"""
    if value is None:
        return
    roles = {'reserve': r'预留|保留|留存|留|备用', 'invest': r'投入|投资|预算|买入|申购|拨',
             'total': r'总额|总资产|总资金|我有|现有|可支配'}
    for match in re.finditer('(?:' + roles.get(field, r'(?!)') + r')[^，,；;。\n]{0,24}', message):
        if _negated(message, match.start()):
            continue
        if Decimal(value) in explicit_numbers(match.group()):
            return
    if field == 'invest':
        for match in re.finditer('(' + _NUMBER + r'\s*(?:万元|千元|元|块钱|块|万|千))'
                                 r'\s*(?:做|用于|用来|来)?(?:投资|投入|申购|买入)', message):
            if _negated(message, match.start()):
                continue
            if Decimal(value) == quoted_number(match[1]):
                return
    if previous_field == field and re.search(r'改成|改为|调整|^\s*' + _NUMBER + r'\s*$', message):
        return
    raise ValueError('CASH_ROLE_NOT_GROUNDED')


def validate_wealth_parameters(intent: object, message: str, context: dict | None = None) -> None:
    """兼容理财对话入口；原文quote存在仍须校验其中实际数值。"""
    for field in ('amount_fen', 'budget_fen', 'budget_min_fen', 'budget_max_fen', 'reserve_fen'):
        value = getattr(intent, field, None)
        if value is not None:
            require_value(str(Decimal(value) / 100), message)
    require_value(getattr(intent, 'units', None), message, unit='units')
    frame = getattr(intent, 'semantic', None)
    for patch in frame.patches if frame else []:
        if patch.quote not in message:
            raise ValueError('PARAMETER_QUOTE_INVALID')
        if patch.op != 'set':
            continue
        values = []
        if patch.field in {'declared_assets', 'reserve_requirement'}:
            if patch.field == 'reserve_requirement' and patch.amount_fen == 0 and re.search(r'不用|无需|不需要|不额外|不留', patch.quote):
                continue
            values = [patch.amount_fen]
        if patch.field == 'investment_budget':
            budget = patch.budget
            if budget.mode == 'fixed':
                values = [budget.amount_fen]
                previous = 'invest' if ((context or {}).get('last_question') or {}).get('field') == 'investment_budget' else None
                require_cash_role('invest', str(Decimal(budget.amount_fen) / 100), message, previous)
            elif budget.mode == 'range':
                values = [budget.min_fen, budget.max_fen]
            elif budget.mode == 'proportion':
                ratios = {value / 100 for value in explicit_numbers(patch.quote, 'percent')}
                if '一半' in patch.quote or '半数' in patch.quote:
                    ratios.add(Decimal('.5'))
                if re.search(r'全部|所有|全投', patch.quote):
                    ratios.add(Decimal(1))
                for match in re.finditer('(' + _NUMBER + r')成', patch.quote):
                    ratios.add(quoted_number(match[1]) / 10)
                if Decimal(budget.ratio) not in ratios:
                    raise ValueError('PARAMETER_NOT_GROUNDED')
        for value in values:
            require_value(str(Decimal(value) / 100), patch.quote)


def validate_tool_parameters(name: str, values: dict, message: str | None, results: list[dict] | None = None) -> None:
    """在副作用发生前验证模型新增的金额及情景假设。"""
    if name == 'preview_card_operation':
        from ...schemas.cards import ACTION_FIELDS
        if any(value is not None and key not in ACTION_FIELDS[values['action']]
               for key, value in values.items() if key != 'action'):
            # 由既有业务Schema返回INVALID_ARGUMENTS，不把结构错误掩盖为来源错误。
            return
        for field in ('credit_limit', 'single_txn_limit', 'daily_txn_limit'):
            require_value(values.get(field), message or '')
    if name == 'project_cashflow':
        for reduction in values.get('reductions', []):
            labels = {'dining': '餐饮', 'shopping': '购物', 'transport': '交通',
                      'entertainment': '娱乐', 'education': '教育', 'other': '其他'}
            if labels[reduction['category']] not in (message or ''):
                raise ValueError('SCENARIO_NOT_GROUNDED')
            require_value(str(reduction['percent']), message or '', unit='percent')
        excluded = values.get('excluded_subscription_ids', [])
        if excluded:
            if not re.search(r'排除|不含|不计|(?:假设|如果).{0,12}取消', message or ''):
                raise ValueError('SCENARIO_NOT_GROUNDED')
            known = {}
            for entry in results or []:
                if entry['name'] not in {'list_subscriptions', 'get_subscription'} or entry['result'].get('error'):
                    continue
                data = entry['result'].get('data', {})
                for row in data.get('items', []) if entry['name'] == 'list_subscriptions' else [data]:
                    if row.get('subscription_id') and row.get('merchant_name'):
                        known[row['subscription_id']] = row['merchant_name']
            for key in excluded:
                if key not in known or not (key in (message or '') or known[key] in (message or '')):
                    raise ValueError('SCENARIO_NOT_GROUNDED')
        for event in values.get('one_off_events', []):
            if event['label'] not in (message or ''):
                raise ValueError('SCENARIO_NOT_GROUNDED')
            require_value(str(abs(Decimal(event['amount_fen']) / 100)), message or '')
            at = datetime.fromisoformat(event['at'].replace('Z', '+00:00'))
            if at.tzinfo is None:
                raise ValueError('SCENARIO_NOT_GROUNDED')
            day = at.astimezone(ZoneInfo('Asia/Shanghai')).date()
            today = utc_now().astimezone(ZoneInfo('Asia/Shanghai')).date()
            dates = [today + timedelta(days=offset) for word, offset in (('今天', 0), ('明天', 1), ('后天', 2))
                     if word in (message or '')]
            if day.isoformat() not in (message or '') and day not in dates:
                raise ValueError('SCENARIO_NOT_GROUNDED')
            direction = r'收入|工资|进账|到账|收款' if event['amount_fen'] > 0 else r'支出|消费|付款|转出|支付|花费'
            if not re.search(direction, message or ''):
                raise ValueError('SCENARIO_NOT_GROUNDED')
