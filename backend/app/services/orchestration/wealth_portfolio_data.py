"""确定性资料快照和全资产消费边界；不推荐、不交易、不刷新上游。"""
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo
from ...schemas.wealth_portfolio_data import RawReference, ProductFact, DailyFact, DataSnapshot, AssetModelFacts, FieldFact
from ...integrations.wealth_portfolio_data import (ALIASES, clean, records, observation, field_fact,
    read_raw, validate_evidence, registered_for)
from ...integrations.wealth_collection_cache import read_protected_json
from .wealth_portfolio import build_snapshot, SnapshotError

_APP_ROOT=Path(__file__).resolve().parents[2]
_ADAPTER_FILES=[Path(__file__),_APP_ROOT/'schemas/wealth_portfolio_data.py',_APP_ROOT/'integrations/wealth_portfolio_data.py',_APP_ROOT/'integrations/wealth_collection_cache.py']
ADAPTER_VERSION='portfolio-data-v1:'+hashlib.sha256(b''.join(p.read_bytes() for p in _ADAPTER_FILES)).hexdigest()
FIXED_FUNCTIONS={'fund_name_em','fund_overview_em','fund_info_ths','fund_individual_basic_info_xq'}
DAILY_FUNCTIONS={'fund_purchase_em','fund_open_fund_info_em','public_unit_nav_history'}
BUCKETS={'money_market':'cash_management','money':'cash_management','cash':'cash_management','bond':'stable','mixed':'growth','equity':'growth'}
TEXT_FIELDS=('objective','strategy','benchmark','tracking_target')


def canonical(value) -> bytes:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def utc(value: str) -> datetime:
    instant=datetime.fromisoformat(value.replace('Z','+00:00'))
    if instant.tzinfo is None: raise ValueError('AS_OF_TIMEZONE_REQUIRED')
    return instant.astimezone(timezone.utc)


def reference(root: Path, path: Path, function: str, kwargs=None, observed_at=None) -> RawReference:
    return RawReference(path=path.relative_to(root).as_posix(),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        function=function,kwargs=kwargs or {},observed_at=observed_at)


