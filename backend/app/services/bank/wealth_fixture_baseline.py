"""Frozen, explicitly authored disposable opening fixtures; never copy a live round on reset."""
from copy import deepcopy
from datetime import date, datetime
from sqlalchemy import select
from ...models import TransactionORM
from ...models.wealth import (WealthOrderORM, WealthLotORM, WealthCashHoldORM,
    WealthUnitHoldORM, WealthSettlementORM)
from ...domain.wealth_synthetic import fingerprint
from ...domain.time import utc_now
from .transfer_service import BankError

MODELS = (WealthOrderORM, WealthLotORM, WealthCashHoldORM, WealthUnitHoldORM,
          WealthSettlementORM, TransactionORM)
VERSION = 'disposable-opening-v1'


def encode(row):
    return {column.key: value.isoformat() if isinstance(value, (date, datetime)) else deepcopy(value)
            for column in row.__table__.columns for value in [getattr(row, column.key)]}


async def freeze(db, context):
    """Admin fixture creator calls once, before the account is exposed for interaction."""
    orders = list(await db.scalars(select(WealthOrderORM).where(WealthOrderORM.account_id == context.account_id)))
    lots = list(await db.scalars(select(WealthLotORM).where(WealthLotORM.account_id == context.account_id)))
    ids = [row.operation_id for row in orders]
    rows = [orders, lots]
    rows.extend([list(await db.scalars(select(model).where(model.operation_id.in_(ids))))
                 for model in (WealthCashHoldORM, WealthUnitHoldORM, WealthSettlementORM)])
    rows.append(list(await db.scalars(select(TransactionORM).where(TransactionORM.account_id == context.account_id))))
    base = {'schema': VERSION, 'cash_fen': 2000000, 'business_at': context.data['business_at'],
            'tables': {model.__tablename__: [encode(row) for row in values] for model, values in zip(MODELS, rows)}}
    context.data = {**context.data, 'fixture_baseline': base, 'fixture_version': VERSION+':'+fingerprint(base)}


async def restore(db, base, user_id, account_id, run_id):
    """Clone only immutable admin baseline; IDs are new and no authorization/verification is inherited."""
    tables = base.get('tables', {})
    if not tables:
        return
    if base.get('schema') != VERSION or set(tables) != {model.__tablename__ for model in MODELS}:
        raise BankError('初始fixture版本不支持，请管理员迁移基线', 'FIXTURE_BASELINE_INVALID', 409)
    mapping = {}
    for model in MODELS:
        pk = next(iter(model.__table__.primary_key.columns)).key
        for values in tables[model.__tablename__]:
            mapping[values[pk]] = 'FX'+fingerprint([run_id, model.__tablename__, values[pk]])[:40]
    for model in MODELS:
        for stored in tables[model.__tablename__]:
            values = deepcopy(stored)
            for column in model.__table__.columns:
                key = column.key
                value = values.get(key)
                if key == 'account_id': values[key] = account_id
                elif key == 'user_id': values[key] = user_id
                elif key in {'operation_id','lot_id','hold_id','event_id','tx_id'} and value in mapping:
                    values[key] = mapping[value]
                elif key == 'authorization_id': values[key] = 'FIXTURE-NON-AUTH-'+fingerprint([run_id, value])[:24]
                elif isinstance(column.type, __import__('sqlalchemy').DateTime) and value:
                    values[key] = datetime.fromisoformat(value)
                elif isinstance(column.type, __import__('sqlalchemy').Date) and value:
                    values[key] = date.fromisoformat(value)
            if model is WealthOrderORM:
                values['details'] = {**values.get('details', {}), 'opening_fixture': True,
                    'fixture_version': VERSION, 'human_authorization_inherited': False}
                values['accepted_at'] = utc_now()
                values['param_digest'] = fingerprint([run_id, stored['param_digest']])
                values['plan_id'] = None
            db.add(model(**values))
        await db.flush()
