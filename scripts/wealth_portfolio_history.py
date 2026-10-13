"""A-only freeze/evaluate CLI. All inputs local and protected; no collector/API calls."""
import argparse
import gzip
import hashlib
import json
import math
import re
import socket
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from html import unescape
from zoneinfo import ZoneInfo
from backend.app.integrations.wealth_collection_cache import read_protected_bytes,read_protected_json
from backend.app.domain.wealth_portfolio_acceptance import parse_history_series,total_return_metrics,require_point_in_time
from backend.app.domain.wealth_portfolio_allocation import ETA,DELTA,PARAMETERS
from backend.app.domain.wealth_portfolio_projection import PRODUCT_ETA
from scripts.wealth_portfolio_acceptance import history_manifest

DIRECTORY='.deps/wealth_portfolio_step09'
VERSION='38005c79535cb83a2d5be663d8a7a1812ba21946fe8f9fa2ab21ca80aea338a9'
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(value):return hashlib.sha256(value).hexdigest()
def reference(root,path):return {'path':path.relative_to(root).as_posix(),'sha256':sha(path.read_bytes())}
def protected(root,ref,directory=DIRECTORY):return read_protected_bytes(root,ref['path'],ref['sha256'],directory)
def variable(text,name):
    match=re.search(r'\bvar\s+'+re.escape(name)+r'\s*=\s*',text)
    if not match:raise ValueError('PUBLIC_FIELD_MISSING:'+name)
    return json.JSONDecoder().raw_decode(text[match.end():])[0]
def public_nav(text,code,cutoff):
    if variable(text,'fS_code')!=code:raise ValueError('HISTORY_SHARE_MISMATCH')
    if variable(text,'ishb'):raise ValueError('MONEY_YIELD_NOT_NAV')
    raw=variable(text,'Data_netWorthTrend');rows=[]
    for r in raw:
        stamp=r.get('x')
        if type(stamp) not in (int,float) or not math.isfinite(stamp):raise ValueError('HISTORY_DATE_MISSING')
        rows.append({'date':datetime.fromtimestamp(stamp/1000,timezone.utc).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat(),'nav':r.get('y')})
    return parse_history_series(rows,'date','nav',unit='CNY_per_share',kind='unit_nav',cutoff=cutoff)

def public_text(data):
    """Honor document-declared charset; no silent replacement or guessed returns."""
    declared=re.search(rb'<meta\b[^>]*charset\s*=\s*["\']?([\w-]+)',data[:10000],re.I)
    encoding=declared.group(1).decode('ascii').lower() if declared else 'utf-8-sig'
    allowed={'gb2312':'gb18030','gbk':'gb18030','gb18030':'gb18030','utf-8':'utf-8-sig','utf8':'utf-8-sig','utf-8-sig':'utf-8-sig'}
    if encoding not in allowed:raise ValueError('PUBLIC_ENCODING_UNKNOWN')
    return data.decode(allowed[encoding]),encoding

