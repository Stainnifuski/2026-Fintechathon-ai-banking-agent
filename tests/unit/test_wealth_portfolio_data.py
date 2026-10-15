"""事实边界验收：新样本独立，旧F01—F24不写入。"""
import asyncio
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
from backend.app.schemas.wealth_portfolio_data import RawReference, EvidenceRecord
from backend.app.integrations.wealth_portfolio_data import records, clean, field_fact, observation, validate_evidence, read_raw
from backend.app.services.orchestration.wealth_portfolio_data import (real_products, simulated_product, simulated_daily,
    build,save,read,read_asset_model_facts,utc,reference)
from backend.app.services.orchestration.wealth_portfolio import FixtureSnapshotSource,SnapshotError
from scripts.generate_wealth_portfolio_fixtures import generate

NOW=utc('2026-10-09T05:45:00Z')

def raw_file(tmp, function='fund_overview_em',kwargs=None,rows=None):
    folder=tmp/'.deps/a1_collection/raw';folder.mkdir(parents=True,exist_ok=True)
    p=folder/'sample.json.gz';value={'function':function,'kwargs':kwargs or {'symbol':'000001'},'observed_at':'2026-10-07T05:00:00Z','records':rows or [{'基金管理人':'机构甲','基金经理人':'个人乙','投资目标':'目标正文','跟踪标的':'该基金无跟踪标的'}]}
    with gzip.open(p,'wt',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False)
    return reference(tmp,p,function,value['kwargs'],utc(value['observed_at']))

def universe(code='000001',status='verified',base='mixed'):
    return {'code':code,'name':'名称A','raw_type':'混合型','base_type':base,'classification_status':status,'basis':'saved explicit','forms':'{}'}

@pytest.mark.parametrize('value',[None,'NA','nan','---',float('inf'),'该基金无跟踪标的'])
def test_placeholders(value):assert clean(value) is None

def test_zero_and_both_structures():
    assert clean(0)==0
    assert records([{'a':0}])==records({'products':[{'a':0}]})
    assert records({'records':[{'item':'a','value':0},{'item':'a','value':1}]})==[{'a':0},{'a':1}]
    assert records({'records':[{'字段':'a','值':0}]})==[{'a':0}]
    with pytest.raises(ValueError):records({'products':None})

def test_unknown_excluded_issuer_manager_and_text_union(tmp_path):
    ref=raw_file(tmp_path)
    products,daily=real_products([universe(),universe('000002','unresolved',None),universe('000003','excluded',None)],[ref],tmp_path,NOW,[])
    p=products[0]; assert p.fields['issuer_raw'].value=='机构甲'
    assert p.fields['tracking_target'].value is None
    assert p.fields['family_id'].value is None and not p.risk_evidence
    assert len(products)==3 and products[1].bucket is None and products[2].scope_reason
    assert daily[0].fields['unit_nav'].value is None

def test_conflicts_not_latest_or_lowest(tmp_path):
    ref=raw_file(tmp_path)
    fact=field_fact([observation('机构甲','基金管理人',ref),observation('机构乙','基金公司',ref)])
    assert fact.status=='conflict' and fact.value is None and len(fact.observations)==2

@pytest.mark.parametrize('risk,category,quick,scope',[(3,'mixed',False,'policy_mismatch'),(1,'cash',False,'policy_mismatch'),(2,'bond',False,'verified'),(1,'cash',True,'pending')])
def test_simulated_policy_keeps_original(tmp_path,risk,category,quick,scope):
    ref=raw_file(tmp_path);p=simulated_product({'product_id':'B017','risk_level':risk,'category':category,'rules':{'quick_redemption':{'window':'test'}} if quick else {},'rules_complete':True},ref,True)
    assert p.fields['raw_risk'].value==risk and p.scope_status==scope
    assert p.approved_trade_product is False

def test_simulated_no_future_nav(tmp_path):
    row={'product_id':'S001','history':[{'date':'2026-10-10','available_at':'2026-10-10T00:00:00Z','published_at':'2026-10-10T00:00:00Z','nav':'2'}]}
    assert simulated_daily(row,raw_file(tmp_path),NOW).fields['unit_nav'].value is None

def evidence(tmp):
    text='2026-10-01 同一基金份额类别 000001 A 000002 C 风险等级R3'
    path=tmp/'.deps/wealth_portfolio_data/public/e.txt';path.parent.mkdir(parents=True);path.write_text(text,encoding='utf8')
    ref=reference(tmp,path,'public_document',{'url':'https://seller.test/document'})
    return dict(evidence_id='fixture-proof',source_kind='real',kind='family',source_role='seller',url='https://seller.test/document',codes=['000001','000002'],share_classes={'000001':'A','000002':'C'},channel='seller.test',published_on='2026-10-01',applicable_from=None,applicable_to=None,applicability_reason='not stated',observed_at='2026-10-09T00:00:00Z',reference=ref.model_dump(mode='json'),excerpt=text,family_id='F1')

