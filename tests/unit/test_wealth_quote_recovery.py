"""Bounded recovery of public market reads; stale execution remains blocked."""
import asyncio
from datetime import datetime,date,timezone
from types import SimpleNamespace
import pytest
from backend.app.services.bank import wealth_quotes as quotes
from backend.app.services.bank.wealth_provider import AKShareProvider
from backend.app.services.bank.transfer_service import BankError


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(quotes,'_cache',{})
    monkeypatch.setattr(quotes,'_locks',{})
    monkeypatch.setattr(quotes,'_limit',asyncio.Semaphore(3))
    clock=[100.0]
    monkeypatch.setattr(quotes.time,'monotonic',lambda:clock[0])
    return clock


def success():
    return {'ok':True,'records':[{'净值日期':'2026-10-20','单位净值':0.7018}],'finished_at':'2026-10-21T06:00:00+00:00'}


@pytest.mark.asyncio
async def test_transient_failure_retries_once_and_coalesces_same_code(monkeypatch,isolated):
    calls=[]
    def fetch(self,method,**kwargs):
        calls.append(kwargs['symbol'])
        return {'ok':False,'error_type':'PROVIDER_TIMEOUT'} if len(calls)==1 else success()
    monkeypatch.setattr(AKShareProvider,'fetch',fetch)
    a,b=await asyncio.gather(quotes.observation('010969'),quotes.observation('010969'))
    assert a==b and a['ok'] and a['attempts']==2
    assert a['latest_nav_date']=='2026-10-20' and len(calls)==2


@pytest.mark.asyncio
async def test_failure_expires_after_short_cooldown_not_sixty_seconds(monkeypatch,isolated):
    calls=[]
    def fetch(self,*args,**kwargs):
        calls.append(1)
        return {'ok':False,'error_type':'ConnectionError'} if len(calls)<=2 else success()
    monkeypatch.setattr(AKShareProvider,'fetch',fetch)
    failed=await quotes.observation('010969')
    assert not failed['ok'] and failed['attempts']==2
    assert await quotes.observation('010969')==failed and len(calls)==2
    isolated[0]+=3.1
    assert (await quotes.observation('010969'))['ok'] and len(calls)==3


@pytest.mark.asyncio
async def test_success_keeps_normal_cache_and_other_codes_are_independent(monkeypatch,isolated):
    calls=[]
    def fetch(self,*args,**kwargs):calls.append(kwargs['symbol']);return success()
    monkeypatch.setattr(AKShareProvider,'fetch',fetch)
    await quotes.observation('010969');isolated[0]+=4
    await quotes.observation('010969');await quotes.observation('005876')
    assert calls==['010969','005876']


@pytest.mark.asyncio
async def test_permanent_schema_failure_does_not_retry_or_fake_a_quote(monkeypatch,isolated):
    calls=[]
    def fetch(self,*args,**kwargs):calls.append(1);return {**success(),'records':[{'wrong_schema':1}]}
    monkeypatch.setattr(AKShareProvider,'fetch',fetch)
    result=await quotes.observation('010969')
    assert not result['ok'] and result['error']=='HISTORY_SCHEMA_DRIFT' and len(calls)==1


def test_stale_nav_still_blocks_with_actionable_network_reason():
    from backend.app.domain.wealth_paper_policy import PRICING_MODE
    from backend.app.domain.wealth import CALENDAR_VERSION
    version=SimpleNamespace(product_id='010969',rules={'pricing_mode':PRICING_MODE,'calendar_version':CALENDAR_VERSION})
    price=SimpleNamespace(nav_date=date(2026,9,30))
    with pytest.raises(BankError) as error:
        quotes.require_trade_nav(version,price,datetime(2026,10,21,6,tzinfo=timezone.utc),{'ok':False,'error':'PROVIDER_TIMEOUT','attempts':2})
    assert error.value.code=='NAV_UPDATE_REQUIRED'
    assert '本次联网查询超时，已自动重试' in error.value.message
    assert '2026-10-20' in error.value.message


@pytest.mark.asyncio
async def test_precheck_market_retry_has_read_timeout_and_not_unknown_trade_semantics():
    from backend.app.integrations.wealth_bank_api import HttpWealthBankApi
    from backend.app.integrations.bank_api import BankResponseUnknown,BankApiError
    class Down(HttpWealthBankApi):
        async def _request(self,method,path,**kwargs):
            assert kwargs['timeout']==30.0
            raise BankResponseUnknown('test unavailable','BANK_UNREACHABLE')
    bank=Down(base_url='http://isolated.invalid',token='isolated-test-only-token')
    with pytest.raises(BankApiError) as error:await bank.call('POST','/precheck',{})
    assert error.value.code=='BANK_READ_UNAVAILABLE'
