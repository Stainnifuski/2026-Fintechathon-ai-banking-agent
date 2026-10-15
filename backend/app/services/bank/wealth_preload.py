"""Compose immutable mixed research snapshots; no balance/history replacement."""
import json
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from sqlalchemy import select
from ...models.wealth_research import WealthFundDirectoryORM as Directory, WealthFundCacheORM as Cache, WealthDatasetORM, WealthResearchProductORM
from ...models.wealth import WealthProductORM
from ...domain.wealth_synthetic import fingerprint

CONFIG = Path(__file__).resolve().parents[4] / 'config' / 'wealth'


def bank_products(config_dir=CONFIG):
    from ...domain.wealth_fixed_data import bank_products as read_fixed
    return read_fixed(config_dir/'data')


async def compose(db, config_dir=CONFIG):
    manifest=json.loads((config_dir/'preload.json').read_text(encoding='utf-8'))
    products=[]; rejected=[]
    for entry in manifest['funds']:
        code=entry['code']; identity=await db.get(Directory,code)
        if identity is None or identity.name!=entry['expected_name']:
            rejected.append({'code':code,'reason':'DIRECTORY_IDENTITY_CHANGED'});continue
        kind='history'
        cached=await db.get(Cache,code+':'+kind)
        if cached is None or not cached.data.get('observations'):
            rejected.append({'code':code,'reason':'NO_SUCCESSFUL_OBSERVATIONS'});continue
        points=cached.data['observations']
        status=await db.get(Cache,code+':status'); fees=await db.get(Cache,code+':fees')
        p={'product_id':'R'+code,'source_product_code':code,'name':identity.name,'source':'akshare',
            'identity':'真实基金数据','category':{'bond':'bond','broad_index':'index','sector_index':'equity','active':'mixed'}[entry['category']],
            'group':entry['category'],'subtype':identity.fund_type,'fund_family_id':entry.get('family_id'),
            'share_class':entry.get('share_class'), 'family_evidence_url':entry.get('family_evidence_url'),
            'rules_complete':False,'risk_level':None,'issuer_risk':None,'seller_risk':None,'rules_version':'research-only-v1',
            'source_url':cached.data['source_url'],'ingested_at':cached.data['available_at'],'published_at':None,
            'missing_rules':['reviewed_risk','full_rules'],'status_evidence':status.data if status else None,'fee_evidence':fees.data if fees else None,
            'history':points,
            'quality_flags':['publication_time_unknown','unit_nav_not_total_return']+
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
