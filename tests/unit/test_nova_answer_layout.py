from backend.app.services.ai.grounding import AnswerSelection
from backend.app.services.ai.answer_layout import format_evidence
import pytest
from pydantic import ValidationError


def test_model_may_choose_layout_but_cannot_supply_rewritten_financial_facts():
    choice=AnswerSelection.model_validate({'evidence_ids':['E1'],'layout':'list'})
    assert choice.layout=='list'
    with pytest.raises(ValidationError):
        AnswerSelection.model_validate({'evidence_ids':['E1'],'reply':'已到账1000元'})


def test_common_layout_preserves_zero_unknown_limits_and_every_fact():
    original='**账单分析**\n查询范围：本人。\n流入0.00 CNY；流出10.20 CNY。\n尚未核实，不保证收益。'
    rendered=format_evidence(original)
    assert '\n\n- ' in rendered
    for fact in original.splitlines():
        assert fact in rendered