def public_review(root,ledger):
    """Method/event observations, never certify completeness from an empty table."""
    observations=[]
    for r in ledger:
        data=protected(root,r);text,encoding=public_text(data)
        item={k:r[k] for k in ('code','kind','url','observed_at','sha256','path','published_on','applicable_on')}
        item['decoded_encoding']=encoding
        if r['kind']=='method':
            plain=re.sub(r'\s+',' ',unescape(re.sub(r'<[^>]+>',' ',text)))
            keys=['累计净值=','基金复权净值','交易日晚上','分红方式','不填写默认为']
            item['locations']=[{'needle':k,'offset_text':plain.find(k),'excerpt':plain[max(0,plain.find(k)-20):plain.find(k)+100]} for k in keys if k in plain]
            if 'chinaamc' in r['url']:
                # Published date is taken from visible HTML, not inferred from URL.
                found=re.search(r'<div\s+class="detail-time">\s*(2021-12-23)\s*</div>',text)
                item['published_on']=found.group(1) if found else None
            item['use']='NAV/accumulated/reinvested definitions only; does not attest LJSYLZS algorithm'
        elif r['kind']=='events':
            tables=re.findall(r'<table\b[^>]*>.*?</table>',text,re.S|re.I);summary=[]
            for table in tables:
                if not any(k in table for k in ('除息日','拆分日','拆分折算日')):continue
                clean=re.sub(r'\s+',' ',unescape(re.sub(r'<[^>]+>',' ',table)))
                dates=re.findall(r'\d{4}-\d{2}-\d{2}',clean)
                summary.append({'kind':'dividend' if '除息日' in table else 'split','dates_min':min(dates) if dates else None,'dates_max':max(dates) if dates else None,'date_tokens':len(dates),'rows':len(re.findall('<tr',table,re.I))-1,'explicit_no_events':'暂无' in clean,'unit':'CNY_per_10_shares' if '每10份分红' in clean else 'ratio_unverified','excerpt':clean[:150]})
            item['tables']=summary;item['completeness']='unproved: page table / no-event message is not complete linked event methodology'
        elif r['kind']=='cumulative':
            raw=json.loads(text);item['series']=[{'name':s.get('name'),'rows':len(s.get('data',[])),'first':s.get('data',[])[:1]} for s in raw.get('Data',[])]
            item['use']='direct upstream series selector observed; unit/anchor/dividend/split algorithm not documented by response'
        elif r['kind']=='history':
            item['bound_code']=variable(text,'fS_code');item['bound_name']=variable(text,'fS_name');item['wrapper_test_data_comment']='测试数据' in text[:100]
            item['use']='public market endpoint observations (not local simulated prices); wrapper comment retained; not issuer-certified total-return index'
        else:item['use']='AKShare branch implementation, not upstream methodology'
        observations.append(item)
    return observations

