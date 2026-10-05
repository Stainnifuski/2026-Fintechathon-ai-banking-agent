"""Latest 2026-10-04 scope: amount first, one confirmation, holdings redemption.

Opt in WEALTH_WORKSPACE_LIVE=1 and select sqlite for one real two-turn model flow.
All financial operations and preloaded redemption holdings are isolated fixtures.
"""
import os
from uuid import uuid4
import pytest
from sqlalchemy import select
from tests.integration.test_wealth_bank import store,http_store,assess,authorize
from tests.integration.test_wealth_workspace_lifecycle import facts
from backend.app.services.ai import wealth_dialogue,wealth_model
from backend.app.domain.wealth_requirements import SemanticFrame
from backend.app.schemas.wealth import InvestmentCommand
from backend.app.services.bank import wealth_service as bank,wealth_research as research
from backend.app.domain.wealth_fixed_data import bank_products
from backend.app.models.wealth_research import WealthContextORM
from backend.app.models.wealth import WealthAuthorizationORM


@pytest.mark.asyncio
async def test_fixed_workspace_mainline(store,http_store,monkeypatch):
    client,_,_=http_store
    live=os.getenv('WEALTH_WORKSPACE_LIVE')=='1'
    monkeypatch.setenv('WEALTH_NLU_MODE','model')
    monkeypatch.setenv('WEALTH_DEMO_VERIFICATION_ENABLED','true')
    if not live:
        async def understand(text,context):
            amount=100000 if text=='我要投资1000元' else 200000
            return wealth_model.Intent(intent='allocate',semantic=SemanticFrame.model_validate({'patches':[
                {'field':'investment_budget','op':'set','quote':text,'budget':{'mode':'fixed','amount_fen':amount}}]})),{'live':False,'provider':'deterministic-semantic-fixture'}
        monkeypatch.setattr(wealth_dialogue,'understand',understand)
    async with store() as db:
        products=bank_products()
        for product in products:product['rules'].update(dataset_id='workspace-mainline',dataset_version='fixed-20261003')
        imported=await research.import_dataset(db,{'schema_version':1,'dataset_id':'workspace-mainline',
            'dataset_version':'fixed-20261003','mode':'snapshot','products':products})
        context=await research.initialize(db,'U1',dataset_key=imported['dataset_key'])
        ctx=await db.get(WealthContextORM,context['context_id'])
        ctx.data={**ctx.data,'business_at':'2026-09-29T14:00:00+08:00'}
        await db.commit();await research.select_context(db,'U1',ctx.context_id,1);await assess(db)
        # Precondition, not a user step: prepare settled redeemable cash-management holdings.
        opening=InvestmentCommand(operation_id='fixture-preloaded-holding',direction='purchase',product_id='B017',
            product_version=1,disclosure_version=1,account_id=context['account_id'],amount_fen=200000)
        await bank.accept_many(db,'U1',[(opening,await authorize(db,opening))])
        ctx=await db.get(WealthContextORM,context['context_id'])
        ctx.data={**ctx.data,'business_at':'2026-10-12T10:00:00+08:00'};await db.commit()
        assert (await bank.settle(db,opening.operation_id))['status']=='SETTLED'
        initial=await facts(db)
    session=(await client.post('/api/v1/wealth/sessions',json={})).json()
    async def send(text):
        response=await client.post('/api/v1/wealth/messages',json={'message':text,'client_message_id':uuid4().hex,**session})
        assert response.status_code==200,response.text
        reply=response.json();session.update(session_id=reply['session_id'],version=reply['version'])
        assert reply['kind']!='model_unavailable',reply
        return reply
    try:
        first=await send('我要投资1000元');assert first['kind']=='plan',first
        assert sum(i['command']['amount_fen'] for i in first['plan']['items'])==100000
        if live:assert first['model_evidence']['live']
        async with store() as db:assert await facts(db)==initial
        chart=await client.get('/api/v1/wealth/products/'+first['plan']['items'][0]['command']['product_id']+'/detail?period=1m')
        assert chart.status_code==200,chart.text
        before_preview=await client.post('/api/v1/wealth/plans/'+first['plan']['plan_id']+'/preview',json={'version':first['plan']['version']})
        assert before_preview.status_code==200,before_preview.text
        second=await send('把投资金额改为2000元');assert second['kind']=='plan',second
        plan=second['plan'];assert plan['plan_id']==first['plan']['plan_id'] and plan['version']==first['plan']['version']+1
        assert sum(i['command']['amount_fen'] for i in plan['items'])==200000
        if live:assert second['model_evidence']['live']
        base='/api/v1/wealth/plans/'+plan['plan_id'];payload={'version':plan['version']}
        views=(await client.post(base+'/preview',json=payload)).json()
        confirmed=await client.post(base+'/confirm',json={**payload,'items':[{'authorization_id':v['authorization_id'],
            'digest':v['digest'],'disclosure_version':v['command']['disclosure_version'],'accepted':True} for v in views['items']]})
        assert confirmed.status_code==200,confirmed.text
        assert (await client.post(base+'/verify/demo',json=payload)).status_code==200
        executed=await client.post(base+'/execute',json=payload);assert executed.status_code==200,executed.text
        assert all(i['status'] in {'ACCEPTED','PENDING_PRICING','PENDING_CASH','SETTLED'} for i in executed.json()['items'])
        assets=(await client.get('/api/v1/wealth/assets')).json()
        assert any(o['operation_id']==plan['items'][0]['command']['operation_id'] for o in assets['orders'])
        assert assets['lots'] and all('redeemable_units' in lot for lot in assets['lots'])
        # Only deterministic safety check adds a third turn; live verification stays bounded to two calls.
        if not live:
            async with store() as db:
                submitted=await facts(db)
                auth_before={a.authorization_id:a.status for a in await db.scalars(select(WealthAuthorizationORM))}
            third=await send('金额换成3000元')
            assert third['code']=='ADDITIONAL_INVESTMENT_CONFIRMATION'
            async with store() as db:
                assert await facts(db)==submitted
                assert {a.authorization_id:a.status for a in await db.scalars(select(WealthAuthorizationORM))}==auth_before
        async def redeem(mode):
            command=InvestmentCommand(operation_id='holding-'+mode,direction='redeem',product_id='B017',
                product_version=1,disclosure_version=1,account_id=context['account_id'],units='10.0000',
                **({'command_version':2,'redemption_mode':'quick'} if mode=='quick' else {}))
            response=await client.post('/api/v1/wealth/previews',json=command.model_dump());assert response.status_code==200,response.text
            view=response.json();path='/api/v1/wealth/previews/'+view['authorization_id']
            linked=await client.post('/api/v1/wealth/sessions/'+session['session_id']+'/preview-link',json={'authorization_id':view['authorization_id']})
            assert linked.status_code==200,linked.text
            assert (await client.post(path+'/confirm',json={'digest':view['digest'],'disclosure_version':1,'accepted':True})).status_code==200
            assert (await client.post(path+'/verify/demo',json={})).status_code==200
            response=await client.post(path+'/execute',json={});assert response.status_code==200,response.text
            return response.json()
        ordinary=await redeem('standard');assert ordinary['status']=='ACCEPTED'
        assets=(await client.get('/api/v1/wealth/assets')).json()
        assert any(float(lot['held_units'])==10 for lot in assets['lots'])
        cash=assets['cash'][0]['balance_fen']
        quick=await redeem('quick');assert quick['status']=='SETTLED'
        assets=(await client.get('/api/v1/wealth/assets')).json()
        assert assets['cash'][0]['balance_fen']==cash+quick['details']['actual']['cash_fen']
        async with store() as db:before_refresh=await facts(db)
        fresh=(await client.post('/api/v1/wealth/sessions',json={})).json()
        assert (await client.get('/api/v1/wealth/sessions/'+fresh['session_id'])).json()['history']==[]
        async with store() as db:assert await facts(db)==before_refresh
        print('workspace mainline: amount 1000->2000, unified purchase, standard pending and quick settled, fresh window financial unchanged; live='+str(live))
    finally:
        if live and wealth_model._client:
            await wealth_model._client.aclose();wealth_model._client=None
