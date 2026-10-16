"""Offline, explicit v2 binding migration from a protected bank capture.

Never touches bank records or overwrites an old snapshot. Capture creation is
an authorized server-side read, not an arbitrary client product dictionary.
"""
import argparse
import json
from pathlib import Path
from backend.app.domain.wealth_product_content import CONTENT_VERSION
from backend.app.services.orchestration.wealth_portfolio_data import read, save, digest, utc
from backend.app.services.orchestration.wealth_portfolio_evidence import derive
from backend.app.integrations.wealth_collection_cache import read_protected_json


def migrate(root, base_version, capture_path, capture_sha256, as_of):
    base = read(root, root/'.deps/wealth_portfolio_data/snapshots', base_version)
    capture = read_protected_json(root, capture_path, capture_sha256, '.deps/wealth_portfolio_step08')
    rows = {r['product_id']: r for r in capture['products']}
    old_keys = ('product_id','version','name','kind','category','product_risk_level',
        'identity','fund_family_id','dataset_key','rules','share_class')
    checked = 0
    for product in base.products:
        if not product.current_binding:
            continue
        row = rows.get(product.product_id)
        if row is None:
            raise ValueError('BOUND_PRODUCT_MISSING:'+product.product_id)
        legacy = {**{k:row.get(k) for k in old_keys},
            'disclosure_version':row.get('disclosure',{}).get('version')}
        if digest(legacy) != product.current_binding['content_sha256']:
            raise ValueError('PREVIOUS_BINDING_CHANGED:'+product.product_id)
        checked += 1
    snapshot = derive(root,base_version,capture_path,capture_sha256,utc(as_of),allow_current_catalog=True)
    save(snapshot,root/'.deps/wealth_portfolio_data/snapshots')
    return {'content_contract':CONTENT_VERSION,'previous_data_version':base_version,
        'data_version':snapshot.version,'daily_version':snapshot.daily_version,
        'previous_bindings_verified':checked,'capture_sha256':capture_sha256,
        'bank_rows':len(rows),'database_writes':0,'external_requests':0}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-version',required=True);p.add_argument('--capture',required=True)
    p.add_argument('--sha256',required=True);p.add_argument('--as-of',required=True)
    a=p.parse_args();root=Path(__file__).resolve().parents[1]
    print(json.dumps(migrate(root,a.base_version,a.capture,a.sha256,a.as_of),indent=2))
