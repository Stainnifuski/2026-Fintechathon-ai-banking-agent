"""R1/quick policy is not a principal or yield guarantee."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from backend.app.services.orchestration.wealth_portfolio_data import simulated_product,reference

ROOT=Path(__file__).resolve().parents[2]
FILE=ROOT/'config/wealth/portfolio_bank_supplement.json'

@pytest.mark.parametrize('code',['B017','B018'])
def test_cash_admission_without_principal_guarantee(code):
    row=next(p for p in json.loads(FILE.read_text(encoding='utf8'))['products'] if p['product_id']==code)
    ref=reference(ROOT,FILE,'simulation_config')
    product=simulated_product(row,ref,True)
    assert product.scope_status=='verified' and product.bucket=='cash_management'
    assert product.fields['formal_risk'].value==1
    assert product.fields['principal_guarantee'].value is None
    assert row['rules']['quick_redemption'] and row['rules_complete']
    from backend.app.domain.wealth_portfolio_candidates import reliable_family
    from datetime import datetime,timezone
    assert reliable_family(product,datetime.now(timezone.utc))==('exact-simulation-code:'+code,[])
    for change in ('risk','rules'):
        bad=deepcopy(row)
        if change=='risk':bad['risk_level']=4
        else:bad['rules_complete']=False
        product=simulated_product(bad,ref,True)
        assert product.scope_status!='verified' and product.bucket is None
