"""Shared non-authorizing semantic bindings for all NOVA adapters.

Models propose meaning; only server-owned fields and object snapshots supply
omitted context. Normalization never changes bank IDs or performs fuzzy ID search.
"""
from dataclasses import dataclass
import re
import unicodedata


def canonical_text(value: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).casefold()


def product_name_key(value: str) -> str:
    """Ignore decorative middle dots in product names, never numeric dots/IDs."""
    return re.sub('[·•・]', '', canonical_text(value))


def ordinal_number(value: str) -> int:
    value=unicodedata.normalize('NFKC',value)
    if value.isdigit():return int(value)
    digits=dict(zip('零〇一二两三四五六七八九',(0,0,1,2,2,3,4,5,6,7,8,9)))
    result=part=0
    for char in value:
        if char in digits:part=digits[char]
        elif char in '十百':result+=(part or 1)*{'十':10,'百':100}[char];part=0
        else:raise ValueError('REFERENCE_INVALID')
    return result+part


ORDINAL=re.compile(r'第([0-9零〇一二两三四五六七八九十百]+)(?:(?:个|只|支|张|笔|项|条|款|位)|(?=$|[，,。；;：:]))')


def ordinals(text: str, *,include_negated=False) -> list[int]:
    text=canonical_text(text);result=[]
    for match in ORDINAL.finditer(text):
        prefix=re.split(r'[，,；;。]|而是|但是|不过',text[:match.start()])[-1]
        if not include_negated and re.search(r'不要|别|不选|不买|不是',prefix[-20:]):continue
        result.append(ordinal_number(match[1]))
    return result


def quote_in_message(quote: str, message: str) -> bool:
    return bool(quote.strip()) and canonical_text(quote) in canonical_text(message)


def object_mentioned(quote: str, *, aliases=(), ordinal: int | None=None) -> bool:
    text=canonical_text(quote)
    if ordinal is not None and ordinal in ordinals(quote):return True
    # Token boundaries prevent product code 100 matching amount 1000, and keep
    # share classes and opaque object IDs distinct.
    for alias in aliases:
        alias=canonical_text(str(alias))
        if alias and re.search(r'(?<![a-z0-9])'+re.escape(alias)+r'(?![a-z0-9])',text):return True
    return False


def require_object_binding(quote: str, *, aliases=(), ordinal: int | None=None,
                           other_aliases=()) -> None:
    if not object_mentioned(quote,aliases=aliases,ordinal=ordinal):raise ValueError('REFERENCE_NOT_GROUNDED')
    if ordinal is not None and set(ordinals(quote))!={ordinal}:raise ValueError('REFERENCE_AMBIGUOUS')
    if any(object_mentioned(quote,aliases=[a]) for a in other_aliases if a not in aliases):
        raise ValueError('REFERENCE_AMBIGUOUS')


def resolve_object(reference: str | None, objects: list[dict], *, id_field='id',
                   name_fields=('name',), focused_id: str | None=None) -> dict:
    if reference is None or canonical_text(reference) in {'这个','这只','这张','这笔','它','当前这个','当前所选'}:
        matches=[o for o in objects if o.get(id_field)==focused_id] if focused_id else []
    else:
        ranks=ordinals(reference)
        if ranks:
            if len(ranks)!=1 or ranks[0]<1 or ranks[0]>len(objects):raise ValueError('REFERENCE_AMBIGUOUS')
            matches=[objects[ranks[0]-1]]
        else:
            target=canonical_text(reference)
            matches=[o for o in objects if o.get(id_field)==reference or any(canonical_text(str(o.get(k,'')))==target
                for k in name_fields if k!=id_field)]
    if len(matches)!=1:raise ValueError('REFERENCE_AMBIGUOUS')
    return matches[0]


@dataclass(frozen=True)
class FieldSpec:
    key: str
    labels: tuple[str,...]


@dataclass(frozen=True)
class TrustedFrame:
    """Owner adapters construct this after scope/version checks, never from LLM JSON."""
    object_id: str
    version: int
    values: dict
    focused_field: str | None=None

    def inherit(self,explicit,key):
        return explicit if explicit is not None else self.values.get(key)


def card_frame(card: dict, *, focused_stage: str | None=None) -> TrustedFrame:
    command=card.get('command') or {};view=card.get('view_context') or {}
    values={**command,**view}
    field=canonical_field(view.get('cash_field') or command.get('cash_field')) if focused_stage=='budget' else None
    return TrustedFrame(card['task_id'],card['version'],values,field)


CASH_FIELDS=(FieldSpec('reserve',('保留现金金额','保留现金','保留','预留','留存','备用','留')),
             FieldSpec('total',('总投资金额','总投资','总投入','总额','总资产','总资金','我有','现有','可支配')),
             FieldSpec('invest',('本次投资金额','新增投资金额','本次投资','新增投资','投入','投资','预算','买入','申购','拨')))

CARD_FIELDS=(FieldSpec('credit_limit',('信用额度','信用卡额度','授信额度','额度')),
             FieldSpec('single_txn_limit',('单笔限额','单次额度','单笔额度','单笔','单次')),
             FieldSpec('daily_txn_limit',('每日限额','单日额度','每日额度','每天额度','每日','单日','每天')))


def canonical_field(field: str | None) -> str | None:
    return {'investment':'invest','cash_reserve':'reserve','total_investment':'total'}.get(field,field)


