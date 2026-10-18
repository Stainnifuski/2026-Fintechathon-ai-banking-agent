"""Authorized exact-code erasure with financial-reference guards.

Database URL is read only from the environment. Does not fetch fund data.
"""
import asyncio,hashlib,json,os
from sqlalchemy import MetaData,select,delete,update,func,cast,String,or_
from sqlalchemy.ext.asyncio import create_async_engine

POLICY='remove-missing-sharpe-20261017-v1'
DROP=object()
IDENTITY={'product_id','code','source_product_code','基金代码','version_id','id'}


def scrub(value,codes):
    if isinstance(value,str) and value.split(':')[0].removeprefix('R') in codes:return DROP
    if isinstance(value,dict):
        if any(isinstance(value.get(k),str) and value[k].split(':')[0].removeprefix('R') in codes for k in IDENTITY):return DROP
        result={}
        for key,child in value.items():
            if key.split(':')[0].removeprefix('R') in codes:continue
            clean=scrub(child,codes)
            if clean is not DROP:result[key]=clean
        return result
    if isinstance(value,list):
        result=[]
        for child in value:
            if isinstance(child,str) and child.split(':')[0].removeprefix('R') in codes:continue
            clean=scrub(child,codes)
            if clean is not DROP:result.append(clean)
        return result
    return value


async def purge(url,codes):
    engine=create_async_engine(url)
    receipt={'deleted':{},'json_updated':{},'retired_waiting':0,'released_fen':0,'receivables_paid_fen':0}
    try:
        async with engine.begin() as c:
            metadata=MetaData();await c.run_sync(metadata.reflect)
            tables=metadata.tables;ids=sorted(codes|{'R'+code for code in codes})
            def predicate(table):
                predicates=[]
                for name in {'product_id','code','source_product_code'}&set(table.c.keys()):predicates.append(table.c[name].in_(ids))
                if 'version_id' in table.c:
                    from sqlalchemy import or_
                    predicates.append(or_(*(table.c.version_id.like(code+':%') for code in ids)))
                from sqlalchemy import or_
                return or_(*predicates) if predicates else None
            # Refuse any unreviewed financial deletion. The audited pool has no
            # financial references; never convert an unknown lot at zero.
            for name in ['wealth_orders','wealth_lots']:
                if name in tables:
                    condition=predicate(tables[name])
                    if condition is not None and await c.scalar(select(func.count()).select_from(tables[name]).where(condition)):
                        raise ValueError('FINANCIAL_REFERENCE_REQUIRES_VALUED_CASH_RESET:'+name)
            accounts=tables.get('accounts')
            def balance_digest(rows):return hashlib.sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()
            before_rows=[dict(r) for r in (await c.execute(select(accounts).order_by(*accounts.primary_key.columns))).mappings()] if accounts is not None else []
            # Legacy pending in retained backups is retired too. Unpriced holds
            # release without crediting principal a second time.
            orders=tables.get('wealth_orders')
            if orders is not None:
                waiting=list((await c.execute(select(orders).where(orders.c.status.in_(['ACCEPTED','PENDING_PRICING','PENDING_CASH'])))).mappings())
                for row in waiting:
                    operation=row['operation_id'];details=row.get('details') or {}
                    if isinstance(details,str):details=json.loads(details)
                    if row['status']=='PENDING_CASH':
                        cash=details.get('actual',{}).get('cash_fen')
                        if type(cash) is not int or cash<0:raise ValueError('RECEIVABLE_UNPROVEN')
                        settlements=tables.get('wealth_settlements')
                        existing=await c.scalar(select(func.count()).select_from(settlements).where(settlements.c.event_id==operation+':cash')) if settlements is not None else 0
                        if existing:raise ValueError('RECEIVABLE_STATUS_INCONSISTENT')
                        await c.execute(update(accounts).where(accounts.c.account_id==row['account_id']).values(balance_fen=accounts.c.balance_fen+cash))
                        # This branch must keep the same idempotent cash record
                        # as the live policy, not just bump an account balance.
                        from datetime import datetime,timezone
                        now=datetime.now(timezone.utc)
                        if settlements is None:raise ValueError('SETTLEMENT_SCHEMA_MISSING')
                        await c.execute(settlements.insert().values(event_id=operation+':cash',operation_id=operation,kind='cash',data={'cash_fen':cash,'policy':POLICY},created_at=now))
                        transactions=tables.get('transactions')
                        if cash and transactions is not None:
                            txid='WRT'+hashlib.sha256(operation.encode()).hexdigest()[:28]
                            await c.execute(transactions.insert().values(tx_id=txid,operation_id=operation,account_id=row['account_id'],type='investment_redemption',amount_fen=cash,counterparty='历史赎回应收',category='investment',note=POLICY,timestamp=now,status='completed',channel='app'))
                        status='SETTLED';receipt['receivables_paid_fen']+=cash
                    else:
                        if details.get('actual'):raise ValueError('PRICED_PENDING_REQUIRES_RECONCILIATION')
                        status='CANCELLED'
                    for name in ['wealth_cash_holds','wealth_unit_holds']:
                        table=tables.get(name)
                        if table is not None:
                            condition=(table.c.operation_id==operation)&(table.c.status=='HELD')
                            if name=='wealth_cash_holds':receipt['released_fen']+=int(await c.scalar(select(func.coalesce(func.sum(table.c.amount_fen),0)).where(condition)))
                            await c.execute(update(table).where(condition).values(status='RELEASED'))
                    await c.execute(update(orders).where(orders.c.operation_id==operation).values(status=status,details={**details,'waiting_reason':None,'retirement_policy':POLICY}))
                    receipt['retired_waiting']+=1
            # Foreign-key order: children before immutable product versions.
            for table in reversed(metadata.sorted_tables):
                if table.name in {'accounts','transactions','users'}:continue
                condition=predicate(table)
                if condition is not None:
                    deleted=(await c.execute(delete(table).where(condition))).rowcount
                    if deleted:receipt['deleted'][table.name]=deleted
            for table in metadata.sorted_tables:
                jsoncols=[col for col in table.c if str(col.type).upper() in {'JSON','JSONB'}]
                if not jsoncols or not table.primary_key.columns:continue
                count=0
                pattern='"(?:R)?(?:'+'|'.join(sorted(codes))+')(?::[0-9]+)?"'
                match=or_(*(cast(col,String).regexp_match(pattern) for col in jsoncols))
                for row in (await c.execute(select(table).where(match))).mappings():
                    changes={}
                    for col in jsoncols:
                        value=row[col.name]
                        clean=scrub(value,codes)
                        if clean is DROP:clean={}
                        if clean!=value:changes[col.name]=clean
                    if changes:
                        condition=[col==row[col.name] for col in table.primary_key.columns]
                        await c.execute(update(table).where(*condition).values(**changes));count+=1
                if count:receipt['json_updated'][table.name]=count
            after_rows=[dict(r) for r in (await c.execute(select(accounts).order_by(*accounts.primary_key.columns))).mappings()] if accounts is not None else []
            if not receipt['receivables_paid_fen'] and balance_digest(before_rows)!=balance_digest(after_rows):raise ValueError('ACCOUNT_CHANGED_UNEXPECTEDLY')
            receipt['account_balance_changes_fen']=receipt['receivables_paid_fen']
        return receipt
    finally:await engine.dispose()


if __name__=='__main__':
    import sys
    codes=set(json.loads(sys.argv[1]))
    print(json.dumps(asyncio.run(purge(os.environ['DATABASE_URL'],codes)),ensure_ascii=False))
