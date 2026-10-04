"""Versioned semantic patches. Model interpretation never grants spending authority."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictSemantic(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Budget(StrictSemantic):
    mode: Literal['fixed', 'range', 'proportion', 'request_suggestion']
    amount_fen: int | None = Field(default=None, ge=0, le=2_000_000_000)
    min_fen: int | None = Field(default=None, ge=0, le=2_000_000_000)
    max_fen: int | None = Field(default=None, ge=0, le=2_000_000_000)
    ratio: str | None = Field(default=None, max_length=32)
    base: Literal['available_cash', 'confirmed_budget', 'settled_proceeds'] | None = None

    @model_validator(mode='after')
    def valid(self):
        if self.mode == 'fixed' and self.amount_fen is None:
            raise ValueError('fixed budget requires amount')
        if self.mode == 'range' and (self.min_fen is None or self.max_fen is None or self.min_fen > self.max_fen):
            raise ValueError('invalid budget range')
        if self.mode == 'proportion':
            try:
                ratio = Decimal(self.ratio or '')
            except InvalidOperation as error:
                raise ValueError('invalid ratio') from error
            if not ratio.is_finite() or not 0 < ratio <= 1 or self.base is None:
                raise ValueError('invalid proportion or missing base')
        return self


class Horizon(StrictSemantic):
    unit: Literal['days', 'months', 'years', 'date', 'unknown']
    count: int | None = Field(default=None, ge=1, le=3650)
    use_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    text: str = Field(max_length=200)

    @model_validator(mode='after')
    def valid(self):
        if self.unit in {'days', 'months', 'years'} and self.count is None:
            raise ValueError('duration count required')
        if self.unit == 'date':
            date.fromisoformat(self.use_date or '')
        return self


class Patch(StrictSemantic):
    field: Literal['investment_budget', 'declared_assets', 'reserve_requirement', 'horizon',
                   'spending_purpose', 'preferences', 'excluded_categories', 'advice_only']
    op: Literal['set', 'clear', 'mark_unknown', 'skip']
    quote: str = Field(min_length=1, max_length=500)
    # Structured alternatives avoid free-form model dictionaries in executable fields.
    budget: Budget | None = None
    horizon: Horizon | None = None
    amount_fen: int | None = Field(default=None, ge=0, le=2_000_000_000)
    text: str | None = Field(default=None, max_length=200)
    values: list[str] = Field(default_factory=list, max_length=12)
    flag: bool | None = None

    @model_validator(mode='after')
    def valid(self):
        if self.op != 'set':
            if any(v is not None for v in (self.budget, self.horizon, self.amount_fen, self.text, self.flag)) or self.values:
                raise ValueError('non-set patch cannot carry a value')
            return self
        required = {'investment_budget': self.budget, 'horizon': self.horizon,
                    'declared_assets': self.amount_fen, 'reserve_requirement': self.amount_fen,
                    'spending_purpose': self.text, 'advice_only': self.flag}
        if self.field in required and required[self.field] is None:
            raise ValueError('patch value missing')
        if self.field == 'excluded_categories' and any(v not in {'cash','bond','mixed','equity','index'} for v in self.values):
            raise ValueError('unknown category')
        return self


class SemanticIntent(StrictSemantic):
    action: Literal['research', 'compare', 'assets', 'plan', 'purchase', 'redeem', 'status', 'external']
    relation: Literal['independent', 'sequential', 'conditional'] = 'independent'
    target: str | None = Field(default=None, max_length=120)


class SemanticFrame(StrictSemantic):
    schema_version: Literal['wealth-semantics-v1'] = 'wealth-semantics-v1'
    intents: list[SemanticIntent] = Field(default_factory=list, max_length=8)
    patches: list[Patch] = Field(default_factory=list, max_length=16)
    ambiguities: list[str] = Field(default_factory=list, max_length=4)


def horizon_days(horizon: dict, business_date: str) -> int | None:
    start = date.fromisoformat(business_date[:10])
    item = Horizon.model_validate(horizon)
    if item.unit == 'unknown':
        return None
    if item.unit == 'days':
        end = start + timedelta(days=item.count)
    elif item.unit == 'date':
        end = date.fromisoformat(item.use_date)
    else:
        months = item.count * (12 if item.unit == 'years' else 1)
        index = start.year * 12 + start.month - 1 + months
        year, month = divmod(index, 12)
        end = date(year, month + 1, min(start.day, monthrange(year, month + 1)[1]))
    days = (end - start).days
    if not 1 <= days <= 3650:
        raise ValueError('使用期限超出范围或已过去')
    return days


def apply_frame(state: dict | None, frame: SemanticFrame, *, message: str, message_id: str,
                session_id: str, bank_facts: dict, account: dict | None) -> dict:
    """Quotes bind changes to this turn; past facts are not model-authored input."""
    state = dict(state or {'schema_version':'wealth-requirements-v1', 'version':0, 'fields':{}})
    fields = dict(state['fields'])
    previous_budget = fields.get('investment_budget', {}).get('resolved_fen')
    changes = []
    for patch in frame.patches:
        if patch.quote not in message:
            raise ValueError('语义字段缺少本轮原文证据')
        item = {'state': {'set':'explicit', 'clear':'unmentioned', 'mark_unknown':'unknown', 'skip':'skipped'}[patch.op],
                'quote':patch.quote, 'message_id':message_id, 'source_turn':message_id}
        if patch.op == 'set':
            if patch.field == 'investment_budget':
                item['value'] = patch.budget.model_dump()
                budget = patch.budget
                if budget.mode == 'fixed':
                    item['resolved_fen'] = budget.amount_fen
                elif budget.mode == 'proportion':
                    if budget.base == 'available_cash':
                        base = account['available_fen'] if account else None
                    elif budget.base == 'confirmed_budget':
                        base = previous_budget
                    else:
                        # Only reconciled proceeds supplied by the bank/DAG service qualify.
                        base = bank_facts.get('verified_settled_proceeds_fen')
                    item['base_fen'] = base
                    if base is not None:
                        item['resolved_fen'] = int((Decimal(base)*Decimal(budget.ratio)).to_integral_value(rounding=ROUND_DOWN))
                    else:
                        item['state'] = 'base_pending'
            elif patch.field == 'horizon':
                item['value'] = patch.horizon.model_dump()
                item['resolved_days'] = horizon_days(item['value'], bank_facts['business_at'])
            elif patch.field in {'declared_assets','reserve_requirement'}:
                item['value'] = patch.amount_fen
                if patch.field == 'reserve_requirement' and patch.amount_fen == 0:
                    item['state'] = 'no_extra_requirement'
            elif patch.field in {'preferences', 'excluded_categories'}:
                item['value'] = patch.values
            elif patch.field == 'advice_only':
                item['value'] = patch.flag
            else:
                item['value'] = patch.text
            if patch.field == 'spending_purpose' and patch.text == 'none':
                item['state'] = 'no_specific_purpose'
        fields[patch.field] = item
        changes.append(patch.field)
    state.update(fields=fields, session_id=session_id, message_id=message_id,
                 ambiguities=frame.ambiguities, intents=[i.model_dump() for i in frame.intents],
                 version=state['version']+(1 if changes else 0), changed_fields=changes,
                 bank_cash={'available_fen':account['available_fen'], 'account_id':account['account_id'],
                            'as_of':bank_facts['business_at']} if account else None)
    return state


def planning_slots(state: dict, legacy: dict) -> dict:
    slots = dict(legacy)
    mapping = {'investment_budget': 'budget_fen', 'reserve_requirement':'reserve_fen', 'horizon':'horizon_days'}
    for field, key in mapping.items():
        item = state['fields'].get(field)
        if item is None:
            continue
        slots.pop(key, None)
        if field == 'investment_budget':
            slots.pop('budget_range', None); slots.pop('suggest_budget', None)
            if item.get('resolved_fen') is not None:
                slots[key] = item['resolved_fen']; slots['budget_source'] = 'explicit_semantic_patch'
            value = item.get('value', {})
            if value.get('mode') == 'range': slots['budget_range'] = [value['min_fen'], value['max_fen']]
            if value.get('mode') == 'request_suggestion': slots['suggest_budget'] = True
        elif field == 'horizon' and item.get('resolved_days') is not None:
            slots[key] = item['resolved_days']
        elif field == 'reserve_requirement' and item.get('value') is not None:
            slots[key] = item['value']
    # Preference and purpose are distinct; old 'need' is solely preference.
    slots['constraint_states'] = state['fields']
    return slots