def explicit_fields(text: str, specs=CASH_FIELDS) -> set[str]:
    text=canonical_text(text)
    occurrences=[]
    for spec in specs:
        for label in spec.labels:
            for match in re.finditer(re.escape(canonical_text(label)),text):
                if len(label)==1 and not re.match(r'[0-9零〇一二两三四五六七八九十百千万]',text[match.end():]):continue
                occurrences.append((match.start(),match.end(),spec.key))
    # Long field names own their substrings: 总投资金额 is not 新增投资.
    return {key for a,b,key in occurrences if not any(c<=a and b<=d and d-c>b-a for c,d,_ in occurrences)}


def bind_field(proposed: str | None, quote: str, *, previous: str | None=None,
               specs=CASH_FIELDS, semantic_evidence=False) -> str:
    proposed,previous=canonical_field(proposed),canonical_field(previous)
    allowed={s.key for s in specs};fields=explicit_fields(quote,specs)
    if proposed is not None and proposed not in allowed:raise ValueError('FIELD_UNKNOWN')
    if fields:
        if len(fields)!=1 or proposed is not None and proposed not in fields:raise ValueError('FIELD_AMBIGUOUS')
        return next(iter(fields))
    # Omitted role can only inherit from the explicitly focused server card.
    if previous in allowed and proposed in (None,previous):return previous
    # A structured semantic proposal may use unfamiliar wording, but must cite
    # its original span. Numeric-only answers cannot invent an active field.
    meaning=re.sub(r'[\d\W]+|人民币|万元|千元|元|块钱|块|帮我|请|金额|数值|改成|改为|改到|调到|调整|看看|这笔|这个|为|到','',quote)
    if semantic_evidence and proposed in allowed and re.search(r'[\u4e00-\u9fff]{2,}',meaning):return proposed
    raise ValueError('FIELD_AMBIGUOUS')


REFERENCE_TOOLS={
    'get_card':('card_id',('list_cards',),('card_id','card_number')),
    'list_card_transactions':('card_id',('list_cards',),('card_id','card_number')),
    'preview_card_operation':('card_id',('list_cards',),('card_id','card_number')),
    'get_subscription':('subscription_id',('list_subscriptions',),('merchant_name',)),
    'request_subscription_cancellation':('subscription_id',('list_subscriptions',),('merchant_name',)),
    'get_transaction':('tx_id',('list_transactions',),('tx_id',)),
    'get_calendar_event':('event_id',('list_calendar_events','search_calendar_events'),('title',)),
    'get_shop_product':('product_id',('list_shop_products',),('name',)),
    'get_shop_order':('order_id',('list_shop_orders',),('order_id',)),
}


def bind_tool_reference(name: str, values: dict, message: str, trace: list[dict] | None,*,page=None) -> dict:
    """Resolve only server-returned lists (current or owner-scoped TTL cache).

    Page-focus fallbacks remain in the owner adapters. No snapshot means a
    numbered reference needs clarification rather than a freshly reordered list.
    """
    config=REFERENCE_TOOLS.get(name)
    if not config:return values
    field,providers,labels=config
    if name=='preview_card_operation' and values.get('action')=='card_apply_virtual':return values
    reference=values.get(field)
    ranks=ordinals(message)
    if not ranks and ordinals(message,include_negated=True):raise ValueError('REFERENCE_NOT_GROUNDED')
    if page and not ranks and reference and canonical_text(reference) in {'这个','这只','这张','这笔','它','当前这个','当前所选'}:
        selected=None
        if field=='card_id' and page.cards:selected=page.cards.selected_card_id
        elif field=='subscription_id' and page.subscriptions:selected=page.subscriptions.selected_subscription_id
        elif field=='tx_id' and page.selected:selected=page.selected.id
        elif field=='event_id' and page.calendar and page.calendar.selected_event:selected=page.calendar.selected_event.event_id
        elif field in {'product_id','order_id'} and page.shop and page.shop.shop==values.get('shop') and page.shop.selected:
            if page.shop.selected.type==('product' if field=='product_id' else 'order'):selected=page.shop.selected.id
        if selected:return {**values,field:selected}
    if not ranks and not reference:return values
    if len(set(ranks))>1:raise ValueError('REFERENCE_AMBIGUOUS')
    snapshot=None
    for item in reversed(trace or []):
        if item.get('name') not in providers:continue
        result=item.get('result') or {}
        if result.get('error'):raise ValueError('REFERENCE_UNAVAILABLE')
        if values.get('shop') and result.get('shop')!=values['shop']:continue
        snapshot=(result.get('data') or {}).get('items')
        if isinstance(snapshot,list):snapshot=snapshot[:10]  # grounding.MAX_ROWS; only displayed rows
        break
    if not isinstance(snapshot,list):
        if not ranks and reference and not ordinals(reference):return values
        raise ValueError('REFERENCE_UNAVAILABLE')
    if not ranks and reference and not ordinals(reference):
        exact=[o for o in snapshot if o.get(field)==reference]
        if exact:return values
        matches=[o for o in snapshot if any(canonical_text(str(o.get(label,'')))==canonical_text(reference) for label in labels)]
        if len(matches)>1:raise ValueError('REFERENCE_AMBIGUOUS')
        try:resolved=resolve_object(reference,snapshot,id_field=field,name_fields=labels)
        except ValueError:
            # The bank owner may still resolve an explicit ID outside a page.
            if canonical_text(reference) in {'这个','这只','这张','这笔','它','当前这个','当前所选'}:
                raise ValueError('REFERENCE_AMBIGUOUS')
            return values
        return {**values,field:resolved[field]}
    resolved=resolve_object(f'第{ranks[0]}个' if ranks else reference,snapshot,id_field=field,name_fields=labels)
    if reference and not ordinals(reference) and reference!=resolved[field]:raise ValueError('REFERENCE_CONFLICT')
    return {**values,field:resolved[field]}