def inventory(root: Path) -> tuple[list[dict],list[RawReference]]:
    """只读进度，取全部目录及允许输入原料，原始路径同缓存保护。"""
    folder=root/'.deps/a1_collection'
    with sqlite3.connect((folder/'progress.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        universe=[dict(row) for row in db.execute('select * from universe order by code')]
        jobs=[dict(row) for row in db.execute('select function,kwargs,raw_path from jobs where status="success" order by id')]
    refs=[]
    for job in jobs:
        if job['function'] not in FIXED_FUNCTIONS|DAILY_FUNCTIONS: continue
        path=(folder/Path(job['raw_path'].replace('\\','/'))).resolve()
        if not path.is_relative_to(folder/'raw'): raise ValueError('EVIDENCE_PATH_DENIED')
        # 指纹生成是显式本地库存步骤，后续每次消费由共用保护再次校验。
        ref=reference(root,path,job['function'],json.loads(job['kwargs']))
        raw=read_protected_json(root,ref.path,ref.sha256,'.deps/a1_collection/raw')
        ref.observed_at=utc(raw['observed_at'])
        refs.append(ref)
    ledger=root/'.deps/wealth_portfolio_step09/public_ledger.json'
    if ledger.exists():
        for entry in json.loads(ledger.read_text(encoding='utf8')):
            if entry.get('kind')!='history' or entry.get('http')!=200 or entry.get('status')!='received':continue
            path=(root/entry['path']).resolve()
            if not path.is_relative_to(root/'.deps/wealth_portfolio_step09/public'):raise ValueError('EVIDENCE_PATH_DENIED')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('EVIDENCE_FINGERPRINT_MISMATCH')
            # Money-market scripts have a yield series, not a unit-NAV series.
            if b'Data_netWorthTrend' not in path.read_bytes():continue
            refs.append(RawReference(path=entry['path'],sha256=entry['sha256'],function='public_unit_nav_history',
                kwargs={'symbol':entry['code'],'url':entry['url'],'indicator':'单位净值走势'},observed_at=utc(entry['observed_at'])))
    return universe,refs


def missing(reason: str) -> FieldFact:
    return FieldFact(status='missing',reason=reason)


def apply_evidence(product: ProductFact, entries: list, cutoff: datetime) -> None:
    scoped=registered_for(product.product_id,product.source_kind,entries,cutoff)
    product.risk_evidence=[e for e in scoped if e.kind=='risk']
    product.family_evidence=[e for e in scoped if e.kind=='family']
    product.identity_evidence=[e for e in scoped if e.kind=='entity']
    shares={e.share_classes[product.product_id] for e in scoped if e.kind in ('family','entity')}
    if len(shares)==1:
        product.share_class=next(iter(shares))
        product.fields['share_class']=FieldFact(value=product.share_class,status='verified') if product.share_class is not None else missing('confirmed_single_non_AC_entity')
    elif len(shares)>1:
        product.fields['share_class']=FieldFact(status='conflict',reason='registered_share_identity_conflict');product.share_class=None
    categories=[e for e in scoped if e.kind=='category']
    if categories:
        original=product.fields.get('raw_category')
        merged=field_fact((original.observations if original else [])+
            [observation(e.field_value,'category',e.reference,as_of=e.published_on) for e in categories])
        if merged.status=='observed':merged.status='verified'
        product.fields['raw_category']=merged
        # 原始类别冲突不能靠新登记覆盖；不由包装/名字补实质类型或解除ETF/FOF排除。
        if merged.status=='conflict' and product.scope_status!='excluded':
            product.scope_status='pending';product.scope_reason='category_evidence_conflict';product.bucket=None
    for field in TEXT_FIELDS:
        candidates=[e for e in scoped if e.kind==field]
        observations=[observation(e.field_value,field,e.reference,as_of=e.published_on) for e in candidates]
        if observations:
            original=product.fields.get(field)
            # 仍保留旧观察；不同正文/版本不可悄悄择一。
            merged=field_fact((original.observations if original else [])+observations)
            if merged.status=='observed':merged.status='verified'
            product.fields[field]=merged
    for kind,field,attr in [('family','family_id','family_id'),('issuer','issuer_normalized','issuer_name')]:
        candidates=[e for e in scoped if e.kind==kind]
        values={getattr(e,attr) for e in candidates}
        if len(values)==1:
            product.fields[field]=FieldFact(value=next(iter(values)),status='verified',observations=[observation(getattr(e,attr),kind,e.reference) for e in candidates])
        elif len(values)>1:
            product.fields[field]=FieldFact(status='conflict',reason='registered_evidence_conflict',observations=[observation(getattr(e,attr),kind,e.reference) for e in candidates])
    # 风险不跨渠道合成通用值，独立风险证据数组供下一步门禁。
    if product.risk_evidence:
        channels=defaultdict(set)
        for record in product.risk_evidence:channels[record.channel].add(record.risk_level)
        if any(len(values)>1 for values in channels.values()):
            product.fields['formal_risk']=FieldFact(status='conflict',reason='channel_ratings_or_applicable_times_unaligned',observations=[observation(e.risk_level,'risk_level',e.reference) for e in product.risk_evidence])
        else:product.fields['formal_risk']=missing('use_channel_specific_risk_evidence; no_universal_rating')


def real_products(universe: list[dict], refs: list[RawReference], root: Path, cutoff: datetime, entries: list) -> tuple[list[ProductFact],list[DailyFact]]:
    by_code={row['code']:row for row in universe if row.get('base_type') not in ('money','money_market')}
    fixed=defaultdict(lambda:defaultdict(list));daily=defaultdict(lambda:defaultdict(list));bindings=defaultdict(list)
    for ref in refs:
        raw=read_raw(root,ref)
        if ref.observed_at>cutoff: continue
        code=ref.kwargs.get('symbol')
        rows=records(raw)
        if code in by_code:
            identities=[str(row['基金代码']) for row in rows if clean(row.get('基金代码')) is not None]
            if identities and not any(code in value for value in identities):raise ValueError('EVIDENCE_ENTITY_MISMATCH')
        for row in rows:
            entity=str(row.get('基金代码',code or ''))
            # 概况的联合前后端代码不扩展到其他份额，只绑定请求代码。
            if code in by_code: entity=code
            if entity not in by_code: continue
            if ref.function in FIXED_FUNCTIONS:
                bindings[entity].append(ref)
                if ref.function=='fund_name_em': continue
                for field,keys in ALIASES.items():
                    if field.startswith('purchase') or field=='redemption_status': continue
                    for key in keys:
                        if key in row:fixed[entity][field].append(observation(row[key],key,ref))
            elif ref.function=='fund_purchase_em':
                for field in ('purchase_status','redemption_status','purchase_minimum','purchase_limit'):
                    for key in ALIASES[field]:
                        if key in row:
                            # 原始状态没有完整日期，金额未获单位元的出处证明，不转成分。
                            daily[entity][field].append(observation(row[key],key,ref,unit='source_unit_unverified' if field in ('purchase_minimum','purchase_limit') else None))
                # This mixed column is NAV only for independently classified
                # non-money funds. MM-DD has no year: preserve that uncertainty
                # and the explicit latest-not-after-observation interpretation.
                category=by_code[entity].get('base_type')
                raw_type=clean(row.get('基金类型'))
                value=clean(row.get('最新净值/万份收益'))
                rawdate=clean(row.get('最新净值/万份收益-报告时间'))
                non_money=(isinstance(raw_type,str) and '货币' not in raw_type) if raw_type else category in ('bond','mixed','equity')
                if non_money and value is not None and rawdate:
                    observed_day=ref.observed_at.astimezone(ZoneInfo('Asia/Shanghai')).date()
                    try:
                        if len(str(rawdate))==5:
                            business_date=date.fromisoformat(f'{observed_day.year}-{rawdate}')
                            if business_date>observed_day:business_date=business_date.replace(year=business_date.year-1)
                        else:business_date=date.fromisoformat(str(rawdate)[:10])
                        if business_date<=observed_day and business_date<=cutoff.astimezone(ZoneInfo('Asia/Shanghai')).date():
                            obs=observation(value,'最新净值/万份收益',ref,unit='yuan_per_share',as_of=business_date)
                            obs.reason='non_money_category_NAV; MM-DD year interpreted as latest not after observation' if len(str(rawdate))==5 else 'non_money_category_NAV'
                            daily[entity]['unit_nav'].append(obs)
                    except (ValueError,TypeError):pass
            else:
                branch=ref.kwargs.get('indicator')
                allowed={'单位净值走势':('单位净值','unit_nav','yuan_per_share'), '累计净值走势':('累计净值','cumulative_nav','yuan_per_share')}
                mappings=[allowed[branch]] if branch in allowed else []
                rawdate=row.get('净值日期')
                try: business_date=date.fromisoformat(str(rawdate)[:10])
                except (ValueError,TypeError): continue
                if business_date>cutoff.astimezone(ZoneInfo('Asia/Shanghai')).date():continue
                for key,field,unit in mappings:
                    if key in row:daily[entity][field].append(observation(row[key],key,ref,unit=unit,as_of=business_date))
    products=[];dailies=[]
    for row in universe:
        if row['code'] not in by_code:continue
        code=row['code']; status=row['classification_status'];base=row['base_type'] if row['base_type'] in BUCKETS else None
        excluded=status=='excluded'
        scope='excluded' if excluded else ('verified' if status=='verified' and base else 'pending')
        fields={field:field_fact(fixed[code][field]) for field in ('issuer_raw',*TEXT_FIELDS,'raw_risk')}
        directory_refs=[r for r in bindings[code] if r.function=='fund_name_em']
        if directory_refs:
            fields['raw_category']=field_fact([observation(row['raw_type'],'基金类型',directory_refs[0])])
        for identity in ('share_class','product_version','disclosure_version'):
            fields[identity]=missing('not_in_saved_directory_or_verified_identity_evidence')
        fields.update({field:missing('no_verified_entity_specific_evidence') for field in ('issuer_normalized','formal_risk','family_id','principal_guarantee')})
        p=ProductFact(product_id=code,source_kind='real',name=row['name'],raw_category=row['raw_type'],
            substantive_category=base,classification_status=status,classification_basis=row['basis'],forms=json.loads(row['forms']),
            scope_status=scope,scope_reason=row['basis'] if scope!='verified' else None,bucket=BUCKETS.get(base) if scope=='verified' else None,
            fields=fields,references=list({r.path:r for r in bindings[code]}.values()))
        apply_evidence(p,entries,cutoff);products.append(p)
        fields_daily={field:field_fact(daily[code][field]) for field in ('purchase_status','redemption_status','purchase_minimum','purchase_limit')}
        fields_daily['source_expires_at']=missing('source_expiry_not_in_saved_raw; rebuild_does_not_renew')
        for metric in ('unit_nav','cumulative_nav','income_per_10000','seven_day_annualized'):
            observations=daily[code][metric]
            if observations:
                latest=max(o.as_of for o in observations)
                fields_daily[metric]=field_fact([o for o in observations if o.as_of==latest])
            else:fields_daily[metric]=missing('no_saved_metric')
        observed=[o.observed_at for f in fields_daily.values() for o in f.observations if o.observed_at]
        dates=[o.as_of for f in fields_daily.values() for o in f.observations if o.as_of]
        stale=bool(observed) and (min(observed)<cutoff-timedelta(days=1) or not dates or min(dates)<cutoff.astimezone(ZoneInfo('Asia/Shanghai')).date()-timedelta(days=1))
        dailies.append(DailyFact(product_id=code,source_kind='real',fields=fields_daily,observations_cutoff=cutoff,
            freshness='stale' if stale else 'unknown',freshness_reason='local_24h_age_indicator_not_source_expiry; business_date_may_be_unavailable' if stale else 'no_proven_current_upstream_status'))
    return products,dailies


def simulated_product(row: dict, ref: RawReference, bank: bool) -> ProductFact:
    code=row['product_id'];category=row.get('category');risk=row.get('risk_level');rules=row.get('rules',{})
    fields={f:missing('not_in_saved_simulation') for f in ('issuer_raw','issuer_normalized',*TEXT_FIELDS,'formal_risk','family_id','principal_guarantee','raw_risk')}
    source_fields={'issuer_raw':'manager','benchmark':'benchmark','family_id':'fund_family_id','raw_risk':'risk_level','strategy':'investment_strategy','objective':'investment_objective'}
    # manager在此虚构产品规范明确是机构名；真实基金不复用这一映射。
    for field,key in source_fields.items():fields[field]=field_fact([observation(row.get(key),key,ref)],simulated=True)
    fields['formal_risk']=field_fact([observation(risk,'risk_level',ref)],simulated=True)
    principal=row.get('principal_guaranteed')
    if principal is None:principal=rules.get('principal_guaranteed')
    fields['principal_guarantee']=field_fact([observation(principal,'principal_guaranteed',ref)],simulated=True)
    quick=bool(rules.get('quick_redemption'))
    scope='verified';reason=None;bucket=BUCKETS.get(category)
    if bank:
        if quick and risk==1 and category=='cash' and row.get('rules_complete') is True:bucket='cash_management'
        elif quick and risk==1:
            scope='pending';bucket=None;reason='cash_management_or_complete_quick_rules_not_proven'
        elif risk==2 and category=='bond':bucket='stable'
        else:scope='policy_mismatch';bucket=None;reason='original_rating_or_attributes_not_in_two_bank_policy_types'
    elif category not in ('money','bond','mixed','equity'):
        scope='pending';bucket=None;reason='form_is_not_verified_substantive_fund_category'
    for field in ('share_class','product_version','disclosure_version'):
        fields[field]=field_fact([observation(row.get(field),field,ref)],simulated=True)
    fields['raw_category']=field_fact([observation(category,'category',ref)],simulated=True)
    return ProductFact(product_id=code,source_kind='simulated',share_class=row.get('share_class'),product_version=row.get('product_version'),
        disclosure_version=row.get('disclosure_version'),name=row.get('name'),raw_category=category,substantive_category=category if category in BUCKETS else None,
        classification_status='simulated',classification_basis='explicit_simulation_category_and_original_rules',forms={'subtype':row.get('subtype')},scope_status=scope,scope_reason=reason,bucket=bucket,fields=fields,references=[ref])


def simulated_daily(row: dict, ref: RawReference, cutoff: datetime) -> DailyFact:
    rules=row.get('rules',{});fields={}
    for field,key in [('purchase_status','purchase_paused'),('redemption_status','redeem_paused'),('purchase_minimum','min_amount_fen'),('purchase_limit','max_amount_fen')]:
        value=rules.get(key)
        if field.endswith('status'):value=None if type(value) is not bool else ('paused' if value else 'open_simulated')
        fields[field]=field_fact([observation(value,'rules.'+key,ref,unit='fen' if field in ('purchase_minimum','purchase_limit') else None)],simulated=True)
    valid=[]
    for point in row.get('history',[]):
        if point['date']>str(cutoff.astimezone(ZoneInfo('Asia/Shanghai')).date()):continue
        if not point.get('available_at') or utc(point['available_at'])>cutoff:continue
        if not point.get('published_at') or utc(point['published_at'])>cutoff:continue
        valid.append(point)
    if valid:
        latest=max(valid,key=lambda x:x['date']); fields['unit_nav']=field_fact([observation(latest['nav'],'history.nav',ref,unit='yuan_per_share',as_of=date.fromisoformat(latest['date']))],simulated=True)
    else: fields['unit_nav']=missing('no_published_simulated_nav_at_cutoff')
    return DailyFact(product_id=row['product_id'],source_kind='simulated',fields=fields,observations_cutoff=cutoff,freshness='unknown',freshness_reason='fixed_simulated_rules_not_live_status; NAV availability guarded')


def coverage(products: list[ProductFact],daily: list[DailyFact]) -> dict:
    real=[p for p in products if p.source_kind=='real']; candidates=[p for p in real if p.scope_status!='excluded']
    exists=lambda p,f: any(o.value is not None for o in p.fields[f].observations)
    text_union=[p for p in candidates if any(exists(p,f) for f in TEXT_FIELDS)]
    risk=[p for p in candidates if p.risk_evidence];family=[p for p in candidates if p.family_evidence]
    bank=[p for p in products if p.product_id.startswith('B') and p.source_kind=='simulated']
    return {'units':'real counts per share code; family groups separately; no eligibility counts',
        'directory_visible':len(real),'candidate_scope':len(candidates),'scope_verified':sum(p.scope_status=='verified' for p in real),
        'scope_pending':sum(p.scope_status=='pending' for p in real),'excluded':sum(p.scope_status=='excluded' for p in real),
        'base_categories':dict(Counter(p.substantive_category for p in real if p.scope_status=='verified')),
        'issuer_raw_union':sum(exists(p,'issuer_raw') for p in candidates),'issuer_normalized':sum(p.fields['issuer_normalized'].status=='verified' for p in candidates),
        'texts':{f:sum(exists(p,f) for p in candidates) for f in TEXT_FIELDS},'text_union':len(text_union),'issuer_text_intersection':sum(exists(p,'issuer_raw') for p in text_union),
        'nonconflicting_issuer_text_intersection':sum(p.fields['issuer_raw'].value is not None and any(p.fields[f].value is not None for f in TEXT_FIELDS) for p in candidates),
        'nonconflicting_texts':{f:sum(p.fields[f].value is not None for p in candidates) for f in TEXT_FIELDS},
        'daily_status_dates':dict(Counter(o.observed_at.date().isoformat() for d in daily if d.source_kind=='real' for o in d.fields['purchase_status'].observations if o.observed_at)),
        'risk_verified_shares':len(risk),'risk_share_channels':sum(len(p.risk_evidence) for p in risk),
        'risk_applicability_known':sum(e.applicable_from is not None for p in risk for e in p.risk_evidence),
        'family_verified_shares':len(family),'family_verified_groups':len({e.family_id for p in family for e in p.family_evidence}),
        'risk_family_intersection':len({p.product_id for p in risk}&{p.product_id for p in family}),
        'conflict_products':sum(any(f.status=='conflict' for f in p.fields.values()) for p in real),
        'conflict_fields':dict(Counter(k for p in real for k,f in p.fields.items() if f.status=='conflict')),
        'bank_original_risk':dict(Counter(str(p.fields['raw_risk'].value) for p in bank)),
        'bank_policy_status':dict(Counter(p.scope_status for p in bank)),
        'bank_details':[{'code':p.product_id,'risk':p.fields['raw_risk'].value,'scope':p.scope_status,'reason':p.scope_reason,'principal_guarantee':p.fields['principal_guarantee'].value,'bucket':p.bucket} for p in bank],
        'daily_freshness':dict(Counter(d.freshness for d in daily if d.source_kind=='real')),
        'purchase_status_observed':sum(d.fields['purchase_status'].value is not None for d in daily if d.source_kind=='real'),
        'external_requests':0,'formal_asset_source':'unavailable', 'approved_trade_product':False}


def build(root: Path, as_of: datetime, *, raw_root: Path | None=None) -> DataSnapshot:
    root=root.resolve();raw_root=(raw_root or root).resolve();universe,refs=inventory(raw_root)
    evidence_path=root/'config/wealth/portfolio_evidence_manifest.json'
    evidence_ref=reference(root,evidence_path,'registered_public_evidence')
    evidence=read_protected_json(root,evidence_ref.path,evidence_ref.sha256,'config/wealth')
    if evidence.get('schema_version')!='portfolio-evidence-v1':raise ValueError('EVIDENCE_VERSION_UNSUPPORTED')
    entries=[validate_evidence(root,e,evidence['authority_sources'],{row['code'] for row in universe}) for e in evidence['records']]
    products,daily=real_products(universe,refs,raw_root,as_of,entries)
    config_refs=[]
    for filename,bank in [('bank_products.json',True),('synthetic.json',False)]:
        path=root/'config/wealth/data'/filename;ref=reference(root,path,'simulation_config');config_refs.append(ref)
        for row in records(read_protected_json(root,ref.path,ref.sha256,'config/wealth/data')):
            products.append(simulated_product(row,ref,bank));daily.append(simulated_daily(row,ref,as_of))
    fixed_version=digest({'universe':universe,'refs':[r.model_dump(mode='json') for r in refs if r.function in FIXED_FUNCTIONS],
        'simulation': [r.model_dump(mode='json') for r in config_refs],'evidence_sha256':evidence_ref.sha256,'adapter':ADAPTER_VERSION,'as_of':as_of.isoformat()})
    daily_version=digest({'refs':[r.model_dump(mode='json') for r in refs if r.function in DAILY_FUNCTIONS],
        'simulation':[r.model_dump(mode='json') for r in config_refs],'as_of':as_of.isoformat(),'adapter':ADAPTER_VERSION})
    evidence_version=digest(evidence)
    version=digest({'fixed':fixed_version,'daily':daily_version,'evidence':evidence_version,'as_of':as_of.isoformat()})
    return DataSnapshot(adapter_version=ADAPTER_VERSION,version=version,fixed_version=fixed_version,daily_version=daily_version,evidence_version=evidence_version,
        as_of=as_of,products=products,daily=daily,input_references=refs+config_refs+[evidence_ref],coverage=coverage(products,daily))


def save(snapshot: DataSnapshot, directory: Path) -> dict:
    """内容寻址，构建时间另存；已有版本损坏时拒绝覆盖。"""
    directory.mkdir(parents=True,exist_ok=True);path=directory/(snapshot.version+'.json')
    content=canonical(snapshot.model_dump(mode='json'))
    if path.exists() and path.read_bytes()!=content:raise ValueError('SNAPSHOT_VERSION_CONTENT_CONFLICT')
    if not path.exists():path.write_bytes(content)
    index={'version':snapshot.version,'sha256':hashlib.sha256(content).hexdigest(),'path':path.name}
    index_path=directory/(snapshot.version+'.index.json')
    if index_path.exists() and json.loads(index_path.read_text())!=index:raise ValueError('SNAPSHOT_INDEX_CONFLICT')
    index_path.write_bytes(canonical(index))
    (directory/'last_build.json').write_bytes(canonical({'built_at':datetime.now(timezone.utc).isoformat(),'cache_read_at':datetime.now(timezone.utc).isoformat(),**index,'coverage':snapshot.coverage}))
    return index


def read(root: Path, directory: Path, version: str) -> DataSnapshot:
    if len(version)!=64 or any(c not in '0123456789abcdef' for c in version):raise ValueError('SNAPSHOT_VERSION_INVALID')
    directory=directory.resolve();root=root.resolve()
    if not directory.is_relative_to(root/'.deps/wealth_portfolio_data'):raise ValueError('SNAPSHOT_PATH_DENIED')
    index=json.loads((directory/(version+'.index.json')).read_text())
    if index['version']!=version or index['path']!=version+'.json':raise ValueError('SNAPSHOT_VERSION_CONFLICT')
    payload=read_protected_json(root,(directory/index['path']).relative_to(root).as_posix(),index['sha256'],'.deps/wealth_portfolio_data')
    snapshot=DataSnapshot.model_validate(payload)
    # 已内容校验的v1快照保留可读；新代码不得使既有版本因适配器哈希变化失效。
    adapter=snapshot.adapter_version
    suffix=adapter.split(':')[-1]
    if snapshot.version!=version or not adapter.startswith(('portfolio-data-v1:','portfolio-derived-v1:')) or len(suffix)!=64 or any(c not in '0123456789abcdef' for c in suffix):
        raise ValueError('SNAPSHOT_VERSION_CONFLICT')
    return snapshot


async def read_asset_model_facts(source, user_id: str, product_facts: list[ProductFact] | None = None,*,from_current: bool=False) -> AssetModelFacts:
    """复用同一银行builder，不从ORM或旧context拼接资产。"""
    if source is None:raise SnapshotError('full AssetSummary source unavailable','ASSET_SUMMARY_UNAVAILABLE',503)
    reader=getattr(source,'read_from_current',source.read) if from_current else source.read
    bundle=await reader(user_id);snapshot=build_snapshot(bundle)
    unknown=[p.position_key for p in snapshot.positions if p.bucket is None]
    if product_facts is not None:
        by_id={p.product_id:p for p in product_facts}
        if len(by_id)!=len(product_facts):raise SnapshotError('ambiguous product identity')
        for position in snapshot.positions:
            fact=by_id.get(position.product_id)
            if fact is not None and fact.product_version is not None and fact.product_version!=position.product_version:
                raise SnapshotError('position and model product version mismatch')
            if fact is None or fact.bucket is None or fact.scope_status!='verified':unknown.append(position.position_key)
    unknown=sorted(set(unknown))
    provenance=bundle.get('assessment_provenance',{})
    allowed={'source','version','read_at','observed_at'}
    if set(provenance)-allowed:raise SnapshotError('assessment provenance fields unsupported')
    for key in ('read_at','observed_at'):
        if provenance.get(key):utc(provenance[key])
    if provenance.get('version') is not None and (type(provenance['version']) not in (int,str) or provenance['version'] in ('',0)):
        raise SnapshotError('assessment version unavailable or malformed')
    if provenance.get('read_at') and utc(provenance['read_at'])!=snapshot.as_of:raise SnapshotError('assessment and asset read time mismatch')
    provenance={**provenance,'missing_fields':[k for k in ('source','version','read_at') if not provenance.get(k)]}
    return AssetModelFacts(snapshot=snapshot,classification_complete=not unknown,disable_eta=bool(unknown) or not snapshot.valuation_complete,
        disable_eta_reasons=([f'unclassified_position:{p}' for p in unknown]+([] if snapshot.valuation_complete else ['valuation_incomplete'])),assessment_provenance=provenance)
