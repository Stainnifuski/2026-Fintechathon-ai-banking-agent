import pytest
from backend.app.services.ai.semantic_bindings import (
    canonical_text,ordinals,resolve_object,bind_field,card_frame,FieldSpec,
    bind_tool_reference,require_object_binding,REFERENCE_TOOLS)
from backend.app.services.ai.parameter_grounding import require_value,require_cash_role,validate_tool_parameters


@pytest.mark.parametrize('text',['总投资金额20000元','总投资２万元','总投入两万元','总额20000元'])
def test_total_uses_longest_semantic_field_not_invest_substring(text):
    require_value('20000',text)
    assert require_cash_role('total','20000',text,quote=text)=='total'


@pytest.mark.parametrize('text,expected',[('保留现金5000元','reserve'),('本次投资金额5000元','invest'),
    ('总投资金额5000元','total'),('这回拿5000元做点理财','invest')])
def test_meaning_is_not_limited_to_a_fixed_field_whitelist(text,expected):
    assert require_cash_role(expected,'5000',text,quote=text)==expected


@pytest.mark.parametrize('text',['我有20000元','现有20000元','总投资金额20000元'])
def test_asset_and_total_cannot_be_silently_reclassified_as_new_cash(text):
    with pytest.raises(ValueError):require_cash_role('invest','20000',text,quote=text)


@pytest.mark.parametrize('raw',['第一只','第1个','第１支','第 一 张','第一笔','第一项'])
def test_reference_spelling_does_not_change_identity(raw):
    rows=[{'id':'opaque:A','name':'甲A'},{'id':'opaque:B','name':'甲C'}]
    assert resolve_object(raw,rows)['id']=='opaque:A'
    require_object_binding(raw+'100元',ordinal=1)


@pytest.mark.parametrize('raw',['第0个','第三个','甲','第一个和第二个','这只'])
def test_invalid_or_ambiguous_references_do_not_pick_first(raw):
    with pytest.raises(ValueError):resolve_object(raw,[{'id':'A','name':'甲A'},{'id':'B','name':'甲C'}])


def test_manual_view_has_priority_and_omitted_field_requires_focus():
    card={'task_id':'one','version':3,'command':{'cash_field':'reserve','cash_yuan':'100'},
          'view_context':{'cash_field':'investment','cash_yuan':'300'}}
    frame=card_frame(card,focused_stage='budget')
    assert frame.focused_field=='invest' and frame.inherit(None,'cash_yuan')=='300'
    assert require_cash_role(None,'500','改成500',frame.focused_field)=='invest'
    with pytest.raises(ValueError):require_cash_role(None,'500','改成500')
    assert card_frame(card,focused_stage='amounts').focused_field is None
    with pytest.raises(ValueError):require_cash_role('reserve','500','改成500','invest',quote='改成500')
    with pytest.raises(ValueError):require_cash_role('invest','500','改成500',quote='改成500')
    from backend.app.services.ai.wealth_steps import parameter_stage
    prior={**card,'command':{**card['command'],'kind':'planning','products':[]}}
    assert parameter_stage(prior,{'kind':'planning','products':[],'cash_field':'invest','cash_yuan':'500'},'trade')=='budget'
    assert parameter_stage(prior,{'kind':'planning','products':[],'cash_field':'invest','cash_yuan':'300.00'}) is None


@pytest.mark.parametrize('message,quote',[('不要投入100元','投入100元'),('不是保留100元','保留100元'),
    ('投入200元','投入100元'),('查看000100基金','000100')])
def test_numeric_evidence_cannot_be_fabricated_or_negation_stripped(message,quote):
    with pytest.raises(ValueError):require_value('100',message,quote=quote)


@pytest.mark.parametrize('text,value,unit',[('赎回一半','50','percent'),('赎回百分之五十','50','percent'),
    ('赎回５０％','50','percent'),('保留20,000元','20000','money')])
def test_equivalent_numeric_expressions_keep_exact_values(text,value,unit):
    require_value(value,text,unit=unit,quote=text)


def test_multi_object_quotes_cannot_swap_money_between_items():
    require_object_binding('第一个100元',ordinal=1)
    with pytest.raises(ValueError):require_object_binding('第一个100元，第二个200元',ordinal=1)
    with pytest.raises(ValueError):require_object_binding('A产品100元B产品200元',aliases=['A产品'],other_aliases=['B产品'])


@pytest.mark.parametrize('tool',list(REFERENCE_TOOLS))
def test_shared_tool_adapter_resolves_only_current_server_snapshot(tool):
    field,providers,_=REFERENCE_TOOLS[tool]
    rows=[{field:'one'},{field:'two'}]
    values={field:'第二个'}
    if tool=='preview_card_operation':values['action']='card_lock'
    trace=[{'name':providers[0],'result':{'data':{'items':rows}}}]
    assert bind_tool_reference(tool,values,'看看第二个',trace)[field]=='two'
    with pytest.raises(ValueError):bind_tool_reference(tool,values,'看看第二个',[])
    with pytest.raises(ValueError):bind_tool_reference(tool,{**values,field:'one'},'第二个',trace)
    with pytest.raises(ValueError):bind_tool_reference(tool,values,'第二个',trace+[{'name':providers[0],'result':{'error':'FAILED'}}])


def test_future_adapter_can_register_fields_without_wealth_code_changes():
    fields=(FieldSpec('one_time',('单次额度',)),FieldSpec('daily',('每日额度',)))
    assert bind_field('daily','每日额度500元',specs=fields)=='daily'
    assert bind_field(None,'改成500',previous='daily',specs=fields)=='daily'
    assert canonical_text('Ａ C')=='ac'
    assert ordinals('2026年第二季度的流水')==[]
    assert ordinals('不要第二张，选第一张')==[1]
    assert not ordinals('不要第一只')
    with pytest.raises(ValueError):resolve_object('opaque:a',[{'id':'opaque:A','name':'甲基金'}])


def test_card_limit_fields_share_role_binding_and_per_value_quotes():
    values={'action':'card_set_transaction_limits','card_id':'C1','single_txn_limit':'100','daily_txn_limit':'200',
            'value_quotes':{'single_txn_limit':'单笔100元','daily_txn_limit':'每日200元'}}
    validate_tool_parameters('preview_card_operation',values,'单笔100元，每日200元')
    with pytest.raises(ValueError):
        validate_tool_parameters('preview_card_operation',{'action':'card_set_transaction_limits','daily_txn_limit':'100'},'单笔100元')
def test_product_number_and_chinese_comma_never_become_thousands_amount():
    from backend.app.services.ai.parameter_grounding import require_value,explicit_numbers
    from decimal import Decimal
    for text in ('申购模拟远山·现金快取01，100元','申购B017,100元','购买产品01,100元'):
        require_value('100',text,quote='100元')
        assert explicit_numbers(text)=={Decimal('100')}
    require_value('1234.56','投资1,234.56元',quote='投资1,234.56元')

