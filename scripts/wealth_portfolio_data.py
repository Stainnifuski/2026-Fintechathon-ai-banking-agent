"""离线资料入口；仅消费本地原料，不调用网络/模型/采集队列。"""
import argparse
import json
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from backend.app.services.orchestration.wealth_portfolio_data import build,save,read,utc,inventory
from backend.app.integrations.wealth_portfolio_data import validate_evidence
from backend.app.services.orchestration.wealth_portfolio_evidence import register,derive


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['build','read','coverage','validate-evidence','register-evidence','derive'])
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--raw-root',type=Path,help='optional existing A1/Step09 cache root; no copying or collection')
    parser.add_argument('--as-of');parser.add_argument('--version');parser.add_argument('--input',type=Path)
    parser.add_argument('--bank-capture');parser.add_argument('--bank-sha256')
    args=parser.parse_args();root=args.root.resolve();directory=root/'.deps/wealth_portfolio_data/snapshots'
    # fail-closed：包括证据登记验证也不能联网。
    def denied(*args,**kwargs):raise RuntimeError('NETWORK_FORBIDDEN_OFFLINE_DATA')
    socket.socket=denied;socket.create_connection=denied
    started=time.monotonic()
    if args.command in ('validate-evidence','register-evidence'):
        if not args.input:parser.error('--input required')
        manifest=json.loads((root/'config/wealth/portfolio_evidence_manifest.json').read_text(encoding='utf-8-sig'))
        if args.command=='register-evidence' and args.version:
            base=read(root,directory,args.version);universe=[{'code':p.product_id} for p in base.products if p.source_kind=='real']
        else:universe,_=inventory(root)
        if args.command=='register-evidence':
            payload=json.loads(args.input.read_text(encoding='utf-8-sig'))
            print(json.dumps(register(root,payload if isinstance(payload,list) else [payload],{r['code'] for r in universe}),ensure_ascii=False));return
        record=validate_evidence(root,json.loads(args.input.read_text(encoding='utf-8-sig')),manifest['authority_sources'],{row['code'] for row in universe})
        print(json.dumps(record.model_dump(mode='json'),ensure_ascii=False));return
    if args.command=='derive':
        if not all((args.as_of,args.version,args.bank_capture,args.bank_sha256)):parser.error('derive requires --as-of --version --bank-capture --bank-sha256')
        snapshot=derive(root,args.version,args.bank_capture,args.bank_sha256,utc(args.as_of));save(snapshot,directory)
    elif args.command=='build':
        if not args.as_of:parser.error('--as-of with timezone required')
        snapshot=build(root,utc(args.as_of),raw_root=args.raw_root);index=save(snapshot,directory)
    else:
        if not args.version:parser.error('--version required; no silent latest fallback')
        snapshot=read(root,directory,args.version);index={'version':snapshot.version}
    result={'version':snapshot.version,'fixed_version':snapshot.fixed_version,'daily_version':snapshot.daily_version,
        'as_of':snapshot.as_of.isoformat(),'cache_read_at':datetime.now(timezone.utc).isoformat(),'product_count':len(snapshot.products),'raw_references':len(snapshot.input_references),
        'elapsed_seconds':round(time.monotonic()-started,3),'coverage':snapshot.coverage,'external_requests':0}
    if args.command=='read':result['example']=snapshot.products[0].model_dump(mode='json')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
