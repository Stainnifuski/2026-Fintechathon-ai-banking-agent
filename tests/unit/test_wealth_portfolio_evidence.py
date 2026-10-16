"""证据消费与受控当前SKU绑定；仅隔离事实，不冒称真实用户通过。"""
from copy import deepcopy
import json
import socket
import pytest
from backend.app.schemas.wealth_portfolio_data import RawReference, EvidenceRecord
from backend.app.services.orchestration.wealth_portfolio_data import (reference,apply_evidence,missing,
    digest,save,read,utc)
from backend.app.services.orchestration.wealth_portfolio_evidence import register,current_product,product_content,derive
from backend.app.domain.wealth_portfolio_candidates import reliable_family,ProductIndex,pipeline
from backend.app.domain.wealth_portfolio_eligibility import qualify_purchase
from backend.app.schemas.wealth_portfolio_candidates import CandidateFilter
from tests.fixtures.wealth_portfolio_candidates_v1 import fixture_pool,NOW
from tests.unit.test_wealth_portfolio_contract_alignment import current_simulation
from tests.unit.test_wealth_portfolio_data import evidence


def test_unknown_publication_not_fabricated_and_registration_atomic(tmp_path):
    record=evidence(tmp_path);record['published_on']=None
    path=tmp_path/'config/wealth/portfolio_evidence_manifest.json';path.parent.mkdir(parents=True)
    manifest={'schema_version':'portfolio-evidence-v1','authority_sources':{'seller.test':'seller'},'records':[]}
    path.write_text(json.dumps(manifest))
    assert register(tmp_path,[record],{'000001','000002'})['added']==1
    first=path.read_bytes()
    assert register(tmp_path,[record],{'000001','000002'})['added']==0 and first==path.read_bytes()
    changed=deepcopy(record);changed['excerpt']='not present 000001 000002'
    with pytest.raises(ValueError):register(tmp_path,[changed],{'000001','000002'})
    assert path.read_bytes()==first
    bad=deepcopy(record);bad['family_id']='different'
    with pytest.raises(ValueError,match='CONTENT_CONFLICT'):register(tmp_path,[bad],{'000001','000002'})
    assert path.read_bytes()==first


def row(code='B013',risk=2,category='bond',kind='bank_wealth'):
    return {'product_id':code,'version':4,'name':'独立模拟SKU','kind':kind,'category':category,
        'product_risk_level':risk,'rules':{},'disclosure':{'version':3,'manager':'虚构管理机构'},
        'identity':{'product_id':code},'fund_family_id':'unsupported-common-name'}


def captured(tmp_path,raw):
    path=tmp_path/'.deps/wealth_portfolio_step08/capture.json';path.parent.mkdir(parents=True,exist_ok=True)
    capture={'schema_version':'portfolio-bank-observation-v1','source':'protected-local-bank',
        'url':'http://127.0.0.1:8010/api/v1/wealth/products','observed_at':NOW.isoformat(),
        'business_at':'2026-10-08T03:00:00Z','products':[raw]}
    path.write_bytes(json.dumps(capture).encode())
    ref=reference(tmp_path,path,'protected_bank_products',observed_at=NOW)
    return capture,ref


def test_current_v4_and_separate_disclosure_never_relabel_old_text(tmp_path):
    snap,_=fixture_pool(count=1);old=snap.products[0]
    cap,ref=captured(tmp_path,row());p=current_product(row(),ref,cap,old,'old-research-v1')
    assert (p.product_version,p.disclosure_version)==(4,3)
    assert p.current_binding['original_product_version']==1
    assert p.fields['strategy'].status=='missing' and not p.family_evidence
    assert p.fields['issuer_raw'].value=='虚构管理机构'
    assert reliable_family(p,NOW)==('bank-sku:B013',[])
    p2=current_product(row('B014'),ref,cap,None,'old')
    assert reliable_family(p2,NOW)[0]!=reliable_family(p,NOW)[0]
    snap.products=[p,p2];snap.as_of=NOW
    index=ProductIndex(snap)
    assert len(index.family_members['bank-sku:B013'])==1
    assert p.approved_trade_product is False


@pytest.mark.parametrize('risk,category,quick,status',[(1,'cash',False,'policy_mismatch'),
    (3,'mixed',False,'policy_mismatch'),(1,'cash',True,'pending')])
def test_bank_policy_no_principal_or_rating_fabrication(tmp_path,risk,category,quick,status):
    raw=row('B017',risk,category)
    if quick:raw['rules']['quick_redemption']={'daily_limit_fen':10000}
    cap,ref=captured(tmp_path,raw);p=current_product(raw,ref,cap,None,'old')
    assert p.scope_status==status and p.fields['formal_risk'].value==risk
    assert p.fields['principal_guarantee'].value is None and p.bucket is None


def test_current_content_same_version_change_or_unknown_rejected():
    p,facts,offer=current_simulation()
    p.current_binding={'source':'protected-local-bank-current-products','content_sha256':'a'*64}
    offer.current_simulation.product_content_sha256='a'*64
    assert qualify_purchase(p,facts,'fixture-user',NOW).purchase_eligible
    for value in (None,'b'*64):
        offer.current_simulation.product_content_sha256=value
        assert 'BANK_PRODUCT_CONTENT_MISMATCH' in qualify_purchase(p,facts,'fixture-user',NOW).reason_codes
    raw=row();changed=deepcopy(raw);changed['disclosure']['risk']='changed without version bump'
    assert digest(product_content(raw))!=digest(product_content(changed))


