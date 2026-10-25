"""识别明确覆盖上级规则的直接注入；权限和事实边界不依赖此启发式。"""
import re
import unicodedata

_OVERRIDES = (
    r'忽略.{0,12}(?:系统|开发者|安全规则|所有(?:之前|先前)的?指令)',
    r'(?:绕过|跳过|关闭|禁用).{0,8}(?:MFA|安全校验|身份验证).{0,12}(?:直接|立即|执行|转账)',
    r'ignore\s+(?:all\s+)?(?:previous|prior|system|developer)\s+(?:instructions|rules|prompts)',
    r'<\s*(?:system|developer)\s*>|\[\s*system\s*\]',
    r'<\|(?:im_start|start_header_id|system|developer)\|>|\[INST\]|<<SYS>>',
    r'(?:泄露|输出|打印|显示).{0,8}(?:系统提示词|开发者提示词|API.?KEY|访问令牌|密钥)',
    r'(?:reveal|print|dump|exfiltrate)\s+(?:the\s+)?(?:system\s+prompt|api\s*key|access\s+token|secrets)',
)


def detect_injection(text: str) -> bool:
    """保守拦截明确覆盖指令；普通纠错“忽略上一笔转账”不在此范围。"""
    normalized = unicodedata.normalize('NFKC', text)
    normalized = ''.join(c for c in normalized if unicodedata.category(c) != 'Cf')
    return any(re.search(pattern, normalized, re.I) for pattern in _OVERRIDES)
