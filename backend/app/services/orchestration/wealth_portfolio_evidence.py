"""A侧显式证据登记与离线当前SKU派生；不联网，不改银行配置/旧研究版本。"""
import hashlib
import json
from collections import Counter
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from ...integrations.wealth_collection_cache import read_protected_json
from ...integrations.wealth_portfolio_data import validate_evidence, observation, field_fact
from ...schemas.wealth_portfolio_data import EvidenceRecord, RawReference, DataSnapshot, DailyFact
from .wealth_portfolio_data import (read, canonical, digest, utc, reference, missing,
    apply_evidence, simulated_product, coverage, TEXT_FIELDS, ADAPTER_VERSION)


def product_content(row: dict) -> dict:
    """规则/披露/身份的内容绑定；净值和观察时刻另由当前业务时效检查。"""
    keys=('product_id','version','name','kind','category','product_risk_level','identity',
          'fund_family_id','dataset_key','rules','disclosure','share_class',
          'investment_objective','investment_strategy','benchmark','tracking_target','material_origin')
    return {key:row.get(key) for key in keys}


def register(root: Path, records: list[dict], expected_codes: set[str]) -> dict:
    path=root/'config/wealth/portfolio_evidence_manifest.json'
    manifest=json.loads(path.read_text(encoding='utf-8-sig'))
    if manifest.get('schema_version')!='portfolio-evidence-v1':raise ValueError('EVIDENCE_VERSION_UNSUPPORTED')
    validated=[validate_evidence(root,r,manifest['authority_sources'],expected_codes) for r in records]
    existing={r['evidence_id']:r for r in manifest['records']}
    if len(existing)!=len(manifest['records']):raise ValueError('DUPLICATE_EVIDENCE_ID')
    added=0
    for record in validated:
        value=record.model_dump(mode='json');old=existing.get(record.evidence_id)
        if old is not None and EvidenceRecord.model_validate(old).model_dump(mode='json')!=value:
            raise ValueError('EVIDENCE_ID_CONTENT_CONFLICT')
        if old is None:added+=1;existing[record.evidence_id]=value
    # 所有输入先验证，失败不写一半；相同输入不会改写版本或文件。
    if added:
        manifest['records']=[existing[k] for k in sorted(existing)]
        temp=path.with_suffix('.tmp');temp.write_bytes(canonical(manifest));temp.replace(path)
    return {'added':added,'total':len(existing),'evidence_version':digest(manifest),'external_requests':0}


