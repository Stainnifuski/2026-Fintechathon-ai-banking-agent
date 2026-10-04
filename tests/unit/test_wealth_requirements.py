"""Recorded typed semantics, not a claim about live model accuracy."""
import pytest
from pydantic import ValidationError
from backend.app.domain.wealth_requirements import SemanticFrame, apply_frame, planning_slots, horizon_days

FACTS={'business_at':'2026-10-03T10:00:00+08:00'}
ACCOUNT={'account_id':'A1','available_fen':1922900}


def apply(state, message, patches):
    return apply_frame(state,SemanticFrame.model_validate({'patches':patches}),message=message,
        message_id='turn-'+str((state or {}).get('version',0)),session_id='session',bank_facts=FACTS,account=ACCOUNT)


def test_fraction_asset_declaration_and_correction_replay():
    state=apply(None,'投入5成',[{'field':'investment_budget','op':'set','quote':'投入5成',
        'budget':{'mode':'proportion','ratio':'0.5','base':'available_cash'}}])
    assert planning_slots(state,{})['budget_fen']==961450
    state=apply(state,'投入10000',[{'field':'investment_budget','op':'set','quote':'投入10000',
        'budget':{'mode':'fixed','amount_fen':1000000}}])
    state=apply(state,'我有2万半年后用',[
        {'field':'declared_assets','op':'set','quote':'我有2万','amount_fen':2000000},
        {'field':'horizon','op':'set','quote':'半年后用','horizon':{'unit':'months','count':6,'text':'半年后用'}}])
    slots=planning_slots(state,{})
    assert slots['budget_fen']==1000000 and slots['horizon_days']==182
    assert state['fields']['declared_assets']['value']==2000000
    assert 'reserve_fen' not in slots and 'need' not in slots
    state=apply(state,'改八千不要混合',[
        {'field':'investment_budget','op':'set','quote':'改八千','budget':{'mode':'fixed','amount_fen':800000}},
        {'field':'excluded_categories','op':'set','quote':'不要混合','values':['mixed']}])
    assert planning_slots(state,{})['budget_fen']==800000
    assert state['fields']['horizon']['resolved_days']==182


def test_short_answer_only_changes_bound_field_and_clear_is_not_skip():
    state=apply(None,'没有特别用途不用额外留现金',[
        {'field':'spending_purpose','op':'set','quote':'没有特别用途','text':'none'},
        {'field':'reserve_requirement','op':'set','quote':'不用额外留现金','amount_fen':0}])
    assert state['fields']['reserve_requirement']['state']=='no_extra_requirement'
    assert state['fields']['spending_purpose']['state']=='no_specific_purpose'
    state=apply(state,'不知道',[{'field':'horizon','op':'mark_unknown','quote':'不知道'}])
    assert state['fields']['horizon']['state']=='unknown'
    assert state['fields']['reserve_requirement']['value']==0
    state=apply(state,'跳过',[{'field':'spending_purpose','op':'skip','quote':'跳过'}])
    assert state['fields']['spending_purpose']['state']=='skipped'
    state=apply(state,'删掉保留现金条件',[{'field':'reserve_requirement','op':'clear','quote':'删掉保留现金条件'}])
    assert 'reserve_fen' not in planning_slots(state,{'reserve_fen':500000})


def test_calendar_months_and_leap_year():
    assert horizon_days({'unit':'months','count':1,'text':'一个月'},'2026-01-31')==28
    assert horizon_days({'unit':'years','count':1,'text':'一年'},'2024-02-29')==365


@pytest.mark.parametrize('ratio',['NaN','Infinity','-0.5','1.1','0','bad'])
def test_invalid_ratio_rejected(ratio):
    with pytest.raises(ValidationError):
        SemanticFrame.model_validate({'patches':[{'field':'investment_budget','op':'set','quote':'一半',
            'budget':{'mode':'proportion','ratio':ratio,'base':'available_cash'}}]})


def test_quote_and_settled_proceeds_are_not_guessed():
    with pytest.raises(ValueError,match='原文'):
        apply(None,'您好',[{'field':'declared_assets','op':'set','quote':'有两万','amount_fen':2000000}])
    state=apply(None,'到账一半',[{'field':'investment_budget','op':'set','quote':'到账一半',
        'budget':{'mode':'proportion','ratio':'0.5','base':'settled_proceeds'}}])
    assert state['fields']['investment_budget']['state']=='base_pending'
    assert 'budget_fen' not in planning_slots(state,{})


def test_non_set_cannot_carry_value():
    with pytest.raises(ValidationError):
        SemanticFrame.model_validate({'patches':[{'field':'reserve_requirement','op':'clear','quote':'删除','amount_fen':0}]})
