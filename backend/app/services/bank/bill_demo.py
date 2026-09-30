"""Deterministic fictional bill history for a fresh demo database only."""
from datetime import datetime, timedelta, timezone

from ...models.transaction import TransactionORM
from .bill_service import TZ


def demo_transactions():
    rows = []
    def add(key, year, month, day, amount, kind="purchase", category="dining", counterparty="演示餐厅", account="A001", seconds=0, status="completed"):
        when = (datetime(year, month, day, 12, tzinfo=TZ) + timedelta(seconds=seconds)).astimezone(timezone.utc)
        rows.append(TransactionORM(tx_id=f"BILL-DEMO-{key}", account_id=account, type=kind,
                                   category=category, amount_fen=amount, timestamp=when,
                                   counterparty=counterparty, note="虚构账单演示数据", channel="app", status=status))
    for year, month in [(2025, 9), (2025, 12), (2026, 1), (2026, 8), (2026, 9)]:
        prefix = f"{year}{month:02}"
        add(prefix + "-salary", year, month, 1, 1500000, "salary", "salary", "演示科技公司")
        for day in range(2, 12):
            add(prefix + f"-meal{day}", year, month, day, 5000 if (year, month) == (2026, 9) else 2000)
        add(prefix + "-travel", year, month, 8, 3000, category="transport", counterparty="演示出行")
        add(prefix + "-shop", year, month, 10, 20000, category="shopping", counterparty="演示商城")
        add(prefix + "-sub", year, month, 12, 1500, "subscription", "subscription", "演示音乐")
    add("large", 2026, 9, 15, 180000, category="shopping", counterparty="演示数码")
    add("duplicate1", 2026, 9, 16, 2500, "subscription", "subscription", "演示视频")
    add("duplicate2", 2026, 9, 16, 2500, "subscription", "subscription", "演示视频", seconds=120)
    add("refund", 2026, 9, 18, 5000, "refund", "shopping", "演示商城")
    add("out", 2026, 9, 19, 10000, "transfer_out", "transfer", "A003")
    add("internal-out", 2026, 9, 20, 20000, "transfer_out", "transfer", "A002")
    add("internal-in", 2026, 9, 20, 20000, "transfer_in", "transfer", "A001", account="A002")
    add("failed", 2026, 9, 21, 999999, status="failed")
    # Retain stable seed IDs so a normal application startup does not append seed charges.
    for row in rows:
        if row.tx_id == "BILL-DEMO-202609-sub":
            row.tx_id, row.counterparty = "TX20260912001", "网易云音乐"
        elif row.tx_id == "BILL-DEMO-duplicate2":
            row.tx_id, row.counterparty = "TX20260918001", "腾讯视频"
        elif row.tx_id == "BILL-DEMO-duplicate1":
            row.counterparty = "腾讯视频"
    return rows