def test_new_evidence_consumed_and_conflict_kept(tmp_path):
    snap,_=fixture_pool(count=1);p=snap.products[0];p.source_kind='real';p.product_id='000001'
    p.fields['objective']=missing('unknown')
    payload=evidence(tmp_path);payload.update(kind='objective',codes=['000001'],share_classes={'000001':None},family_id=None,field_value='同一基金')
    record=EvidenceRecord.model_validate(payload);apply_evidence(p,[record],NOW)
    assert p.fields['objective'].value=='同一基金' and p.fields['objective'].status=='verified'
    changed=record.model_copy(update={'evidence_id':'other','field_value':'不同目标'})
    apply_evidence(p,[record,changed],NOW)
    assert p.fields['objective'].status=='conflict' and len(p.fields['objective'].observations)>=2


def test_unknown_risk_date_not_purchase_proof():
    snap,facts=fixture_pool(count=1);p=snap.products[0];p.source_kind='real'
    facts.mode='real';facts.assessment.source_kind='real'
    offer=facts.offers[p.product_id];offer.source_kind='real'
    e=p.family_evidence[0].model_copy(update={'kind':'risk','codes':[p.product_id],
        'share_classes':{p.product_id:'A'},'risk_level':2,'source_kind':'real','source_role':'seller',
        'channel':facts.channel,'family_id':None,'published_on':None,'applicable_from':None,
        'applicable_to':None,'applicability_reason':'not stated'})
    p.risk_evidence=[e];offer.risk_evidence_id=e.evidence_id
    decision=qualify_purchase(p,facts,'fixture-user',NOW)
    assert not decision.purchase_eligible and 'PRODUCT_RISK_TIME_UNPROVEN' in decision.reason_codes


def test_derive_offline_reproducible_old_readable_and_hash_changes(tmp_path,monkeypatch):
    snap,_=fixture_pool(count=1);snap.products=snap.products[:1];p=snap.products[0]
    p.source_kind='simulated';p.product_id='B013';p.fields.update({k:missing('unknown') for k in ('raw_risk','issuer_normalized','objective','benchmark','tracking_target')})
    snap.adapter_version='portfolio-data-v1:'+'a'*64;snap.version='1'*64
    directory=tmp_path/'.deps/wealth_portfolio_data/snapshots';save(snap,directory)
    path=tmp_path/'config/wealth/portfolio_evidence_manifest.json';path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'schema_version':'portfolio-evidence-v1','authority_sources':{},'records':[]}))
    cap,ref=captured(tmp_path,row())
    monkeypatch.setattr(socket,'socket',lambda *a,**k:(_ for _ in ()).throw(AssertionError('network')))
    a=derive(tmp_path,snap.version,ref.path,ref.sha256,NOW);b=derive(tmp_path,snap.version,ref.path,ref.sha256,NOW)
    assert a==b and a.external_requests==0
    save(a,directory);assert read(tmp_path,directory,a.version)==a and read(tmp_path,directory,snap.version).products[0].product_version==1
    changed=row();changed['disclosure']['strategy']='本产品主要投资于债券票息资产。'
    cap,ref2=captured(tmp_path,changed);c=derive(tmp_path,snap.version,ref2.path,ref2.sha256,NOW)
    assert c.version!=a.version and c.evidence_version!=a.evidence_version and c.products[0].fields['strategy'].value
    with pytest.raises(ValueError,match='FINGERPRINT'):derive(tmp_path,snap.version,ref.path,ref.sha256,NOW)
    # UTC仍是8日，北京已是9日；不把已公布的北京时间净值误当未来数据。
    changed.update(nav='1',nav_date='2026-10-09',available_at='2026-10-08T19:00:00Z')
    cap,ref2=captured(tmp_path,changed);cap['business_at']='2026-10-08T20:00:00Z'
    path=tmp_path/ref2.path;path.write_bytes(json.dumps(cap).encode());ref2=reference(tmp_path,path,'protected_bank_products',observed_at=NOW)
    assert derive(tmp_path,snap.version,ref2.path,ref2.sha256,NOW).daily[0].fields['unit_nav'].value=='1'


def test_simulated_bank_kind_not_misclassified_as_fund(tmp_path):
    raw=row('S049');cap,ref=captured(tmp_path,raw);p=current_product(raw,ref,cap,None,'old')
    snap,_=fixture_pool(count=1);snap.products=[p];snap.as_of=NOW;index=ProductIndex(snap)
    assert index.match(CandidateFilter(mode='category',category='bank_wealth'))==[p]
    assert index.match(CandidateFilter(mode='category',category='bond_fund'))==[]


def test_current_cash_mapping_keeps_raw_value_and_wrappers_pending(tmp_path):
    raw=row('S003',1,'cash','open_fund');cap,ref=captured(tmp_path,raw)
    p=current_product(raw,ref,cap,None,'old')
    assert p.raw_category=='cash' and p.fields['raw_category'].value=='cash'
    assert p.substantive_category=='money' and p.bucket=='cash_management'
    raw['category']='index';p=current_product(raw,ref,cap,None,'old')
    assert p.scope_status=='pending' and p.substantive_category is None


def test_category_record_does_not_overwrite_conflict_or_exclusion(tmp_path):
    snap,_=fixture_pool(count=1);p=snap.products[0];p.source_kind='real';p.product_id='000001'
    from tests.fixtures.wealth_portfolio_candidates_v1 import field
    p.fields['raw_category']=field('债券型','category')
    payload=evidence(tmp_path);payload.update(kind='category',codes=['000001'],share_classes={'000001':None},family_id=None,field_value='股票型')
    e=EvidenceRecord.model_validate(payload)
    apply_evidence(p,[e],NOW)
    assert p.fields['raw_category'].status=='conflict' and p.bucket is None and p.scope_status=='pending'
    p.scope_status='excluded';apply_evidence(p,[e],NOW)
    assert p.scope_status=='excluded'
