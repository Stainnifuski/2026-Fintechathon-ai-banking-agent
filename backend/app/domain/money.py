"""银行账本金额：整数分持久化，元仅用于兼容输入和展示。"""

from decimal import Decimal, InvalidOperation

MAX_LEDGER_FEN = 9_223_372_036_854_775_807


def ledger_fen(value: int) -> int:
    """拒绝布尔、小数、负数及超出BIGINT范围的账本值。"""
    if type(value) is not int or not 0 <= value <= MAX_LEDGER_FEN:
        raise ValueError("银行账本金额必须为非负整数分")
    return value


def exact_fen(value: Decimal | float | int | str) -> int:
    """兼容元输入但不舍入，允许零余额。"""
    try:
        if isinstance(value, bool):
            raise ValueError("布尔值不是金额")
        amount = Decimal(str(value)) * 100
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise ValueError("银行金额必须精确到分")
        return ledger_fen(int(amount))
    except (InvalidOperation, TypeError) as error:
        raise ValueError("银行金额无效") from error


def decimal_yuan(value: int) -> Decimal:
    """整数分转精确元，避免浮点累计。"""
    return Decimal(ledger_fen(value)) / 100
