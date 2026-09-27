"""模拟银行手机号规范化：仅接受明确的中国大陆手机号码。"""

import re

def normalize_phone(value: str) -> str:
    """移除允许的展示分隔符和 +86；不做尾号或模糊匹配。"""
    if not isinstance(value, str):
        raise ValueError("请输入完整的中国大陆手机号")
    compact = re.sub(r"[ -]", "", value.strip())
    if compact.startswith("+86"):
        compact = compact[3:]
    if not re.fullmatch(r"1[3-9][0-9]{9}", compact):
        raise ValueError("请输入完整的中国大陆手机号")
    return compact


def mask_phone(value: str) -> str:
    normalized = normalize_phone(value)
    return normalized[:3] + "****" + normalized[-4:]