def test_verified_registration_and_simulation_isolation(tmp_path):
    e=evidence(tmp_path);registered=validate_evidence(tmp_path,e,{'seller.test':'seller'})
    assert registered.family_id=='F1'
    e['source_kind']='simulated';e['source_role']='simulation'
    from backend.app.integrations.wealth_portfolio_data import registered_for
    assert registered_for('000001','real',[EvidenceRecord.model_validate(e)],NOW)==[]

@pytest.mark.parametrize('change',[{'channel':''},{'codes':['999999']},{'published_on':None,'applicability_reason':None},{'source_role':'simulation'},{'share_classes':{'000001':'B','000002':'C'}},{'url':'https://unapproved.test'}])
def test_bad_evidence_rejected(tmp_path,change):
    e=evidence(tmp_path);e.update(change)
    with pytest.raises(ValueError):validate_evidence(tmp_path,e,{'seller.test':'seller'})

def test_hash_path_and_binding_protected(tmp_path):
    ref=raw_file(tmp_path);assert read_raw(tmp_path,ref)['records']
    with pytest.raises(ValueError,match='FINGERPRINT'):read_raw(tmp_path,ref.model_copy(update={'sha256':'0'*64}))
    with pytest.raises(ValueError,match='PATH_DENIED'):read_raw(tmp_path,ref.model_copy(update={'path':'../outside.json'}))
    with pytest.raises(ValueError,match='BINDING'):read_raw(tmp_path,ref.model_copy(update={'kwargs':{'symbol':'999999'}}))

def setup_inventory(tmp):
    import sqlite3
    ref=raw_file(tmp)
    with sqlite3.connect(tmp/'.deps/a1_collection/progress.sqlite') as db:
        db.executescript('CREATE TABLE universe(code,name,raw_type,base_type,classification_status,basis,inception,forms); CREATE TABLE jobs(id,function,kwargs,raw_path,status);')
        row=universe();db.execute('INSERT INTO universe VALUES(?,?,?,?,?,?,?,?)',[row[k] for k in ('code','name','raw_type','base_type','classification_status','basis')]+[None,row['forms']])
        db.execute('INSERT INTO jobs VALUES(1,?,?,?,"success")',(ref.function,json.dumps(ref.kwargs),'raw/sample.json.gz'))
    path=tmp/'config/wealth';(path/'data').mkdir(parents=True)
    (path/'portfolio_evidence_manifest.json').write_text(json.dumps({'schema_version':'portfolio-evidence-v1','version':'1','authority_sources':{},'records':[]}))
    (path/'data/bank_products.json').write_text('[]');(path/'data/synthetic.json').write_text('{"products":[]}')

def test_deterministic_network_free_and_change_version(tmp_path,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'socket',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('network')))
    setup_inventory(tmp_path);a=build(tmp_path,NOW);b=build(tmp_path,NOW)
    assert a.model_dump()==b.model_dump() and a.version==b.version
    directory=tmp_path/'.deps/wealth_portfolio_data/snapshots';save(a,directory)
    assert read(tmp_path,directory,a.version)==a
    path=tmp_path/'config/wealth/portfolio_evidence_manifest.json';j=json.loads(path.read_text());j['version']='2';path.write_text(json.dumps(j))
    assert build(tmp_path,NOW).version!=a.version
    p=tmp_path/'.deps/a1_collection/raw/sample.json.gz'
    with gzip.open(p,'rt') as f:j=json.load(f)
    j['records'][0]['投资目标']='changed'
    with gzip.open(p,'wt') as f:json.dump(j,f)
    assert build(tmp_path,NOW).fixed_version!=a.fixed_version
    saved=directory/(a.version+'.json');saved.write_text('{}')
    with pytest.raises(ValueError,match='FINGERPRINT'):read(tmp_path,directory,a.version)

def test_stale_status_unchanged_by_rebuild(tmp_path):
    ref=raw_file(tmp_path,function='fund_purchase_em',kwargs={},rows=[{'基金代码':'000001','申购状态':'开放申购','购买起点':0}])
    _,a=real_products([universe()],[ref],tmp_path,NOW,[])
    _,b=real_products([universe()],[ref],tmp_path,NOW,[])
    assert a==b and a[0].freshness=='stale' and a[0].fields['purchase_minimum'].value==0
    assert a[0].fields['purchase_minimum'].observations[0].unit=='source_unit_unverified'

@pytest.mark.parametrize('fixture',['F08','F18','F10'])
def test_reuse_asset_builder(fixture):
    bundle=generate()[fixture]['input']['bundle']
    facts=asyncio.run(read_asset_model_facts(FixtureSnapshotSource(bundle),'fixture-user'))
    assert facts.snapshot.basis_status=='fixture_only'
    assert facts.snapshot.total_assets_fen==(None if fixture=='F10' else 1000000)
    assert 'version' in facts.assessment_provenance['missing_fields']

def test_unclassified_value_stays_T_and_disables_eta():
    bundle=generate()['F18']['input']['bundle']
    for lot in bundle['lots']:lot['category']='unknown'
    facts=asyncio.run(read_asset_model_facts(FixtureSnapshotSource(bundle),'fixture-user'))
    assert facts.snapshot.total_assets_fen==1000000 and facts.snapshot.valuation_complete
    assert facts.disable_eta and not facts.classification_complete