def current_product(row: dict, ref: RawReference, capture: dict, old, base_version: str):
    from ...integrations.wealth_portfolio_bank_facts import native_product_evidence
    code=row['product_id'];bank=row.get('kind')=='bank_wealth'
    category=row.get('category')
    # 银行cash与真实基金money区分；仅明确open_fund现金类映射为货币基金。
    if not bank and row.get('kind')=='open_fund' and category=='cash':category='money'
    disclosure=row.get('disclosure',{});rules=row.get('rules',{});native=native_product_evidence(row)
    normalized={'product_id':code,'name':row.get('name'),'category':category,
        'risk_level':row.get('product_risk_level'),'product_version':row.get('version'),
        'disclosure_version':disclosure.get('version'),'share_class':row.get('share_class',rules.get('share_class')),
        'manager':disclosure.get('manager'),'rules':rules,'rules_complete':native['rules_complete'],
        'principal_guaranteed':row.get('principal_guaranteed',rules.get('principal_guaranteed'))}
    product=simulated_product(normalized,ref,bank)
    product.raw_category=row.get('category')
    product.fields['raw_category']=field_fact([observation(row.get('category'),'category',ref)],simulated=True)
    product.forms={'kind':row.get('kind'),'quick_redemption':bool(rules.get('quick_redemption'))}
    product.classification_basis='protected_current_bank_kind_category_and_original_risk; cash/open_fund->money'
    sources={'issuer_raw':'disclosure.manager','raw_category':'category','formal_risk':'product_risk_level',
        'raw_risk':'product_risk_level','product_version':'version','disclosure_version':'disclosure.version',
        'share_class':'rules.share_class','principal_guarantee':'principal_guaranteed|rules.principal_guaranteed'}
    for field in sources:
        fact=product.fields[field]
        for obs in fact.observations:obs.field=sources[field]
    # 旧文本/占位benchmark不迁移；只采用当前产品自身明示的正文。
    adopted={f:sources[f] for f in sources if product.fields[f].value is not None}
    for field,keys in {'objective':('investment_objective','objective'),
        'strategy':('investment_strategy','strategy'),'benchmark':('benchmark',),
        'tracking_target':('tracking_target',)}.items():
        observations=[]
        for container,prefix in ((row,''),(disclosure,'disclosure.')):
            for key in keys:
                value=container.get(key)
                # These saved labels name a synthetic series but define no
                # investment benchmark. Preserve the raw observation, not a
                # false sufficient investment-material claim.
                named_only = field == 'benchmark' and (value in {
                    '虚构money基准','虚构bond基准','虚构broad_index基准',
                    '虚构sector基准','虚构active基准','虚构bank_wealth基准',
                    '模拟因子基准；不采用说明书比较基准作为收益'})
                if named_only:
                    product.fields['benchmark_name_only']=field_fact([observation(value,prefix+key,ref)],simulated=True)
                if isinstance(value,str) and value.strip() and not named_only:
                    observations.append(observation(value,prefix+key,ref))
        product.fields[field]=field_fact(observations,simulated=True) if observations else missing('current_bank_text_missing; old_research_not_rebound')
        if observations:adopted[field]=[o.field for o in observations]
    # 银行SKU事实只证明自身，不能将SF...或名称相似视作多份额关系。
    product.fields['family_id']=missing('current_bank_family_relation_unproven; independent_exact_SKU')
    entity=EvidenceRecord(evidence_id='bank-sku:'+digest(product_content(row)),source_kind='simulated',
        kind='entity',source_role='simulation',url=capture['url'],codes=[code],
        share_classes={code:product.share_class},channel='local-competition-bank',published_on=None,
        applicable_from=None,applicable_to=None,applicability_reason='current protected simulation SKU observation; no publication date or cross-SKU relation',
        observed_at=ref.observed_at,reference=ref,excerpt=f'protected current product_id={code}; exact independent SKU',
        family_id='bank-sku:'+code,document_sha256=ref.sha256,location='products.'+code)
    product.identity_evidence=[entity]
    origin=row.get('material_origin',{})
    family=row.get('fund_family_id')
    if (origin.get('source')=='immutable_project_simulation_dataset'
        and origin.get('dataset_key')==row.get('dataset_key')
        and origin.get('product_id')==code and origin.get('product_version')==row.get('version')
        and origin.get('dataset_fingerprint') and 'fund_family_id' in origin.get('fields',[])
        and isinstance(family,str) and family):
        product.fields['family_id']=field_fact([observation(family,'fund_family_id',ref)],simulated=True)
    product.current_binding={'source':'protected-local-bank-current-products','source_kind':'simulated',
        'product_id':code,'share_class':product.share_class,'product_version':product.product_version,
        'disclosure_version':product.disclosure_version,'original_research_version':base_version,
        'original_research_entity':old.product_id if old else None,
        'original_product_version':old.product_version if old else None,
        'original_disclosure_version':old.disclosure_version if old else None,
        'adopted_fields':adopted,'content_sha256':digest(product_content(row)),
        'capture_sha256':ref.sha256,'observed_at':capture['observed_at'],'business_at':capture['business_at'],
        'current_sale_and_rules':native,'policy_status':product.scope_status,'policy_reason':product.scope_reason,
        'real_code_mapping':row.get('source_product_code'),
        'mapping_status':'explicit' if row.get('source_product_code') else 'unproven',
        'missing_fields':[f for f,v in product.fields.items() if v.status=='missing'],
        'conflict_fields':[f for f,v in product.fields.items() if v.status=='conflict']}
    return product


def bind_current_families(products, rows, ref):
    """Register explicit multi-code relationships from this whole bank capture.

    A single matching label is not proof of a second share or independent risk.
    """
    groups={};by_code={p.product_id:p for p in products}
    for row in rows:
        origin=row.get('material_origin',{});code=row['product_id'];p=by_code[code]
        family=p.fields.get('family_id')
        if (not family or not family.value or origin.get('source')!='immutable_project_simulation_dataset'
            or origin.get('dataset_key')!=row.get('dataset_key') or origin.get('product_id')!=code
            or origin.get('product_version')!=row['version'] or not origin.get('dataset_fingerprint')):
            continue
        key=(origin['dataset_key'],origin['dataset_fingerprint'],family.value)
        groups.setdefault(key,[]).append(p)
    for key,members in groups.items():
        if len(members)<2:continue
        codes=sorted(p.product_id for p in members);shares={p.product_id:p.share_class for p in members}
        payload=members[0].identity_evidence[0].model_dump(mode='json')
        payload.update(kind='family',evidence_id='bank-family:'+digest({'source':key,'codes':codes,'shares':shares}),
            family_id=key[2],codes=codes,share_classes=shares,
            excerpt='explicit immutable project simulation family '+str(key[2])+': '+', '.join(codes))
        evidence=EvidenceRecord.model_validate(payload)
        for p in members:p.family_evidence=[evidence.model_copy(deep=True)]


