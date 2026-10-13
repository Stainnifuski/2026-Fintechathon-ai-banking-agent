"""Existing immutable simulation material is read only and version-specific."""
import pytest
from types import SimpleNamespace
from copy import deepcopy
from datetime import datetime,timezone
from backend.app.services.bank.wealth_material import current_material
from backend.app.models.wealth_research import WealthDatasetORM,WealthResearchProductORM
from tests.integration.test_wealth_bank import store,http_store

@pytest.mark.asyncio
async def test_material_requires_exact_original_version_rules_and_grade(store):
    raw={'product_id':'S001','product_version':1,'risk_level':2,'category':'bond','kind':'open_fund','rules':{
        'dataset_id':'test-material','dataset_version':'v1'},
        'share_class':'C','fund_family_id':'declared-family','benchmark':'虚构bond基准'}
    async with store() as db:
        db.add(WealthDatasetORM(dataset_key='test-material:v1',dataset_id='test-material',version='v1',mode='synthetic',manifest={'fingerprint':'a'*64},ingested_at=datetime.now(timezone.utc)))
        await db.flush()
        db.add(WealthResearchProductORM(record_id='test-material:v1:S001',dataset_key='test-material:v1',product_id='S001',data=raw,history=[]));await db.commit()
        version=SimpleNamespace(product_id='S001',version=1,risk_level=2,category='bond',kind='open_fund',rules=raw['rules'])
        material=await current_material(db,version)
        assert material['share_class']=='C' and material['fund_family_id']=='declared-family'
        assert 'investment_strategy' not in material
        for field,bad in [('version',2),('risk_level',1),('category','mixed'),('kind','bank_wealth'),('rules',{**raw['rules'],'purchase_paused':True})]:
            changed=deepcopy(version);setattr(changed,field,bad)
            assert await current_material(db,changed)=={}

def test_named_benchmark_not_sufficient_material_and_declared_family_is_not_two_risks(tmp_path):
    from tests.unit.test_wealth_portfolio_evidence import row,captured
    from backend.app.services.orchestration.wealth_portfolio_evidence import current_product,bind_current_families
    from backend.app.domain.wealth_portfolio_candidates import reliable_family,sourced_value
    raw=row('S001',2,'bond','open_fund');raw.update(share_class='A',benchmark='虚构bond基准',dataset_key='fixed:v1',fund_family_id='SF001')
    raw['material_origin']={'source':'immutable_project_simulation_dataset','dataset_key':'fixed:v1','dataset_fingerprint':'a'*64,'product_id':'S001','product_version':4,'fields':['share_class','fund_family_id','benchmark']}
    cap,ref=captured(tmp_path,raw);product=current_product(raw,ref,cap,None,'old')
    assert product.share_class=='A'
    other=deepcopy(raw);other['product_id']='S002';other['share_class']='C';other['material_origin']={**other['material_origin'],'product_id':'S002'}
    second=current_product(other,ref,cap,None,'old')
    bind_current_families([product,second],[raw,other],ref)
    assert reliable_family(product,ref.observed_at)[0]=='SF001' and reliable_family(second,ref.observed_at)[0]=='SF001'
    assert product.family_evidence[0].codes==['S001','S002']
    from backend.app.schemas.wealth_portfolio_data import ProductFact
    ProductFact.model_validate(product.model_dump(mode='json'))
    assert sourced_value(product,'benchmark')[0] is None
    assert product.fields['benchmark_name_only'].value=='虚构bond基准'
    assert product.fields['objective'].value is None and product.fields['strategy'].value is None