def test_missing_formal_source_and_provenance_mismatch():
    with pytest.raises(SnapshotError) as e:asyncio.run(read_asset_model_facts(None,'u'))
    assert e.value.status_code==503
    bundle=generate()['F18']['input']['bundle'];bundle['assessment_provenance']={'read_at':'2026-10-09T00:00:00Z'}
    with pytest.raises(SnapshotError,match='time mismatch'):asyncio.run(read_asset_model_facts(FixtureSnapshotSource(bundle),'fixture-user'))

def test_saved_money_market_mapping(tmp_path):
    products,_=real_products([universe(base='money_market')],[],tmp_path,NOW,[])
    assert products[0].scope_status=='verified' and products[0].bucket=='cash_management'

def test_registered_risk_stays_per_channel(tmp_path):
    payload=evidence(tmp_path);payload.update(kind='risk',codes=['000001'],share_classes={'000001':'A'},family_id=None,risk_level=3)
    record=validate_evidence(tmp_path,payload,{'seller.test':'seller'})
    products,_=real_products([universe()],[],tmp_path,NOW,[record])
    assert len(products[0].risk_evidence)==1 and products[0].fields['formal_risk'].value is None
    assert products[0].risk_evidence[0].applicable_from is None

def test_model_product_policy_unknown_retains_assets():
    bundle=generate()['F18']['input']['bundle']
    facts=asyncio.run(read_asset_model_facts(FixtureSnapshotSource(bundle),'fixture-user',[]))
    assert facts.snapshot.total_assets_fen==1000000 and facts.disable_eta

def test_actual_response_entity_mismatch_rejected(tmp_path):
    ref=raw_file(tmp_path,rows=[{'基金代码':'999999','基金管理人':'wrong'}])
    with pytest.raises(ValueError,match='ENTITY_MISMATCH'):real_products([universe()],[ref],tmp_path,NOW,[])

@pytest.mark.parametrize('value',[False,1.0,-1])
def test_fen_unit_strict(tmp_path,value):
    with pytest.raises(ValueError):observation(value,'min',raw_file(tmp_path),unit='fen')

def test_evidence_code_not_in_directory(tmp_path):
    with pytest.raises(ValueError,match='NOT_IN_DIRECTORY'):validate_evidence(tmp_path,evidence(tmp_path),{'seller.test':'seller'},{'999999'})

def test_same_channel_conflicting_risks_preserved(tmp_path):
    payload=evidence(tmp_path);payload.update(kind='risk',codes=['000001'],share_classes={'000001':'A'},family_id=None,risk_level=3)
    first=validate_evidence(tmp_path,payload,{'seller.test':'seller'})
    second=first.model_copy(update={'risk_level':2,'evidence_id':'different_observation'})
    products,_=real_products([universe()],[],tmp_path,NOW,[first,second])
    assert len(products[0].risk_evidence)==2 and products[0].fields['formal_risk'].status=='conflict'

def test_purchase_nav_disambiguation_and_saved_year(tmp_path):
    rows=[{'基金代码':'000001','最新净值/万份收益':1.208,'最新净值/万份收益-报告时间':'09-30'},
          {'基金代码':'000002','最新净值/万份收益':0.423,'最新净值/万份收益-报告时间':'09-30'}]
    ref=raw_file(tmp_path,function='fund_purchase_em',kwargs={'scope':'all'},rows=rows)
    _,daily=real_products([universe(base='bond'),universe('000002',base='money_market')],[ref],tmp_path,NOW,[])
    nav=daily[0].fields['unit_nav']
    assert nav.value=='1.208' and str(nav.observations[0].as_of)=='2026-09-30'
    assert 'year interpreted' in nav.observations[0].reason
    assert daily[1].fields['unit_nav'].value is None


def test_step09_public_nav_exact_code_hash_and_time(tmp_path):
    from datetime import date
    folder=tmp_path/'.deps/wealth_portfolio_step09/public';folder.mkdir(parents=True)
    path=folder/'history.raw'
    path.write_text('var Data_netWorthTrend = [{"x":1790697600000,"y":1.208}]; var other="not executed";',encoding='utf8')
    ref=reference(tmp_path,path,'public_unit_nav_history',{'symbol':'000015','indicator':'单位净值走势','url':'https://fund.eastmoney.com/pingzhongdata/000015.js'},NOW)
    _,daily=real_products([universe('000015',base='bond')],[ref],tmp_path,NOW,[])
    assert daily[0].fields['unit_nav'].value=='1.208'
    assert daily[0].fields['unit_nav'].observations[0].as_of==date(2026,9,30)
    with pytest.raises(ValueError,match='BINDING'):
        read_raw(tmp_path,ref.model_copy(update={'kwargs':{**ref.kwargs,'symbol':'000016'}}))
    with pytest.raises(ValueError,match='FINGERPRINT'):
        read_raw(tmp_path,ref.model_copy(update={'sha256':'0'*64}))
