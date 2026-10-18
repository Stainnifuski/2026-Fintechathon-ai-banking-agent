"""Explicit offline reduction from a verified existing catalogue (no collection).

Run once in a developer checkout. The resulting manifest and package, rather
than the superseded full pool, are the reproducible deployment inputs.
"""
import argparse, hashlib, json, shutil
from collections import Counter
from pathlib import Path

TARGETS = {'bond': 271, 'mixed': 560, 'equity': 69}
PINNED = {'000001','000003','000004','000006','671030'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()


def choose(products, pinned=()):
    by_code = {p['product_id']: p for p in products}
    keep = set(pinned) | PINNED
    if not keep <= by_code.keys():
        raise ValueError('PINNED_PRODUCT_MISSING:'+','.join(sorted(keep-by_code.keys())))
    chosen = []
    for category, count in TARGETS.items():
        group = [p for p in products if p['category']==category]
        mandatory = [p for p in group if p['product_id'] in keep]
        if len(mandatory)>count: raise ValueError('PINNED_CATEGORY_OVERFLOW')
        rest = sorted([p for p in group if p['product_id'] not in keep],
            key=lambda p: (-len(p.get('history',[])), hashlib.sha256(('compact900-v1:'+p['product_id']).encode()).hexdigest()))
        chosen += mandatory + rest[:count-len(mandatory)]
    if Counter(p['category'] for p in chosen)!=Counter(TARGETS): raise ValueError('CATEGORY_COVERAGE')
    return sorted(chosen,key=lambda p:p['product_id'])


def prepare(root):
    if (root/'config/wealth/product_exclusions.json').exists():
        raise ValueError('LEGACY_POOL_EXPANSION_REMOVED: use the approved Sharpe-filtered fixed pool; do not recreate the old 900 pool')
    config=root/'config/wealth'; archive=root/'.deps/compact_upgrade/source'; archive.mkdir(parents=True,exist_ok=True)
    names=['paper_products.json','paper_products_core_v2.json','paper_products_legacy_v1.json','portfolio_evidence_manifest.json','preload.json','competition_runtime_scope.json','portfolio_content_binding_v2.json']
    for name in names:
        if not (archive/name).exists():shutil.copy2(config/name,archive/name)
    payload=json.loads((archive/'paper_products.json').read_bytes())
    legacy=json.loads((archive/'paper_products_legacy_v1.json').read_bytes())
    pinned={p['product_id'] for p in legacy['products']}
    # Preserve all already-present exact public evidence samples too.
    evidence=json.loads((archive/'portfolio_evidence_manifest.json').read_bytes())
    known={p['product_id'] for p in payload['products']}
    pinned.update(c for e in evidence['records'] for c in e['codes'] if c in known)
    rows=choose(payload['products'],pinned);codes={p['product_id'] for p in rows}
    version=hashlib.sha256(canonical({'scope':'compact900-v1','codes':sorted(codes),'product_version':4})).hexdigest()[:24]
    for p in rows:
        p['product_version']=4;p['disclosure_version']=4
        p['rules']={**p['rules'],'dataset_version':version}
        p['known_relationships']=[r for r in p.get('known_relationships',[]) if not isinstance(r,dict) or not r.get('product_id') or r['product_id'] in codes]
    payload.update(products=rows,dataset_version=version,scope_policy_version='competition-compact900-v1')
    (config/'paper_products.json').write_bytes(canonical(payload))
    for name in ('paper_products_core_v2.json','paper_products_legacy_v1.json'):
        old=json.loads((archive/name).read_bytes());old['products']=[p for p in old['products'] if p['product_id'] in codes]
        (config/name).write_bytes(canonical(old))
    evidence['records']=[e for e in evidence['records'] if all(c in codes or not c.isdigit() for c in e['codes'])]
    evidence['checks']=[e for e in evidence.get('checks',[]) if e.get('code') in codes]
    (config/'portfolio_evidence_manifest.json').write_bytes(canonical(evidence))
    preload=json.loads((archive/'preload.json').read_bytes())
    preload.update(version='compact900-v1',target={'real_funds':900,'simulated_bank_wealth':19},
        funds=[{'code':p['product_id'],'expected_name':p['name'],'category':p['category']} for p in rows])
    (config/'preload.json').write_bytes(canonical(preload))
    scope=json.loads((archive/'competition_runtime_scope.json').read_bytes());scope.update(version='competition-compact900-v1',real_purchase_pool=900,categories=TARGETS)
    scope['pricing']='competition-instant-v1: latest published valid NAV for new confirmed simulated trades; historical accepted orders keep original pricing rules'
    (config/'competition_runtime_scope.json').write_bytes(canonical(scope))
    manifest={'schema_version':'wealth-compact-pool-v1','version':version,'count':900,'categories':TARGETS,
        'selection':'Proportional largest-remainder quotas; preserve existing financial/evidence samples; prefer more cached NAV observations then deterministic SHA256 order. No performance/risk promises.',
        'source_categories':{'bond':4093,'mixed':8444,'equity':1041},'codes':sorted(codes),
        'dataset_key':payload['dataset_id']+':'+version,'product_version':4,
        'network_requests':0,'excluded_real_money':True}
    (config/'compact_pool.json').write_bytes(canonical(manifest))
    print(json.dumps({'count':900,'categories':TARGETS,'dataset_key':manifest['dataset_key'],'source_archive':str(archive)},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);prepare(p.parse_args().root)
