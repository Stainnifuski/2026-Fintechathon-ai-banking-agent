import json
import httpx
import pytest
from tests.integration.test_bills import db,tx,NOW
from backend.app.models.bill_foundation import BillTransactionMetaORM as Meta

@pytest.mark.asyncio
async def test_new_routes_reject_foreign_identity_and_expose_current_decision_data(db):
    from backend.app.main import app
    from backend.app.database.connection import get_db
    from backend.app.middleware.auth import create_access_token
    from backend.app.services.bank.bill_foundation import sync_users
    from backend.app.services.ai.nova_tools import ToolContext
    from backend.app.schemas.nova import PageContext
    from backend.app.services.ai.nova import execute_tool
    db.add_all([tx('own',100000,counterparty='滴滴出行'),tx('foreign',500,account='A3')]);await db.commit();await sync_users(db,['U1'],now=NOW);await db.commit()
    async def override():yield db
    app.dependency_overrides[get_db]=override
    headers={'Authorization':'Bearer '+create_access_token({'sub':'U1'})}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            assert (await client.get('/api/v1/bills/budgets?month=2026-09')).status_code==401
            response=await client.put('/api/v1/bills/transactions/own/classification',headers=headers,json={'category':'transport','expected_version':0,'apply_to_merchant':True})
            assert response.status_code==200,response.text
            assert (await client.get('/api/v1/bills/transactions/own',headers=headers)).json()['category']=='transport'
            assert (await client.put('/api/v1/bills/transactions/foreign/classification',headers=headers,json={'category':'other','expected_version':0})).status_code==404
            assert (await client.put('/api/v1/bills/budgets',headers=headers,json={'month':'2026-09','amount_fen':10000,'user_id':'U2'})).status_code==422
            assert (await client.put('/api/v1/bills/budgets',headers=headers,json={'month':'2026-09','amount_fen':1.5})).status_code==422
            response=await client.put('/api/v1/bills/budgets',headers=headers,json={'month':'2026-09','amount_fen':10000})
            assert response.status_code==200,response.text
            budget=(await client.get('/api/v1/bills/budgets?month=2026-09',headers=headers)).json()
            assert budget['items'][0]['status']=='exceeded'
            assert (await client.put('/api/v1/bills/budgets',headers=headers,json={'month':'2026-13','amount_fen':10000})).status_code==422
            response=await client.post('/api/v1/bills/recurring/plans',headers=headers,json={'name':'年费','account_id':'A1','kind':'expense','frequency':'YEARLY','amount_fen':10000,'anchor_at':'2026-10-25T12:00:00+08:00'})
            assert response.status_code==200,response.text
            assert (await client.get('/api/v1/bills/recurring',headers=headers)).json()['items'][0]['source']=='manual'
            assert (await client.post('/api/v1/bills/recurring/plans',headers=headers,json={'name':'bad','account_id':'A3','kind':'expense','frequency':'WEEKLY','amount_fen':1,'anchor_at':'2026-10-25T12:00:00+08:00'})).status_code==404
            assert (await client.get('/api/v1/bills/merchant-history',headers=headers)).status_code==200
        context=ToolContext('U1',db,PageContext(page='insights'))
        for name,args in [('list_recurring_records',{}),('get_budget_status',{'month':'2026-09'}),('list_forecast_reviews',{})]:
            result=await execute_tool({'function':{'name':name,'arguments':json.dumps(args)}},context)
            assert 'error' not in result,result
            json.dumps(result) # date fields in tool results must be safe for the next model turn
    finally:app.dependency_overrides.pop(get_db,None)
