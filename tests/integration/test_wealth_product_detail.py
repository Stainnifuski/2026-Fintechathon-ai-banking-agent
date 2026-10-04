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
