"""Explicitly reduce the fixed pool to the audited numeric-Sharpe subset.

No network or database access. Run only with the complete 900-code audit.
"""
import hashlib,json
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def prepare(root=ROOT):
    config=root/'config/wealth'
    audit=root/'.deps/sharpe_coverage_20261017/observations.jsonl'
    rows=[json.loads(line) for line in audit.read_text(encoding='utf-8').splitlines()]
    previous=json.loads((config/'compact_pool.json').read_bytes())
    assert len(rows)==len({r['code'] for r in rows})==900
    allowed={r['code'] for r in rows if r.get('values')}
    removed={r['code'] for r in rows}-allowed
    assert len(allowed)==806 and len(removed)==94
    assert set(previous['codes']) in (allowed,allowed|removed)
    payload=json.loads((config/'paper_products.json').read_bytes())
    products=[p for p in payload['products'] if p['product_id'] in allowed]
    categories=dict(Counter(p['category'] for p in products))
    assert categories=={'bond':242,'mixed':501,'equity':63}
    canonical=lambda obj:json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    version=hashlib.sha256(canonical({'policy':'fixed-sharpe-observed-v1','codes':sorted(allowed),'product_version':5})).hexdigest()[:24]
    # New catalogue binding receives a new version; accepted trades keep v1-v4.
    payload.update(products=products,dataset_version=version,scope_policy_version='competition-sharpe806-v1')
    for product in products:
        product['rules']={**product['rules'],'dataset_version':version}
        product['product_version']=5;product['disclosure_version']=5
    (config/'paper_products.json').write_bytes(canonical(payload))
    for name in ['paper_products_core_v2.json','paper_products_legacy_v1.json']:
        data=json.loads((config/name).read_bytes());data['products']=[p for p in data['products'] if p['product_id'] not in removed]
        (config/name).write_bytes(canonical(data))
    preload=json.loads((config/'preload.json').read_bytes())
    preload.update(version='sharpe806-v1',target={'real_funds':806,'simulated_bank_wealth':19},
                   funds=[{'code':p['product_id'],'expected_name':p['name'],'category':p['category']} for p in products])
    (config/'preload.json').write_bytes(canonical(preload))
    scope=json.loads((config/'competition_runtime_scope.json').read_bytes())
    scope.update(version='competition-sharpe806-v1',real_purchase_pool=806,categories=categories)
    (config/'competition_runtime_scope.json').write_bytes(canonical(scope))
    evidence=json.loads((config/'portfolio_evidence_manifest.json').read_bytes())
    evidence['records']=[r for r in evidence['records'] if not set(r['codes'])&removed]
    evidence['checks']=[r for r in evidence.get('checks',[]) if r.get('code') not in removed]
    (config/'portfolio_evidence_manifest.json').write_bytes(canonical(evidence))
    previous.update(version=version,codes=sorted(allowed),count=806,categories=categories,product_version=5,
                    dataset_key=payload['dataset_id']+':'+version,
                    selection='Fixed subset with numeric annualized Sharpe in the 2026-10-17 Xueqiu analysis audit; missing data excluded by explicit user instruction. Not a performance ranking.',
                    sharpe_observation_policy='Any available 1y/3y/5y numeric value; observation does not assert a common period end or guarantee future availability.')
    (config/'compact_pool.json').write_bytes(canonical(previous))
    print(json.dumps({'count':806,'categories':categories,'version':version}))


if __name__=='__main__':prepare()
