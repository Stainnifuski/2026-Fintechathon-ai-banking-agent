"""User cash budget over a model recommendation, using exact integer cents."""
from fractions import Fraction
from .wealth_portfolio_allocation import ModelError


def cash_budget(amounts, total, reserve, available, unavailable_cash=0):
    if type(reserve) is not int or not 0 <= reserve <= available:
        raise ModelError('PLANNING_CASH_OUT_OF_RANGE')
    cash = reserve + unavailable_cash
    liquid = max(amounts[0], cash)
    if liquid > total:
        raise ModelError('PLANNING_CASH_OUT_OF_RANGE')
    remaining = total - liquid
    noncash = amounts[1] + amounts[2]
    if remaining and not noncash:
        raise ModelError('PLANNING_NONCASH_UNAVAILABLE')
    # Floor growth and give the remaining cent to stable; rounding cannot
    # increase the model's growth share or create/unaccount for money.
    growth = int(Fraction(remaining * amounts[2], noncash)) if noncash else 0
    return [liquid, remaining - growth, growth], cash