def inventory(root):
    previous_path=root/'.deps/wealth_portfolio_acceptance/evaluation_manifest.json'
    previous=json.loads(previous_path.read_text(encoding='utf-8'))
    old={r['path']:r for r in previous['samples']};m=history_manifest(root);rows=[];codes=set()
    for s in m['samples']:
        # Against previously frozen fingerprint, not self-attestation of changed cache.
        if s['path'] not in old:raise ValueError('UNFROZEN_HISTORY_INPUT')
        ref={'path':s['path'],'sha256':old[s['path']]['sha256']}
        raw=read_protected_json(root,ref['path'],ref['sha256'],'.deps/a1_collection/raw')
        if raw['function']!=s['function'] or raw['kwargs']!=s['kwargs']:raise ValueError('HISTORY_BINDING_MISMATCH')
        recs=raw.get('records',[]);dates={};numeric={}
        for field in raw.get('columns',[]):
            vs=[r.get(field) for r in recs];parsed=[];invalid=0
            for v in vs:
                if isinstance(v,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',v):
                    try:datetime.strptime(v,'%Y-%m-%d');parsed.append(v)
                    except ValueError:invalid+=1
            if parsed or any(t in field for t in ('日期','登记日','除息日','发放日','拆分日')):
                dates[field]={'valid':len(parsed),'missing_or_invalid':len(vs)-len(parsed),'invalid_date':invalid,'duplicates':len(parsed)-len(set(parsed)),'ascending':parsed==sorted(parsed),'start':min(parsed) if parsed else None,'end':max(parsed) if parsed else None,'after_observation':sum(v>raw['observed_at'][:10] for v in parsed)}
            nums=[v for v in vs if type(v) in (int,float)]
            if nums:
                numeric[field]={'numeric':len(nums),'missing_or_nonnumeric':len(vs)-len(nums),'nonfinite':sum(not math.isfinite(v) for v in nums),'nonpositive':sum(v<=0 for v in nums),'unit':('CNY_per_share' if field in ('单位净值','累计净值') else 'unproven' if field=='累计收益率' else 'field_specific_not_return')}
        code=s['kwargs'].get('symbol');codes.add(code) if code else None
        kind=('unit_nav' if s['branch']=='单位净值走势' else 'accumulated_nav' if s['branch']=='累计净值走势' else 'cumulative_return_unproved' if s['branch']=='累计收益率走势' else 'ranking' if '排名' in s['branch'] else 'money_yield' if s['function']=='fund_money_fund_info_em' else 'events')
        rows.append({**s,'kind':kind,'date_columns':dates,'numeric_columns':numeric,'fingerprint_verified':True,'event_completeness':'unproved','publication_times':'unknown','exact_share_code':code})
    return {'logical_jobs':len(rows),'unique_responses':len(set(r['path'] for r in rows)),'exact_symbol_codes':len(codes),'kind_counts':dict(Counter(r['kind'] for r in rows)),'samples':rows,'previous_manifest':reference(root,previous_path)}

def freeze(root):
    out=root/DIRECTORY;inv=inventory(root);targets=json.loads((out/'targets.json').read_text(encoding='utf-8'))
    ledger=json.loads((out/'public_ledger.json').read_text(encoding='utf-8'));samples=[]
    observations=public_review(root,ledger)
    snapshot=root/f'.deps/wealth_portfolio_data/snapshots/{VERSION}.json';idx=json.loads(snapshot.with_suffix('.index.json').read_text(encoding='utf-8'))
    catalog=json.loads(read_protected_bytes(root,snapshot.relative_to(root).as_posix(),idx['sha256'],'.deps/wealth_portfolio_data/snapshots'))
    products={p['product_id']:p for p in catalog['products']}
    for code in targets['codes']:
        p=products[code];old=next((s for s in inv['samples'] if s['exact_share_code']==code and s['kind']=='unit_nav'),None)
        direct=next(r for r in ledger if r['code']==code and r['kind']=='history')
        sample={'code':code,'name':p['name'],'category':p['substantive_category'],'category_status':p['classification_status'],'scope_status':p['scope_status'],'share_class':p['share_class'],'family_id':p['fields']['family_id']['value'],'identity_evidence':p.get('family_evidence',[]),'historical_decision_facts':{},'point_in_time_review':'current catalog category/text/grade not historically applicable; dated family existence announcements do not prove continued sale/risk/text at decision date','total_return_status':'blocked','total_return_gaps':['LJSYLZS adjustment/percent anchor unproved','complete share dividend/split/reinvestment linkage unproved']}
        if old:
            sample.update(input={'path':old['path'],'sha256':old['sha256']},input_kind='old_nav',observed_at=old['observed_at'],date_field='净值日期',value_field='单位净值',kwargs=old['kwargs'],source='AKShare saved pingzhongdata parser',unit='CNY_per_share')
        else:sample.update(input={k:direct[k] for k in ('path','sha256')},input_kind='public_js',observed_at=direct['observed_at'],source=direct['url'],unit='CNY_per_share')
        sample['event_input']=next({k:r[k] for k in ('path','sha256','url','observed_at')} for r in ledger if r['code']==code and r['kind']=='events')
        # Finite preparation records are frozen too; full current page is not historical availability.
        sample['nav_status']='excluded' if p['substantive_category']=='money_market' else 'candidate'
        sample['nav_reason']='money return needs income distribution convention; seven-day rate is not daily compound return' if sample['nav_status']=='excluded' else 'supplemental NAV description only; exact upstream code; no financial risk/offer approval'
        samples.append(sample)
    baseline=json.loads((out/'baseline.json').read_text(encoding='utf-8'))
    manifest={'schema_version':'portfolio-history-evaluation-v2','prepared_at':datetime.now(timezone.utc).isoformat(),'head':baseline['head'],'data_version':VERSION,'daily_version':catalog['daily_version'],'fixed_version':catalog['fixed_version'],'evidence_version':catalog['evidence_version'],'source_snapshot':reference(root,snapshot),'inventory':inv,'samples':samples,'preparation_ledger':reference(root,out/'public_ledger.json'),'targets':reference(root,out/'targets.json'),'public_inputs':[{k:r[k] for k in ('path','sha256','url','kind','code','observed_at','published_on','applicable_on')} for r in ledger if r.get('http')==200],
              'rules':{'window':['2025-01-01','2025-12-31'],'selection':targets['rule'],'alignment':'common observed date intersection only; no fill; non-valuation vs missing unknown','annualization':'none: no proved comparable disclosure calendar','unit_nav_use':'supplemental NAV description, not dividend-inclusive return','available_at':'no historical publication timestamp: not eligible for point-in-time decisions'},
              'model':{'ids':['portfolio-greedy-cholesky-v1','portfolio-bigram-tfidf-80-10-10-v1','portfolio-geometric-projection-v1','portfolio-capped-simplex-v1'],'code': [reference(root,root/f'backend/app/domain/wealth_portfolio_{n}.py') for n in ('dpp','allocation','projection')],'eta':ETA,'eta_product':PRODUCT_ETA,'delta':DELTA,'risk_parameters':PARAMETERS,'dpp_quality':1,'feature_weights':[.8,.1,.1]},
              'public_review':observations,'fixed_scenario':{'initial_wealth_fen':1000000,'cash_weight':.2,'weights_rule':'equal across qualified total-return components, not projected equal','assumptions':{'cash_return':'constant_index_research_assumption','external_fees':'not_modelled','rebalance':'none'},'common_window':None,'research_only':True},
              'out_of_sample':{'split':'2024-12-31T23:59:59+08:00','frequency':'single frozen decision, evaluate calendar 2025','initial_state':'research assumption T=1000000 fen; h=.2/.3/.5; preference R4/p=.625, not production assessment','required_fields':['identity','category','risk','family','channel','sale_permission','objective','strategy','benchmark','price'],'status':'blocked'},
              'evaluation_code':[reference(root,Path(__file__).resolve()),reference(root,root/'backend/app/domain/wealth_portfolio_acceptance.py')]}
    # Determine actual candidate NAV/common dates BEFORE any metric; no return-dependent window.
    dates=[]
    for s in samples:
        try:series=load_series(root,s,manifest['rules']['window']);s['window_observations']=len(series['dates']);s['window_dates_sha256']=sha(canonical(series['dates']));s['window_start']=series['dates'][0] if series['dates'] else None;s['window_end']=series['dates'][-1] if series['dates'] else None
        except ValueError as e:s['nav_status']='excluded';s['nav_reason']=str(e);continue
        if s['nav_status']=='candidate' and len(series['dates'])>=2:dates.append(set(series['dates']))
    common=sorted(set.intersection(*dates)) if dates else []
    manifest['supplemental_nav_common_window']={'start':common[0] if common else None,'end':common[-1] if common else None,'observations':len(common),'dates_sha256':sha(canonical(common))}
    content=canonical(manifest);version=sha(content);path=out/f'evaluation_manifest_{version}.json'
    if path.exists() and path.read_bytes()!=content:raise ValueError('MANIFEST_COLLISION')
    path.write_bytes(content);(out/'latest_manifest.json').write_bytes(canonical({'path':path.relative_to(root).as_posix(),'sha256':version}))
    return {'manifest_sha256':version,'manifest':path.relative_to(root).as_posix(),'inventory':{k:v for k,v in inv.items() if k not in ('samples','previous_manifest')},'nav_candidates':sum(s['nav_status']=='candidate' for s in samples),'supplemental_window':manifest['supplemental_nav_common_window']}

def load_series(root,s,window):
    if s['nav_status']=='excluded':raise ValueError(s['nav_reason'])
    data=protected(root,s['input'],'.deps/a1_collection/raw' if s['input_kind']=='old_nav' else DIRECTORY)
    if s['input_kind']=='old_nav':
        raw=json.loads(gzip.decompress(data))
        if raw['kwargs']!=s['kwargs'] or raw['kwargs']['symbol']!=s['code']:raise ValueError('HISTORY_SHARE_MISMATCH')
        result=parse_history_series(raw['records'],s['date_field'],s['value_field'],unit=s['unit'],kind='unit_nav',cutoff=window[1])
    else:result=public_nav(data.decode('utf-8-sig'),s['code'],window[1])
    keep=[(d,v) for d,v in zip(result['dates'],result['values']) if d>=window[0]]
    result['dates']=[d for d,v in keep];result['values']=[v for d,v in keep]
    return result

def evaluate(root,ref):
    m=json.loads(protected(root,ref));
    for s in m['inventory']['samples']:
        raw=read_protected_json(root,s['path'],s['sha256'],'.deps/a1_collection/raw')
        if raw['function']!=s['function'] or raw['kwargs']!=s['kwargs']:raise ValueError('HISTORY_BINDING_MISMATCH')
    for r in [m['source_snapshot'],*m['model']['code'],*m['evaluation_code']]:
        directory='.deps/wealth_portfolio_data/snapshots' if r==m['source_snapshot'] else str(Path(r['path']).parent)
        protected(root,r,directory)
    protected(root,m['targets']);protected(root,m['preparation_ledger'])
    for r in m['public_inputs']:protected(root,r)
    nav=[];pit=[]
    for s in m['samples']:
        protected(root,s['event_input'])
        try:
            series=load_series(root,s,m['rules']['window'])
            if len(series['dates'])<2:raise ValueError('WINDOW_TOO_SHORT')
            if sha(canonical(series['dates']))!=s['window_dates_sha256']:raise ValueError('WINDOW_BINDING_CHANGED')
            raw=total_return_metrics(series['dates'],series['values'],consecutive_trading_days=False)
            metrics={'nav_change':raw['total_return'],'observation_volatility':raw['daily_volatility'],'maximum_nav_drawdown':raw['maximum_drawdown'],'annualized_volatility':None}
            nav.append({'code':s['code'],'status':'available','data_kind':'real_nav_description_not_total_return','metric':metrics,'window':{k:raw[k] for k in ('start','end','observations')},'assumptions':{'dividend_adjustment':'none','split_adjustment':'none','annualization':'not established','interval':'successive disclosed observations; not proven daily'},'input_sha256':s['input']['sha256']})
        except ValueError as e:nav.append({'code':s['code'],'status':'blocked','data_kind':'real_nav_description_not_total_return','metric':None,'window':None,'reason':str(e)})
        pit.append({'code':s['code'],**require_point_in_time(s['historical_decision_facts'],m['out_of_sample']['split'],m['out_of_sample']['required_fields'])})
    return {'schema_version':'portfolio-history-result-v2','manifest_sha256':ref['sha256'],'network_requests':0,'single_product_total_return':{'status':'blocked','data_kind':'real_dividend_inclusive_return','qualified_samples':0,'metric':None,'window':None,'gaps':[{'code':s['code'],'missing':s['total_return_gaps']} for s in m['samples']]},'supplemental_nav':nav,
            'fixed_buy_hold':{'status':'blocked','data_kind':'hypothetical_fixed_portfolio','metric':None,'window':None,'samples':[],'assumptions':m['fixed_scenario'],'reason':'no verified component wealth indices; NAV-only calculation cannot close fixed total-return scenario'},
            'out_of_sample':{'status':'blocked','data_kind':'model_out_of_sample','metric':None,'window':None,'decision':m['out_of_sample'],'point_in_time_gaps':pit,'reason':'historical decision-time inputs absent; current undated text/risk not backfilled; actual initial client holdings NOT an extra prerequisite'},'production_parameters_unchanged':True}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['freeze','evaluate']);parser.add_argument('--manifest');parser.add_argument('--output-dir',help='evaluation results only; e.g. /tmp/portfolio-step09 on read-only container');args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];out=root/DIRECTORY;out.mkdir(exist_ok=True)
    def denied(*a,**k):raise RuntimeError('OFFLINE_CONNECTION_FORBIDDEN')
    old=(socket.socket.connect,socket.socket.connect_ex,socket.create_connection);socket.socket.connect=socket.socket.connect_ex=socket.create_connection=denied
    try:
        if args.action=='freeze':print(json.dumps(freeze(root),ensure_ascii=False));return
        ref=json.loads((out/'latest_manifest.json').read_text()) if not args.manifest else {'path':args.manifest,'sha256':Path(args.manifest).stem.removeprefix('evaluation_manifest_')}
        a=evaluate(root,ref);b=evaluate(root,ref);data=canonical(a)
        if data!=canonical(b):raise ValueError('OFFLINE_REPEAT_DIFFERENCE')
        result_dir=Path(args.output_dir) if args.output_dir else out;result_dir.mkdir(parents=True,exist_ok=True)
        digest=sha(data);path=result_dir/f'evaluation_result_{digest}.json';path.write_bytes(data)
        (result_dir/'repeat.json').write_bytes(canonical({'manifest':ref,'result':path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path),'sha256':digest,'repetitions':2,'identical':True,'external_requests':0}))
        print(json.dumps({'result_sha256':digest,'manifest_sha256':ref['sha256'],'repeated_identical':True,'nav_available':sum(s['status']=='available' for s in a['supplemental_nav']),'total_return':'blocked','fixed':'blocked','out_of_sample':'blocked','network_requests':0},ensure_ascii=False))
    finally:socket.socket.connect,socket.socket.connect_ex,socket.create_connection=old
if __name__=='__main__':main()
