"""B3c/F19: retain real cash ledger, exclude wealth from every bill read."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.models import TransactionORM, AccountORM
from backend.app.schemas.bills import BillQuery, BillAnalysis
from backend.app.services.bank.bill_service import analyze, transactions
from backend.app.services.bank import data_service, wealth_service as bank
from backend.app.database.connection import get_db
from backend.app.middleware.auth import create_access_token
from backend.app.routers import bills, transactions as transaction_routes
from tests.integration.test_bills import db, tx, NOW, QUERY
from tests.integration.test_wealth_bank import store, assess, authorize, command, move

F19=json.loads((Path(__file__).parents[1]/'fixtures/wealth_portfolio_contract_v1/f19.json').read_text(encoding='utf-8'))


@pytest.mark.asyncio
async def test_f19_http_report_excludes_but_cashflow_api_retains_all_three(db,monkeypatch):
    for row in F19['transactions']:
        db.add(tx(row['tx_id'],row['amount_fen'],row['type'],category=row['category'],when=row['timestamp']))
    db.add(tx('foreign-wealth',888888,'investment_redemption',account='A3',category='investment'))
    await db.commit()
    sessions=async_sessionmaker(db.bind,expire_on_commit=False)
    monkeypatch.setattr(data_service,'AsyncSessionLocal',sessions)
    from backend.app.services.ai import bills as ai
    monkeypatch.setattr(ai,'get_mode',lambda:'offline')
    async def forbidden(*args,**kwargs):
        pytest.fail('B3c verification must not call an external model')
    monkeypatch.setattr(ai,'model_extract',forbidden)
    app=FastAPI()
    app.include_router(bills.router)
    app.include_router(transaction_routes.router)
    async def database():
        async with sessions() as session:
            yield session
    app.dependency_overrides[get_db]=database
    headers={'Authorization':'Bearer '+create_access_token({'sub':'U1'})}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://isolated',headers=headers) as client:
        response=await client.get('/api/v1/bills/analysis',params=F19['query'])
        assert response.status_code==200,response.text
        result=BillAnalysis.model_validate(response.json())
        for key,value in F19['expected_bill'].items():
            assert getattr(result.totals,key)==value
        assert result.totals.investment_purchase_fen==result.totals.investment_redemption_fen==0
        assert result.excluded_transaction_types==['investment_purchase','investment_redemption']
        page=(await client.get('/api/v1/bills/transactions',params=F19['query'])).json()
        assert page['total']==1 and [r['tx_id'] for r in page['items']]==F19['expected_bill_ids']
        for row in F19['transactions'][1:]:
            hidden=(await client.get('/api/v1/bills/transactions',params={**F19['query'],'tx_ids':row['tx_id']})).json()
            assert hidden['total']==0 and hidden['items']==[]
            direct=await client.get('/api/v1/transactions/'+row['tx_id'])
            assert direct.status_code==200 and direct.json()['type']==row['type']
        history=(await client.get('/api/v1/transactions/history')).json()
        assert len(history)==F19['expected_cashflow_count']
        assert {r['tx_id'] for r in history}=={r['tx_id'] for r in F19['transactions']}
        assert (await client.get('/api/v1/transactions/foreign-wealth')).status_code==404
        reply=(await client.post('/api/v1/bills/messages',json={'message':'2026年9月账单'})).json()
        assert reply['analysis']['totals']==response.json()['totals']
        assert reply['message']=='\n'.join(result.report)
        assert '不含理财申购和赎回' in reply['message']
        assert '¥1,000.00' not in reply['message'] and '¥1,100.00' not in reply['message']
    assert await db.scalar(select(func.count()).select_from(TransactionORM))==4
    assert await db.scalar(select(func.sum(AccountORM.balance_fen)))==300_000


@pytest.mark.asyncio
@pytest.mark.parametrize('period,category',[('month',None),('month','dining'),('year',None),('year','dining')])
async def test_wealth_cannot_change_current_previous_anomalies_text_or_filtered_details(db,period,category):
    # Include real consumption anomalies, transfers, salary, refund and unknowns.
    # Notes mentioning investment are customer text, never an exclusion rule.
    for i in range(5):
        db.add(tx(f'previous-{i}',1000,when=f'2026-08-{i+1:02}T04:00:00'))
        db.add(tx(f'last-year-{i}',1000,when=f'2025-09-{i+1:02}T04:00:00'))
    db.add_all([tx('large-consumption',150000,note='理财课程 investment_purchase'),
                tx('duplicate1',1500,'subscription',when='2026-09-11T04:00:00'),
                tx('duplicate2',1500,'subscription',when='2026-09-11T04:01:00'),
                tx('salary',100000,'salary',note='理财赎回不应删除这笔工资'),
                tx('refund',1000,'refund'),tx('out',5000,'transfer_out',counterparty='A3'),
                tx('in',2000,'transfer_in',counterparty='A3'),tx('mystery',777,'mystery'),
                tx('ordinary-classification',1234,'purchase',category='investment')])
    await db.commit()
    query=BillQuery(period=period,date='2026-09' if period=='month' else '2026',category=category)
    before=await analyze(db,'U1',query,now=NOW)
    detail_before=await transactions(db,'U1',query,page_size=100,as_of=NOW)
    assert before['anomalies']
    for i,date in enumerate(['2026-09-10T04:00:00','2026-08-10T04:00:00','2025-09-10T04:00:00']):
        db.add_all([tx(f'wealth-buy-{i}',90000000,'investment_purchase',category='dining',when=date),
                    tx(f'wealth-sell-{i}',99000000,'investment_redemption',category='shopping',when=date)])
    await db.commit()
    after=await analyze(db,'U1',query,now=NOW)
    assert after==before  # Includes both totals, category counts, trend, evidence, text.
    assert await transactions(db,'U1',query,page_size=100,as_of=NOW)==detail_before
    if category is None:
        assert 'ordinary-classification' in {r['tx_id'] for r in detail_before['items']}
        assert before['totals']['unknown_count']==1


@pytest.mark.asyncio
async def test_actual_freeze_cancel_settle_and_redemption_create_cashflows_only_once(store,monkeypatch):
    occurred=datetime(2026,10,9,7,tzinfo=timezone.utc)
    monkeypatch.setattr(bank,'utc_now',lambda:occurred)
    async def rows(db,operation):
        return list((await db.scalars(select(TransactionORM).where(TransactionORM.operation_id==operation))).all())
    async with store() as session:
        await assess(session)
        cancelled=command('b3c-cancel-no-refund')
        await bank.precheck(session,'U1',cancelled)
        assert await rows(session,cancelled.operation_id)==[]
        await bank.accept_many(session,'U1',[(cancelled,await authorize(session,cancelled))])
        assert await rows(session,cancelled.operation_id)==[]
        await bank.cancel(session,'U1',cancelled.operation_id)
        assert await rows(session,cancelled.operation_id)==[]
        buy=command('b3c-purchase-cashflow')
        await bank.accept_many(session,'U1',[(buy,await authorize(session,buy))])
        assert await rows(session,buy.operation_id)==[]
        await move(session,12)
        purchase=await bank.settle(session,buy.operation_id)
        first=await rows(session,buy.operation_id)
        assert len(first)==1 and first[0].type=='investment_purchase' and first[0].status=='completed'
        actual=purchase['details']['actual']
        assert first[0].amount_fen==actual['gross_fen']-actual['refund_fen']
        await bank.settle(session,buy.operation_id)
        assert len(await rows(session,buy.operation_id))==1
        sell=command('b3c-redemption-cashflow',direction='redeem',amount=0,quantity='10.0000')
        await bank.accept_many(session,'U1',[(sell,await authorize(session,sell))])
        assert await rows(session,sell.operation_id)==[]
        await move(session,13)
        pending=await bank.settle(session,sell.operation_id)
        assert pending['status']=='PENDING_CASH' and await rows(session,sell.operation_id)==[]
        await move(session,14)
        paid=await bank.settle(session,sell.operation_id)
        await bank.settle(session,sell.operation_id)
        credited=await rows(session,sell.operation_id)
        assert len(credited)==1 and credited[0].type=='investment_redemption' and credited[0].status=='completed'
        assert credited[0].amount_fen==paid['details']['actual']['cash_fen']
        result=await analyze(session,'U1',BillQuery(date='2026-10'),now=occurred+timedelta(seconds=1))
        assert result['totals']['count']==result['totals']['unknown_count']==0
        assert result['totals']['inflow_fen']==result['totals']['outflow_fen']==0
        assert await session.scalar(select(func.count()).select_from(TransactionORM))==2
