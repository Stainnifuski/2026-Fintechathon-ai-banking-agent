"""版本化模拟资金配置规则，全部金额来自确定性计算。"""

from decimal import Decimal, ROUND_DOWN
from datetime import date,timedelta
from .wealth import advance_business

RULE_VERSION = "mock-allocation-v1"
WEIGHTS = {
    1: {"cash": 100},
    2: {"cash": 40, "bond": 60},
    3: {"cash": 20, "bond": 40, "mixed": 40},
    4: {"cash": 10, "bond": 30, "mixed": 30, "equity": 20, "index": 10},
    5: {"cash": 10, "bond": 20, "mixed": 30, "equity": 20, "index": 20},
}
MAX_CONCENTRATION_PERCENT = 60
DEVIATION_PERCENT = 10
PERSONAS = (
    {"name": "短期备用", "capacity": 1, "horizon_days": 30, "reserve_fen": 500000},
    {"name": "稳健支出", "capacity": 2, "horizon_days": 90, "reserve_fen": 800000},
    {"name": "均衡长期", "capacity": 3, "horizon_days": 365, "reserve_fen": 500000},
    {"name": "增长且保留现金", "capacity": 4, "horizon_days": 730, "reserve_fen": 500000},
    {"name": "拒绝本金损失", "capacity": 1, "horizon_days": 365, "reserve_fen": 2000000, "purchase_allowed": False},
)


def candidates(products: list[dict], capacity: int, horizon_days: int,
               excluded: list[str]) -> list[dict]:
    """先资格、风险、锁定和到账约束，再按费用/流动性/ID稳定排序。"""
    rows = [p for p in products if p["product_risk_level"] <= capacity and p["status"] == "on_sale"
        and p["product_id"] not in excluded and not p["rules"]["purchase_paused"]
        and liquidity_days(p) <= horizon_days
        and p["nav"] is not None and p.get('purchase_open', True) and not p.get('data_stale', False)]
    return sorted(rows, key=lambda p: (p["product_risk_level"], Decimal(p["rules"]["purchase_fee_rate"]),
        p["rules"]["holding_days"], p["rules"]["cash_business_days"], p["product_id"]))


def liquidity_days(product: dict) -> int:
    """Use each version's calendar rather than multiplying business days by two."""
    rules=product['rules']; start=date.fromisoformat(product.get('nav_date') or '2026-09-30')
    confirmed=advance_business(start,rules.get('confirm_business_days',0),rules['calendar_version'])
    unlocked=confirmed+timedelta(days=rules['holding_days'])
    cash=advance_business(unlocked,rules['cash_business_days'],rules['calendar_version'])
    return (cash-start).days


def allocate(products: list[dict], *, budget_fen: int, reserve_fen: int,
             capacity: int, horizon_days: int, excluded: list[str], purchase_allowed: bool = True,
             exposure_fen: dict[str, int] | None = None, max_items: int = 3, full_investment: bool = False) -> dict:
    """费用包含在每笔总支出，按max_items筛选，保留未分配尾款不强制花光。"""
    if type(budget_fen) is not int or type(reserve_fen) is not int or not 0 <= reserve_fen <= budget_fen:
        raise ValueError("现金保留不能超过本次预算")
    investable = budget_fen - reserve_fen
    eligible = candidates(products, capacity, horizon_days, excluded) if purchase_allowed else []
    categories = {category: [p for p in eligible if p["category"] == category] for category in WEIGHTS[capacity]}
    active = {category: weight for category, weight in WEIGHTS[capacity].items() if categories[category]}
    if exposure_fen is not None:
        total = sum(exposure_fen.values())
        before = {**exposure_fen, "cash": max(0, exposure_fen.get("cash", 0)-investable)}
        active = {category: max(0, total*WEIGHTS[capacity][category]//100-before.get(category, 0))
                  for category in active}
        active = {category: deficit for category, deficit in active.items() if deficit > 0}
    rows = []
    is_v12=any(p.get('dataset_key') and p['dataset_key']!='legacy-v1' for p in products)
    weight_sum = sum(active.values()) if exposure_fen is not None or not is_v12 else 100
    cap = investable * MAX_CONCENTRATION_PERCENT // 100
    used_families=set()
    for category, weight in active.items():
        target = investable * weight // weight_sum
        left = target
        for product in categories[category]:
            if len(rows) >= max_items:
                break
            family=product.get('fund_family_id') or product['product_id']
            if family in used_families: continue
            amount = min(left, cap, product["rules"]["max_amount_fen"])
            if amount >= product["rules"]["min_amount_fen"]:
                rows.append({"product_id": product["product_id"], "product_name": product["name"],
                    "product_version": product["version"], "disclosure_version": product["disclosure"]["version"],
                    "category": category, "amount_fen": amount, 'fund_family_id':family,
                    "reason": f"风险不超过R{capacity}；匹配{horizon_days}天期限，费用和流动性优先；单产品不超过可投资预算60%"})
                left -= amount
                used_families.add(family)
            if left <= 0:
                break
    if full_investment:
        # Complete stated investment within existing risk, family and amount limits.
        remaining=investable-sum(row['amount_fen'] for row in rows)
        for row in rows:
            product=next(p for p in eligible if p['product_id']==row['product_id'])
            extra=min(remaining,max(0,min(cap,product['rules']['max_amount_fen'])-row['amount_fen']))
            row['amount_fen']+=extra;remaining-=extra
        for product in eligible:
            family=product.get('fund_family_id') or product['product_id']
            if remaining<=0 or len(rows)>=max_items:break
            if family in used_families:continue
            amount=min(remaining,cap,product['rules']['max_amount_fen'])
            if amount<product['rules']['min_amount_fen']:continue
            rows.append({'product_id':product['product_id'],'product_name':product['name'],
                'product_version':product['version'],'disclosure_version':product['disclosure']['version'],
                'category':product['category'],'amount_fen':amount,'fund_family_id':family,
                'reason':'匹配正式风险评估；按费用与流动性规则完成本次投入，保留原产品限额。'})
            used_families.add(family);remaining-=amount
    spent = sum(row["amount_fen"] for row in rows)
    return {"rule_version": RULE_VERSION, "budget_fen": budget_fen, "reserve_fen": reserve_fen,
        "investment_fen": spent, "retained_cash_fen": budget_fen-spent, "items": rows,
        "unallocated_fen": investable-spent, "eligible_product_ids": [p["product_id"] for p in eligible],
        "unallocated_reason": ('受产品最低金额、最高限额、家族集中度、候选数量或必要现金留存限制，未能全部分配' if full_investment and spent<budget_fen else None),
        "notice": "比赛虚构配置研究，非真实投资建议；不保证收益，可选择全部保留现金"}
