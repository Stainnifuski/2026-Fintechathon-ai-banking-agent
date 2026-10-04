import pytest
from tests.integration.test_wealth_bank import store,http_store,NOW
from datetime import datetime


@pytest.mark.asyncio
async def test_detail_is_published_only_and_supports_three_ranges(store,http_store):
    client,_,_=http_store
    for period in ['1m','3m','1y']:
        response=await client.get('/api/v1/wealth/products/MF002/detail',params={'period':period})
        assert response.status_code==200,response.text
        result=response.json();chart=result['chart']
        assert chart['period']==period and set(chart['metrics'])=={'nav_change','max_drawdown','volatility'}
        assert all(p['date']<'2026-09-30' for p in chart['points'])
        assert result['simulation']
    bad=await client.get('/api/v1/wealth/products/MF002/detail?period=all')
    assert bad.status_code==422
    absent=await client.get('/api/v1/wealth/products/OTHER/detail')
    assert absent.status_code==404


@pytest.mark.asyncio
async def test_money_yield_detail_never_fabricates_nav_or_future_report(store,http_store):
    from backend.app.services.bank import wealth_research as research
    from backend.app.models.wealth_research import WealthContextORM
    client,_,_=http_store
    async with store() as db:
        # Explicit recorded schema fixture, not a claim of a new AKShare fetch.
        payload={'schema_version':1,'dataset_id':'money-detail-fixture','dataset_version':'1','mode':'snapshot',
            'products':[{'product_id':'R000001','name':'Recorded cash yield fixture','source':'akshare',
                'category':'cash','rules_complete':False,'history':[],
                'money_yield':[{'date':'2026-09-29','available_at':'2026-09-29T10:00:00+00:00',
                    'income_per_10000':'0.40','seven_day_annualized_percent':'1.50'},
                    {'date':'2026-09-30','available_at':'2026-09-30T10:00:00+00:00',
                    'income_per_10000':'0.50','seven_day_annualized_percent':'1.60'}]}]}
        imported=await research.import_dataset(db,payload);assert imported['ok'],imported
        context=await research.initialize(db,'U1',dataset_key=imported['dataset_key'])
        scope=await db.get(WealthContextORM,context['context_id'])
        scope.data={**scope.data,'business_at':NOW.isoformat()};await db.commit()
        await research.select_context(db,'U1',scope.context_id,1)
    result=(await client.get('/api/v1/wealth/products/R000001/detail')).json()
    assert result['chart'] is None and 'latest_nav' not in result
    assert len(result['money_yield'])==1 and result['money_yield'][0]['date']=='2026-09-29'
