import httpx
import pytest
from backend.app.integrations.wealth_bank_api import HttpWealthBankApi

@pytest.mark.asyncio
async def test_only_explicit_reads_get_longer_wait_no_retry():
    client=object.__new__(HttpWealthBankApi);calls=[]
    async def request(method,path,**options):
        calls.append((path,options));return httpx.Response(200,json={})
    client._request=request
    for path in ('/catalog','/assets','/asset-summary','/contexts','/product-detail','/precheck','/orders'):
        await client.call('POST',path,{})
    assert len(calls)==7
    assert [options.get('timeout') for _,options in calls]==[30,30,30,30,15,None,None]