def derive(root: Path, base_version: str, bank_path: str, bank_sha256: str, as_of: datetime, *, allow_current_catalog: bool=False) -> DataSnapshot:
    root=root.resolve();base=read(root,root/'.deps/wealth_portfolio_data/snapshots',base_version)
    if as_of<base.as_of:raise ValueError('DERIVED_CUTOFF_BEFORE_BASE')
    capture=read_protected_json(root,bank_path,bank_sha256,'.deps/wealth_portfolio_step08')
    from urllib.parse import urlparse
    url=urlparse(capture.get('url',''))
    if (capture.get('schema_version')!='portfolio-bank-observation-v1' or capture.get('source')!='protected-local-bank'
        or url.scheme!='http' or url.hostname not in ('localhost','127.0.0.1','::1')
        or url.path!='/api/v1/wealth/products' or url.username or url.password or url.query or url.fragment):
        raise ValueError('BANK_CAPTURE_SOURCE_INVALID')
    observed=utc(capture['observed_at']);business=utc(capture['business_at'])
    if observed>as_of or business>observed:raise ValueError('BANK_CAPTURE_FUTURE')
    rows=capture['products'];ids=[r['product_id'] for r in rows]
    if len(set(ids))!=len(ids) or any(r.get('kind') not in ('bank_wealth','open_fund') for r in rows):raise ValueError('BANK_CAPTURE_IDENTITY_INVALID')
    ref=RawReference(path=bank_path,sha256=bank_sha256,function='protected_bank_products',kwargs={'url':capture['url']},observed_at=observed)
    manifest_path=root/'config/wealth/portfolio_evidence_manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    real_codes={p.product_id for p in base.products if p.source_kind=='real'}
    entries=[validate_evidence(root,r,manifest['authority_sources'],real_codes) for r in manifest['records']]
    products=[];daily=[d.model_copy(deep=True) for d in base.daily if d.source_kind=='real']
    previous={p.product_id:p for p in base.products}
    old_simulated={p.product_id for p in base.products if p.source_kind=='simulated'}
    if set(ids)!=old_simulated and not allow_current_catalog:
        raise ValueError('BANK_CAPTURE_CATALOG_SET_CHANGED')
    for old in base.products:
        if old.source_kind!='real':continue
        product=old.model_copy(deep=True);apply_evidence(product,entries,as_of);products.append(product)
    for row in sorted(rows,key=lambda r:r['product_id']):
        products.append(current_product(row,ref,capture,previous.get(row['product_id']),base_version))
        fields={'purchase_status':field_fact([observation(row.get('purchase_open'),'purchase_open',ref)],simulated=True),
            'redemption_status':field_fact([observation(row.get('rules',{}).get('redeem_paused'),'rules.redeem_paused',ref)],simulated=True)}
        nav_date=row.get('nav_date');available=row.get('available_at')
        if nav_date and available and utc(available)<=business and nav_date<=business.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat():
            from datetime import date
            fields['unit_nav']=field_fact([observation(row.get('nav'),'nav',ref,unit='yuan_per_share',as_of=date.fromisoformat(nav_date))],simulated=True)
        else:fields['unit_nav']=missing('bank_nav_not_visible_at_business_time')
        daily.append(DailyFact(product_id=row['product_id'],source_kind='simulated',fields=fields,
            observations_cutoff=as_of,freshness='observed',freshness_reason='protected current observation; runtime rechecks sale/rules/time'))
    bind_current_families([p for p in products if p.source_kind=='simulated'],rows,ref)
    adapter='portfolio-derived-v1:'+hashlib.sha256(Path(__file__).read_bytes()+ADAPTER_VERSION.encode()).hexdigest()
    fixed=digest({'base':base.version,'products':[p.model_dump(mode='json') for p in products],
        'evidence':digest(manifest),'adapter':adapter,'as_of':as_of.isoformat()})
    daily_version=digest({'daily':[d.model_dump(mode='json') for d in daily],'as_of':as_of.isoformat(),'adapter':adapter})
    evidence_version=digest({'manifest':manifest,'bank_capture':ref.model_dump(mode='json')})
    version=digest({'fixed':fixed,'daily':daily_version,'evidence':evidence_version,'as_of':as_of.isoformat()})
    info=coverage(products,daily)
    info.update(base_version=base.version,current_bank_count=len(rows),
        current_catalog_explicitly_selected=allow_current_catalog,
        simulated_catalog_added=sorted(set(ids)-old_simulated),
        simulated_catalog_removed=sorted(old_simulated-set(ids)),
        independent_simulated_skus=sum(bool(p.identity_evidence) for p in products if p.source_kind=='simulated'),
        bank_current_versions=dict(Counter(str(r['version']) for r in rows)),
        simulation_text_complete=sum(any(p.fields[f].value for f in TEXT_FIELDS) for p in products if p.source_kind=='simulated'))
    return DataSnapshot(adapter_version=adapter,version=version,fixed_version=fixed,daily_version=daily_version,
        evidence_version=evidence_version,as_of=as_of,products=products,daily=daily,
        input_references=base.input_references+[ref,reference(root,manifest_path,'registered_public_evidence')],coverage=info)
