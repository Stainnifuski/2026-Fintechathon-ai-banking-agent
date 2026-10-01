"""比赛模拟适当性量表；未经专业效度验证，不由模型评分。"""

QUESTIONNAIRE_VERSION = "mock-questionnaire-v1"
SCORE_VERSION = "mock-score-v1"
QUESTIONS = (
    ("payment", "财务支付能力", ("没有可投资结余", "少量结余", "稳定结余", "充足结余")),
    ("liabilities", "资产与负债", ("负债超过资产", "负债较重", "负债可控", "无负债")),
    ("income", "收入稳定性", ("无稳定收入", "收入波动大", "较稳定", "稳定且有备用金")),
    ("experience", "投资经验", ("无经验", "仅存款", "了解基金理财", "多种模拟投资经验")),
    ("purpose", "投资目的", ("保留本金", "低波动", "平衡增长", "可承受较高波动")),
    ("horizon", "预计投资期限", ("一个月内", "三个月内", "一年内", "一年以上")),
    ("liquidity", "流动性需要", ("随时需要全部资金", "很可能近期使用", "部分资金近期使用", "暂无近期使用需要")),
    ("loss", "本金损失承受能力", ("不能接受任何本金损失", "可接受少量损失", "可接受中等损失", "可接受较大损失")),
    ("understanding", "风险理解", ("不了解净值波动", "知道可能亏损", "理解费用与流动性", "理解产品主要风险")),
)


def score(answers: dict[str, int], *, adult: bool, full_capacity: bool) -> dict:
    """确定性五档评分及保守硬限制；关键矛盾先澄清。"""
    if not adult or not full_capacity:
        raise ValueError("仅支持成年完全民事行为能力模拟客户")
    if set(answers) != {question[0] for question in QUESTIONS}:
        raise ValueError("请本人完整回答9题")
    if any(type(value) is not int or not 0 <= value <= 3 for value in answers.values()):
        raise ValueError("答案必须为0至3的选项编号")
    if answers["loss"] == 0 and answers["purpose"] >= 2:
        raise ValueError("不能接受本金损失与增长偏好矛盾，请本人澄清")
    total = sum(answers.values())
    level = 1 + sum(total >= edge for edge in (6, 12, 18, 24))
    # 收入或资产多不单独获得高承受能力；经验、理解及损失能力共同设上限。
    level = min(level, answers["loss"] + 2,
                answers["understanding"] + 2, answers["experience"] + 2)
    executable = answers["loss"] > 0 and answers["payment"] > 0 and answers["liabilities"] > 0
    return {"score": total, "capacity_level": level, "purchase_allowed": executable,
            "questionnaire_version": QUESTIONNAIRE_VERSION, "score_version": SCORE_VERSION,
            "notice": "比赛模拟量表，未经专业效度验证；风险匹配不保证收益"}
