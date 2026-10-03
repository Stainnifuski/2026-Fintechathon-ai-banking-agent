"""只允许隔离副本：历史修复/对话拆分前后金融字段及重复执行核对。"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.models import AccountORM, TransactionORM
from backend.app.models.wealth import (WealthOrderORM, WealthLotORM, WealthCashHoldORM,
    WealthUnitHoldORM, WealthSettlementORM)
from backend.app.services.bank.wealth_reconciliation import reconcile
from backend.app.services.bank.wealth_history_migration import split


async def snapshot(db, *, financial: bool) -> str:
    """日期、占用、现金与份额均保护，仅审计证据支持的事件元数据可修正。"""
    protected = {
        AccountORM: ['account_id', 'user_id', 'balance_fen'],
        TransactionORM: ['tx_id', 'account_id', 'amount_fen', 'type', 'status'],
        WealthOrderORM: ['operation_id', 'account_id', 'amount_fen', 'units_scaled', 'status',
                         'pricing_date', 'confirm_date', 'cash_date'],
        WealthLotORM: list(WealthLotORM.__table__.columns.keys()),
        WealthCashHoldORM: list(WealthCashHoldORM.__table__.columns.keys()),
        WealthUnitHoldORM: list(WealthUnitHoldORM.__table__.columns.keys()),
        WealthSettlementORM: list(WealthSettlementORM.__table__.columns.keys()),
    }
    values = {}
    for model, fields in protected.items():
        if not financial:
            fields = list(model.__table__.columns.keys())
        rows = await db.scalars(select(model).execution_options(populate_existing=True))
        values[model.__tablename__] = sorted(
            [[str(getattr(row, field)) for field in fields] for row in rows])
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


async def main(report: Path) -> None:
    url = make_url(os.environ['DATABASE_URL'])
    name = url.database or ''
    allowed = name.startswith('bank_test_') if url.get_backend_name() == 'postgresql' else (
        url.get_backend_name() == 'sqlite' and Path(name).name.endswith('-copy.db'))
    if not allowed:
        raise ValueError('仅接受bank_test_* PostgreSQL或*-copy.db SQLite副本；不能运行演示/普通账本')
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            before = await snapshot(db, financial=True)
            dry = await reconcile(db)
            history_dry = await split(db)
            assert before == await snapshot(db, financial=True)
            applied = await reconcile(db, apply=True)
            history = await split(db, apply=True)
            after = await snapshot(db, financial=True)
            complete = await snapshot(db, financial=False)
            repeated = await reconcile(db, apply=True)
            history_repeat = await split(db, apply=True)
            assert before == after == await snapshot(db, financial=True)
            assert complete == await snapshot(db, financial=False)
            assert not history_repeat['rows']
            report.write_text(json.dumps({'financial_sha256_before': before, 'financial_sha256_after': after,
                'financial_unchanged': True, 'repeat_unchanged': True, 'dry_run': dry, 'apply': applied,
                'repeat': repeated, 'history_dry': history_dry, 'history_apply': history,
                'history_repeat': history_repeat}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'financial_unchanged': True, 'repeat_unchanged': True,
                'history_source_count': len(history['rows'])}))
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    asyncio.run(main(parser.parse_args().report))
