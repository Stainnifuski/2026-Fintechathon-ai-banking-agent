"""Prepare/import a content-verified offline A snapshot, without collection/DB.

The package reproduces an existing frozen runtime source. It does not renew
source dates or claim that the raw collection can be rebuilt from this package.
"""
import argparse,gzip,hashlib,json
from pathlib import Path
from backend.app.services.orchestration.wealth_portfolio_data import read,canonical,save

ROOT=Path(__file__).resolve().parents[1]
def sha(raw):return hashlib.sha256(raw).hexdigest()

def export(root,version,out):
    source=root/'.deps/wealth_portfolio_data/snapshots'
    snapshot=read(root,source,version) # complete schema/time/unit validation
    index=json.loads((source/(version+'.index.json')).read_text())
    raw=(source/(version+'.json')).read_bytes()
    packed=gzip.compress(raw,mtime=0)
    public=[root/'config/wealth/portfolio_evidence_manifest.json',
            *sorted((root/'config/wealth/portfolio_public_evidence').glob('*.txt'))]
    public.extend(sorted((root/'config/wealth').glob('paper_products*.json')))
    supplement=root/'config/wealth/portfolio_bank_supplement.json'
    if supplement.exists():public.append(supplement)
    scope=root/'config/wealth/competition_runtime_scope.json'
    if scope.exists():public.append(scope)
    manifest={'schema_version':'wealth-portfolio-offline-package-v1','data_version':version,
        'daily_version':snapshot.daily_version,'snapshot_sha256':index['sha256'],
        'gzip_sha256':sha(packed),'file':version+'.snapshot.json.gz',
        'directory_count':len(snapshot.products),'as_of':snapshot.as_of.isoformat(),
        'public_files':{p.relative_to(root).as_posix():sha(p.read_bytes()) for p in public},
        'boundary':'frozen source only; no renewed qualification, pricing, rating or collection'}
    out.mkdir(parents=True,exist_ok=True)
    for name,content in [(manifest['file'],packed),(version+'.package.json',canonical(manifest))]:
        path=out/name
        if path.exists() and path.read_bytes()!=content:raise ValueError('PACKAGE_CONTENT_CONFLICT')
        if not path.exists():path.write_bytes(content)
    return manifest

def install(root,manifest_path):
    manifest=json.loads(manifest_path.read_text(encoding='utf8'))
    if manifest['schema_version']!='wealth-portfolio-offline-package-v1':raise ValueError('PACKAGE_SCHEMA_INVALID')
    version=manifest['data_version']
    if len(version)!=64 or any(c not in '0123456789abcdef' for c in version):raise ValueError('PACKAGE_VERSION_INVALID')
    if manifest['file']!=version+'.snapshot.json.gz':raise ValueError('PACKAGE_PATH_INVALID')
    packed=(manifest_path.parent/manifest['file']).read_bytes()
    if sha(packed)!=manifest['gzip_sha256']:raise ValueError('PACKAGE_SHA_MISMATCH')
    raw=gzip.decompress(packed)
    if sha(raw)!=manifest['snapshot_sha256']:raise ValueError('SNAPSHOT_SHA_MISMATCH')
    for relative,fingerprint in manifest['public_files'].items():
        path=(root/relative).resolve()
        if not path.is_relative_to((root/'config/wealth').resolve()):raise ValueError('PUBLIC_PATH_INVALID')
        if sha(path.read_bytes())!=fingerprint:raise ValueError('PUBLIC_EVIDENCE_SHA_MISMATCH')
    from backend.app.schemas.wealth_portfolio_data import DataSnapshot
    snapshot=DataSnapshot.model_validate_json(raw)
    if snapshot.version!=version or snapshot.daily_version!=manifest['daily_version']:raise ValueError('PACKAGE_VERSION_CONFLICT')
    return save(snapshot,root/'.deps/wealth_portfolio_data/snapshots')

def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='action',required=True)
    e=s.add_parser('export');e.add_argument('--version',required=True);e.add_argument('--output',type=Path,default=ROOT/'.deps/wealth_portfolio_data/packages')
    i=s.add_parser('install');i.add_argument('--manifest',required=True,type=Path)
    a=p.parse_args()
    result=export(ROOT,a.version,a.output) if a.action=='export' else install(ROOT,a.manifest)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
