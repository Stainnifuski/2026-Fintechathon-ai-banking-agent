"""Compose immutable mixed research snapshots; no balance/history replacement."""
import json
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from sqlalchemy import select
from ...models.wealth_research import WealthFundDirectoryORM as Directory, WealthFundCacheORM as Cache, WealthDatasetORM, WealthResearchProductORM
from ...models.wealth import WealthProductORM
from ...domain.wealth_synthetic import generate, fingerprint

CONFIG = Path(__file__).resolve().parents[4] / 'config' / 'wealth'


def bank_products(config_dir=CONFIG):
    manifest = json.loads((config_dir / 'bank_rules.json').read_text(encoding='utf-8'))
    source_products = [p for p in generate()['products'] if p['kind']=='bank_wealth']
    result = []
    offsets = {}
    for mapping in manifest['products']:
        template = manifest['templates'][mapping['template_id']]
        compatible = [p for p in source_products if p['category'] == template['category']]
        index = offsets.get(template['category'], 0)
        base = compatible[index % len(compatible)]
        offsets[template['category']] = index + 1
        p = deepcopy(base); pid = mapping['product_id']; rules = template['simulation_rules']
        p.update(product_id=pid, name=mapping['name'], fund_family_id='BANK-TEMPLATE-'+mapping['template_id'],
            share_class=None, manager='虚构青穗模拟理财机构', source_product_code=None, identity='模拟银行理财／官方规则参考',
            official_reference=template['source'], rules_adaptations=template['adaptations'],
            source_rules=template['source_rules'], rules_version=manifest['version'], product_version=1, disclosure_version=1,
            category=template['category'], risk_level=template['simulated_risk'], rules={**rules, 'currency':'CNY',
            'dataset_id':'mixed-preload-v12', 'fund_family_id':'BANK-TEMPLATE-'+mapping['template_id'],
            'rules_source':'official_reference_simulation_v1', 'source_template_id':mapping['template_id']},
            quality_flags=['simulated_nav', 'official_rules_reference_not_real_sale', 'shared_template_not_independent_risk'],
            benchmark='模拟因子基准；不采用说明书比较基准作为收益', simulated=True)
        # Reuse factor history with explicit fee difference, not source product NAV.
        old_rate=sum((Decimal(base['rules'][k]) for k in ('annual_management_rate','annual_custody_rate','annual_service_rate')),Decimal(0))
        new_rate=sum((Decimal(p['rules'][k]) for k in ('annual_management_rate','annual_custody_rate','annual_service_rate')),Decimal(0))
        adjustment=(Decimal(1)+(old_rate-new_rate)/252)
        for i, point in enumerate(p['history'],1):
            point['nav']=format((Decimal(point['nav'])*adjustment**i).quantize(Decimal('0.00000001')),'f')
            point.pop('income_per_10000',None); point.pop('seven_day_annualized',None)
        result.append(p)
    return result


async def compose(db, config_dir=CONFIG):
    manifest=json.loads((config_dir/'preload.json').read_text(encoding='utf-8'))
    products=[]; rejected=[]
    for entry in manifest['funds']:
        code=entry['code']; identity=await db.get(Directory,code)
        if identity is None or identity.name!=entry['expected_name']:
            rejected.append({'code':code,'reason':'DIRECTORY_IDENTITY_CHANGED'});continue
        kind='money_yield' if entry['category']=='money' else 'history'
        cached=await db.get(Cache,code+':'+kind)
        if cached is None or not cached.data.get('observations'):
            rejected.append({'code':code,'reason':'NO_SUCCESSFUL_OBSERVATIONS'});continue
        points=cached.data['observations']
        status=await db.get(Cache,code+':status'); fees=await db.get(Cache,code+':fees')
        p={'product_id':'R'+code,'source_product_code':code,'name':identity.name,'source':'akshare',
            'identity':'真实基金数据','category':{'money':'cash','bond':'bond','broad_index':'index','sector_index':'equity','active':'mixed'}[entry['category']],
            'group':entry['category'],'subtype':identity.fund_type,'fund_family_id':entry.get('family_id'),
            'share_class':entry.get('share_class'), 'family_evidence_url':entry.get('family_evidence_url'),
            'rules_complete':False,'risk_level':None,'issuer_risk':None,'seller_risk':None,'rules_version':'research-only-v1',
            'source_url':cached.data['source_url'],'ingested_at':cached.data['available_at'],'published_at':None,
            'missing_rules':['reviewed_risk','full_rules'],'status_evidence':status.data if status else None,'fee_evidence':fees.data if fees else None,
            'history':points if kind=='history' else [],'money_yield':points if kind=='money_yield' else [],
            'quality_flags':['publication_time_unknown','money_yield_not_nav' if kind=='money_yield' else 'unit_nav_not_total_return']+
                (['last_refresh_failed_retained_success'] if cached.status=='FAILED' else [])+
                (['family_unverified'] if not entry.get('family_id') else [])}
        products.append(p)
    products += bank_products(config_dir)
    now=datetime.now(timezone.utc)
    version=fingerprint({'config':manifest,'products':products,'as_of':now.date().isoformat()})[:24]
    existing=await db.get(WealthDatasetORM, 'mixed-preload-v12:'+version)
    old_rows=(await db.scalars(select(WealthResearchProductORM).where(WealthResearchProductORM.dataset_key=='mixed-preload-v12:'+version))).all() if existing else []
    old_versions={r.product_id:r.data.get('product_version') for r in old_rows}
    for p in products:
        if p.get('rules'):
            current=await db.get(WealthProductORM,p['product_id'])
            p['product_version']=old_versions.get(p['product_id']) or ((current.current_version+1) if current else 1)
            p['disclosure_version']=p['product_version']
            p['rules']['dataset_version']=version
    counts={}
    for p in products:counts[p.get('group','bank_wealth')]=counts.get(p.get('group','bank_wealth'),0)+1
    return {'schema_version':1,'dataset_id':'mixed-preload-v12','dataset_version':version,'mode':'snapshot','as_of':now.date().isoformat(),
        'products':products,'source':'akshare_and_official_reference_simulation','selection_version':manifest['version'],
        'group_counts':counts,'rejected':rejected,'target':manifest['target'],'family_count_verified':len({p['fund_family_id'] for p in products if p.get('fund_family_id')}),
        'family_unknown_count':sum(not p.get('fund_family_id') for p in products),'not_independent_asset_count':True}
