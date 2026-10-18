import json
from copy import deepcopy
from pathlib import Path
import pytest
from backend.app.services.bank.wealth_research import import_dataset

ROOT=Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
@pytest.mark.parametrize('prefix',['','R'])
async def test_excluded_fund_cannot_be_reintroduced_by_old_import(prefix):
    payload=json.loads((ROOT/'config/wealth/paper_products.json').read_bytes())
    excluded=json.loads((ROOT/'config/wealth/product_exclusions.json').read_bytes())['codes'][0]
    product=deepcopy(payload['products'][0]);product['product_id']=prefix+excluded
    payload['products']=[product]
    # Validation must return before attempting to use even a DB session.
    result=await import_dataset(None,payload)
    assert result['ok'] is False
    assert any(error['code']=='PRODUCT_PERMANENTLY_EXCLUDED' for error in result['errors'])


@pytest.mark.asyncio
async def test_missing_exclusion_policy_fails_closed(monkeypatch):
    payload=json.loads((ROOT/'config/wealth/paper_products.json').read_bytes())
    original=Path.read_bytes
    def read(path):
        if path.name=='product_exclusions.json':raise FileNotFoundError('isolated missing policy')
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',read)
    result=await import_dataset(None,payload)
    assert result=={'ok':False,'errors':[{'row':0,'code':'EXCLUSION_POLICY_UNAVAILABLE'}],'written':0}


def test_cached_clarification_options_do_not_keep_excluded_product_reference():
    from scripts.wealth_product_purge import scrub
    excluded=json.loads((ROOT/'config/wealth/product_exclusions.json').read_bytes())['codes'][0]
    cached={'required_fields':[{'options':[{'id':excluded+':4','label':'removed fund'},
        {'id':'000001:5','label':'retained fund'}]}]}
    cleaned=scrub(cached,{excluded})
    assert cleaned['required_fields'][0]['options']==[{'id':'000001:5','label':'retained fund'}]
