"""B5 recorded A adapter with real bank prepare, never a runtime demo provider."""
from copy import deepcopy
import pytest
from tests.integration.test_wealth_bank import store, http_store
from tests.integration.test_wealth_portfolio_prepare import setup, SEVEN
from tests.integration.test_wealth_actions import action, no_nlp

@pytest.mark.asyncio
async def test_snapshot_and_actions_revision_prepare_keep_personal_scope(store,http_store,monkeypatch):
    client,local,source,_=await setup(store,http_store,monkeypatch)
    await source.record(local,SEVEN)
    snapshot=await client.get('/api/v1/wealth/portfolio/snapshot')
    assert snapshot.status_code==200,snapshot.text
    assert snapshot.json()['snapshot_id']==source.plan['snapshot_id']
    async def plan(**kwargs):return deepcopy(source.plan)
    async def revise(**kwargs):
        selected=kwargs['params']['selection']
        assert selected['expected_version']==source.plan['version']
        assert selected['items'][0]['share_class'] is None
        source.plan['version']+=1
        return deepcopy(source.plan)
    http_store[2].state.wealth_action_handlers={'plan_portfolio':plan,'revise_portfolio':revise}
    result=await client.post('/api/v1/wealth/actions',json={**action('plan_portfolio',{}),'request_id':'structured-plan-01'})
    assert result.status_code==200,result.text
    planned=result.json()['data']
    selection=dict(portfolio_id=planned['portfolio_id'],snapshot_id=planned['snapshot_id'],context_id=planned['context_id'],expected_version=planned['version'],
        items=[{k:p[k] for k in ('product_id','product_version','share_class','target_ratio_total')} for p in planned['selected_positions']])
    result=await client.post('/api/v1/wealth/actions',json={**action('revise_portfolio',{'selection':selection}),'request_id':'structured-revise-01'})
    assert result.status_code==200,result.text
    revised=result.json()['data'];assert revised['version']==planned['version']+1
    prepared=await client.post('/api/v1/wealth/actions',json={**action('prepare_purchase',dict(portfolio_id=revised['portfolio_id'],expected_version=revised['version'],snapshot_id=revised['snapshot_id'])),'request_id':'structured-prepare-01'})
    assert prepared.status_code==200,prepared.text
    assert prepared.json()['data']['portfolio_version']==revised['version']
    assert len(prepared.json()['data']['actions'])==7
    facts=await local.assets('U1')
    assert not facts['orders'] and all(a['held_fen']==0 for a in facts['cash'])
    original=source.snapshot
    async def foreign(*args):return {**await original(*args),'context_id':'foreign'}
    monkeypatch.setattr(source,'snapshot',foreign)
    assert (await client.get('/api/v1/wealth/portfolio/snapshot')).status_code==409
    http_store[2].state.wealth_portfolio_source=None
    assert (await client.get('/api/v1/wealth/portfolio/snapshot')).status_code==503

@pytest.mark.asyncio
async def test_reprepare_revised_plan_preserves_structured_session(store,http_store,monkeypatch):
    client,local,source,_=await setup(store,http_store,monkeypatch)
    await source.record(local,SEVEN)
    def payload(number,**session):
        return {**action('prepare_purchase',dict(portfolio_id=source.plan['portfolio_id'],
            expected_version=source.plan['version'],snapshot_id=source.plan['snapshot_id']),**session),
            'request_id':f'structured-reprepare-{number}'}
    first=await client.post('/api/v1/wealth/actions',json=payload(1))
    assert first.status_code==200,first.text
    initial=first.json()
    source.plan['version']+=1
    request=payload(2,session_id=initial['session_id'],expected_session_version=initial['session_version'])
    second=await client.post('/api/v1/wealth/actions',json=request)
    assert second.status_code==200,second.text
    revised=second.json()
    assert revised['session_id']==initial['session_id']
    assert revised['session_version']==initial['session_version']+1
    assert revised['data']['execution_plan_id']==initial['data']['execution_plan_id']
    assert revised['data']['execution_plan_version']==initial['data']['execution_plan_version']+1
    assert (await client.post('/api/v1/wealth/actions',json=request)).json()==revised
    facts=await local.assets('U1')
    assert not facts['orders'] and all(a['held_fen']==0 for a in facts['cash'])
