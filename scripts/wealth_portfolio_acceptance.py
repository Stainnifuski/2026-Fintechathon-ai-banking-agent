"""只读本地独立验收入口；不采集、不联网、不接公共交易。"""
import argparse
import asyncio
import gzip
import hashlib
import json
import math
import socket
import sqlite3
from collections import Counter
from copy import deepcopy
from pathlib import Path
from backend.app.domain.wealth_portfolio_acceptance import allocation_comparison,product_comparison
from backend.app.domain.wealth_portfolio_allocation import ETA,DELTA,PARAMETERS,ModelError
from backend.app.domain.wealth_portfolio_projection import PRODUCT_ETA,WeightFamily,WeightPart
from backend.app.schemas.wealth_portfolio import SelectionItem
from backend.app.schemas.wealth_portfolio_lifecycle import PortfolioCreate,PortfolioRecommendation,SelectedShare,CandidateBinding
from backend.app.schemas.wealth_portfolio_candidates import CandidateFilter
from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateError
from backend.app.services.orchestration.wealth_portfolio import SnapshotError
from backend.app.services.orchestration.wealth_portfolio_data import digest
from tests.fixtures.wealth_portfolio_lifecycle_v1 import lifecycle_fixture
from tests.fixtures.wealth_portfolio_candidates_v1 import field,NOW
from scripts.wealth_portfolio_lifecycle_check import request,key

U='fixture-user';C='fixture-context'

def normalized(plan):
    # 完整业务输出参与重复核对，不能省略时点/评级/资料/金额/版本。
    value=plan.model_dump(mode='json');value.pop('portfolio_id');return value

def record_hash(service,plan):
    r=service.store.rows[plan.portfolio_id][0]
    return digest({'user':r.user_id,'access':r.access,'fingerprint':r.fingerprint,'plan':normalized(r.plan),'metadata':r.metadata,'explicit':[v.model_dump(mode='json') for v in r.explicit]})

async def representative_flow():
    s,*_=lifecycle_fixture();p=await s.create(U,PortfolioCreate(context_id=C,growth_ratio_ex_cash='.625'));out={'create':normalized(p)}
    group=await s.recommend(U,PortfolioRecommendation(context_id=C,filter=CandidateFilter(mode='general')))
    refs=[SelectedShare(product_id=f'Q{i:02}A',product_version=1,share_class='A') for i in range(6)]
    chosen=await s.recommend(U,PortfolioRecommendation(context_id=C,filter=CandidateFilter(mode='general'),selected=refs));v=chosen.session_binding
    selected=await s.create(U,PortfolioCreate(context_id=C,selected=refs,candidate_binding=CandidateBinding(session_id=v['session_id'],expected_version=v['version'],filter=CandidateFilter(mode='general'))))
    out['selected_create']=normalized(selected);out['recommend_family_ids']=[r.family_id for r in group.items]
    out['get_identical']=await s.get(U,p.portfolio_id,C)==p
    original=deepcopy(s.store.rows[p.portfolio_id][0].metadata['validation_contract']['calculation'])
    for pref in ('.626','.627','.628'):
        p=await s.revise(U,p.portfolio_id,request(p,growth_ratio_ex_cash=pref))
        assert s.store.rows[p.portfolio_id][0].metadata['validation_contract']['calculation']==original
    out['small_adjustments']=normalized(p);before=record_hash(s,p)
    try:await s.revise(U,p.portfolio_id,request(p,growth_ratio_ex_cash='.75'))
    except (CandidateError,ModelError) as e:out['outside_original_U']={'code':e.code,'before':before,'after':record_hash(s,p)}
    else:raise AssertionError('Repeated adjustments expanded original U')
    p=await s.revise(U,p.portfolio_id,request(p,revision_kind='preference',growth_ratio_ex_cash='.65'));out['preference']=normalized(p)
    before=record_hash(s,p)
    try:await s.revise(U,p.portfolio_id,request(p,expected_version=1))
    except CandidateError as e:out['conflict']={'code':e.code,'before':before,'after':record_hash(s,p)}
    r2,data,facts,*_=lifecycle_fixture(2);next(r for r in data.products if r.product_id=='Q07C').fields['formal_risk']=field(4,'risk_level');facts.offers['Q07C'].risk_level=4
    old=await r2.create(U,PortfolioCreate(context_id=C));before=record_hash(r2,old)
    try:await r2.revise(U,old.portfolio_id,request(old,items=[SelectionItem(product_id='Q07C',product_version=1,share_class='C',target_ratio_total='0')]))
    except CandidateError as e:out['risk']={'code':e.code,'before':before,'after':record_hash(r2,old)}
    else:raise AssertionError('Risk gate bypass')
    # 无plan换批保留选择；不忽略context/事实指纹，只归一化随机ID。
    no,*_=lifecycle_fixture();g=await no.recommend(U,PortfolioRecommendation(context_id=C,filter=CandidateFilter(mode='general'),selected=refs));bind=g.session_binding
    nxt=await no.recommend(U,PortfolioRecommendation(context_id=C,filter=CandidateFilter(mode='general'),selected=refs,cursor=g.next_cursor,candidate_binding=CandidateBinding(session_id=bind['session_id'],expected_version=bind['version'],filter=CandidateFilter(mode='general'))))
    state=no.provider.candidates.sessions[bind['session_id']]
    out['no_plan_next']={'selected':sorted(state.selected),'version':state.version,'family_ids':[x.family_id for x in nxt.items]}
    # 可信旧超风险原份额只允许维持/减持，实际新购规则保持关闭。
    oldsvc,data,facts,*_=lifecycle_fixture(1);next(r for r in data.products if r.product_id=='Q03A').fields['formal_risk']=field(4,'risk_level');facts.offers['Q03A'].risk_level=4
    held=await oldsvc.create(U,PortfolioCreate(context_id=C));out['overrisk_holding']=normalized(held)
    assert all(out[k]['before']==out[k]['after'] for k in ('outside_original_U','conflict','risk'))
    return out

async def repeated_flows():
    runs=[await representative_flow() for _ in range(3)]
    hashes=[digest(r) for r in runs];assert len(set(hashes))==1
    return {'status':'passed','repetitions':3,'hashes':hashes,'normalized_only':['random_portfolio_id','random_candidate_session_id'],'sample':runs[0]}

async def explanations():
    rows=[]
    for label,level,p in [('R1',1,None),('R2',2,None),('R4_explicit',4,'.625'),('hold_only_R1',1,None),('reduced_eligible',4,None),('missing_asset_source',4,None)]:
        s,data,facts,*_=lifecycle_fixture(level)
        if label=='hold_only_R1':
            next(r for r in data.products if r.product_id=='Q03A').fields['formal_risk']=field(4,'risk_level');facts.offers['Q03A'].risk_level=4
        if label=='reduced_eligible':
            for code in ('Q04A','Q04C','Q05A','Q05C','Q07A','Q07C'):facts.offers[code].purchase_allowed=False
        if label=='missing_asset_source':s.provider.asset_source=None
        try:
            plan=await s.create(U,PortfolioCreate(context_id=C,growth_ratio_ex_cash=p));r=s.store.rows[plan.portfolio_id][0];m=r.metadata['model'];x=m['continuous_solution']
            active=[list(a) for a in m['request_constraints'] if abs(a[0]*x[0]+a[1]*x[2]-a[2])<=1e-9]
            selection=r.metadata.get('selection')
            text=f"按已核实的虚构R{level}评估与整体资产{plan.basis_fen/100:.2f}元计算，活钱/稳健/进取目标分别为{[b.target_fen for b in plan.buckets]}分。"
            text+=('全活钱政策使稳健和进取比例不适用。' if level==1 else '保留当前持仓影响并满足比例上限；区间是模型允许调整范围，不是收益或置信区间。')
            text+='购买资格独立校验；仅持有份额不能增买。资料差异不等于收益相关性，未评价金融收益。'
            rows.append({'label':label,'status':'passed','facts':{'source':'fixture_only','as_of':plan.as_of.isoformat(),'assessment':facts.assessment.model_dump(mode='json'),'data_version':facts.data_version,'daily_version':facts.daily_version},
                         'plan':normalized(plan),'policy':{'eta':ETA,'eta_product':PRODUCT_ETA,'delta':DELTA,'level':level,'p_requested':p,'p_upper':PARAMETERS[level][2]},
                         'calculation':{'b':m['b'],'h':m['h'],'x_star':x,'active_constraints':active,'integer_amounts':m['amounts_fen'],'ranges':m['ranges'],'slider':m['slider'],'product_projections':r.metadata['product_projections'],'selection':selection},'explanation':text})
        except (CandidateError,ModelError,SnapshotError) as e:
            rows.append({'label':label,'status':'passed' if label=='missing_asset_source' else 'failed','code':e.code,'source':'fixture_only','explanation':'缺少完整资产来源，拒绝构造有效方案；不补零、不自动改产品。'})
    assert all(r['status']=='passed' for r in rows)
    return rows


def sensitivity():
    h=[.2,.3,.5];rows=[]
    for eta in (0,.25,.5):rows.append({'parameter':'eta',**allocation_comparison(4,h,eta=eta)})
    for delta in (.005,.01,.02):rows.append({'parameter':'delta',**allocation_comparison(4,h,delta=delta)})
    delta_rows=[r for r in rows if r['parameter']=='delta'];assert all(r['x_star']==delta_rows[0]['x_star'] for r in delta_rows)
    families=[WeightFamily('old','stable',100000,[WeightPart('original-A',100000,False,True)]),WeightFamily('new','stable',0,[WeightPart('eligible-C',0,True,True)])]
    free=[WeightFamily('old','stable',100000,[WeightPart('original-A',100000,True,True)]),families[1]]
    for eta in (0,.25,.5):rows.append({'parameter':'eta_product',**product_comparison(families,600000,1000000,eta),
                                    'uncapped_same_input':product_comparison(free,600000,1000000,eta)})
    rows.append({'parameter':'unknown_h',**allocation_comparison(4,None,eta=0)})
    assert ETA==PRODUCT_ETA==.25 and DELTA==.01
    return {'status':'passed','formal_parameters_unchanged':True,'rows':rows,'boundary':'comparison_only engineering sensitivity, not provider parameter or financial effect'}


def history_manifest(root):
    """只读已保存原响应，先验收口径；不把累计收益率列自动认证为总回报。"""
    dbfile=root/'.deps/a1_collection/progress.sqlite'
    with sqlite3.connect(dbfile.as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        jobs=[dict(x) for x in db.execute("select function,branch,entity,kwargs,raw_path from jobs where status='success' and function in ('fund_open_fund_info_em','fund_fh_em','fund_cf_em') order by entity,function,branch")]
    evidence=[];paths=set()
    for j in jobs:
        path=(root/'.deps/a1_collection'/j['raw_path']).resolve()
        if not path.is_relative_to((root/'.deps/a1_collection/raw').resolve()):raise ValueError('HISTORY_PATH_DENIED')
        data=path.read_bytes();raw=json.loads(gzip.decompress(data).decode() if data[:2]==b'\x1f\x8b' else data.decode('utf-8-sig'));paths.add(path)
        records=raw.get('records',[]);dates=[]
        for row in records:
            for k,v in row.items():
                if ('日期' in k or k=='净值日期') and isinstance(v,str) and len(v)>=10:
                    try:
                        from datetime import date
                        dates.append(date.fromisoformat(v[:10]).isoformat())
                    except ValueError:pass
        branch=j['branch'];is_event=j['function'] in ('fund_fh_em','fund_cf_em') or '分红' in branch
        reason='event_table_without_verified_complete_share_event_adjustment' if is_event else 'return_definition_or_event_adjustment_not_verified'
        if '排名' in branch:reason='ranking_is_not_return'
        evidence.append({'function':j['function'],'branch':branch,'product_or_year':j['entity'],'kwargs':json.loads(j['kwargs']),'path':path.relative_to(root).as_posix(),'sha256':hashlib.sha256(data).hexdigest(),
                         'observed_at':raw.get('observed_at'),'columns':raw.get('columns',[]),'rows':len(records),'start':min(dates) if dates else None,'end':max(dates) if dates else None,
                         'net_value_kind':branch,'status':'excluded','reason':reason,'metrics':None})
    # 固定所有现存样本，无收益驱动挑选；无合格事件调整证据时不计算收益。
    generator_manifest=root/'config/wealth/data/manifest.json'
    fixed=json.loads(generator_manifest.read_text(encoding='utf-8'))
    local_ak=root/'.deps/wealth-optional/akshare/fund/fund_em.py'
    implementation={'path':local_ak.relative_to(root).as_posix(),'sha256':hashlib.sha256(local_ak.read_bytes()).hexdigest(),
                    'review':'LJSYLZS returns cumulative-return column; stored implementation does not attest dividend reinvestment/split adjustment methodology. fhsp tables and yearly events alone do not prove complete exact-share linkage.'} if local_ak.exists() else {'status':'not_available'}
    return {'schema_version':'portfolio-history-evaluation-v1','created_at':NOW.isoformat(),'status':'blocked','inventory_mode':'all existing successful listed history/event jobs, read-only; not target-universe reduction',
            'logical_jobs':len(jobs),'unique_raw_responses':len(paths),'branch_counts':{f'{f}/{b}':n for (f,b),n in Counter((j['function'],j['branch']) for j in jobs).items()},
            'samples':evidence,'qualified_total_return_samples':0,'common_window':None,'manifest_fixed_before_metrics':True,
            'local_source_implementation_review':implementation,
            'simulated_histories':{'manifest_path':generator_manifest.relative_to(root).as_posix(),'manifest_sha256':hashlib.sha256(generator_manifest.read_bytes()).hexdigest(),
                'data_version':fixed['data_version'],'products':fixed['products'],'source':'simulation','status':'excluded_from_real_financial_evaluation',
                'reason':'generated fictional NAV, not real verified total returns; pre-generated future rows cannot be consumed early','cutoff':NOW.isoformat(),'future_rows_consumed':0},
            'single_product':{'status':'blocked','reason':'No verified total-return definition/reinvestment and complete event linkage'},
            'buy_and_hold':{'status':'blocked','weights':None,'cash_return':'unknown_not_zero','fees':'not_simulated','rebalance':'none','reason':'component total returns unavailable'},
            'out_of_sample':{'status':'blocked','reason':'historical point-in-time risk/family/permission/text/holdings unavailable; current material cannot backfill past'},
            'missing_rules':'no zero fill; no future data; no NAV/accumulated-NAV ratio or money yield substitution',
            'unlock_conditions':['verified total-return index definition or complete dividend/split/reinvestment evidence linked to exact share and dates','observation/calendar completeness for annualization','historical decision-time facts for out-of-sample']}


def source_audit(root):
    build=json.loads((root/'.deps/wealth_portfolio_data/build_result.json').read_text())
    audit=json.loads((root/'.deps/wealth_portfolio_candidates/actual_pool_audit.json').read_text())
    version=build['version'];indexfile=root/f'.deps/wealth_portfolio_data/snapshots/{version}.index.json';index=json.loads(indexfile.read_text())
    snapshot=indexfile.parent/index['path'];assert hashlib.sha256(snapshot.read_bytes()).hexdigest()==index['sha256']
    assert audit['data_version']==version and audit['daily_version']==build['daily_version']
    manifest=root/'config/wealth/portfolio_evidence_manifest.json'
    return {'status':'blocked','version_verified_this_run':True,'data_version':version,'daily_version':build['daily_version'],'as_of':build['as_of'],'snapshot_sha256':index['sha256'],
            'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),'prior_audit_reused_not_recollected':True,'product_count':build['product_count'],'by_source':audit['by_source'],
            'required_dependencies':[
                {'name':'full_asset_summary','fields':'all authorized account_ids, debit book cash, lots valuation, receivable, held, versions','implementation':'AssetSnapshotSource/read_snapshot; read_asset_model_facts','gate':'503 ASSET_SUMMARY_UNAVAILABLE','unlock':'trusted full-scope same-read source'},
                {'name':'formal_assessment_and_bank','fields':'assessment id/version/expiry/capacity/user; exact share/channel risk, permission/limits/holding rules','implementation':'CurrentFactSource/CandidateService.current; qualify_purchase','gate':'503 ASSESSMENT_SOURCE_UNAVAILABLE or closed default portfolio dependency','unlock':'trusted current bank fact source with provenance'},
                {'name':'scope_session_control','fields':'user/context/epoch/session_ref/full scope/can_read/can_edit','implementation':'PortfolioAccessSource/ExistingPortfolioAccess','gate':'503 PORTFOLIO_SOURCE_UNAVAILABLE','unlock':'trusted resolver and independent write-control checks'},
                {'name':'product_material','fields':'risk/share/family/issuer/text dated provenance, daily permission validity','implementation':'ProductIndex/pipeline/registered evidence','gate':'external CandidateGroup unavailable, internal audit no_eligible_products','unlock':'verified evidence; never infer family or rating from names'}],
            'real_metrics':'NA with empty qualified set; not 100% compliance or zero risk','internal_01_action':'protocols/read-only adapters provided; missing external facts cannot be fabricated'}


async def run(root):
    attempts={'upstream':0,'LLM':0,'bank_writes':0,'external_connect_attempts':0}
    old=socket.socket.connect;old_ex=socket.socket.connect_ex;old_create=socket.create_connection
    def blocked(*a,**k):attempts['external_connect_attempts']+=1;raise RuntimeError('OFFLINE_CONNECTION_FORBIDDEN')
    socket.socket.connect=socket.socket.connect_ex=socket.create_connection=blocked
    try:
        result={'source_audit':source_audit(root),'repeat_flows':await repeated_flows(),'explanations':await explanations(),'sensitivity':sensitivity(),'side_effects':attempts}
        manifest=history_manifest(root)
        assert attempts['external_connect_attempts']==0
        return result,manifest
    finally:socket.socket.connect=old;socket.socket.connect_ex=old_ex;socket.create_connection=old_create


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--fixture',action='store_true');args=parser.parse_args()
    if not args.fixture:parser.error('explicit --fixture required; no formal source fallback')
    root=Path(__file__).resolve().parents[1];out=root/'.deps/wealth_portfolio_acceptance';out.mkdir(parents=True,exist_ok=True)
    result,manifest=asyncio.run(run(root))
    # 保存manifest先于任何真实金融指标；当前所有历史样本未通过口径，指标不计算。
    (out/'evaluation_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'repeat_flows':result['repeat_flows']['status'],'explanations':len(result['explanations']),'sensitivity_rows':len(result['sensitivity']['rows']),'history_status':manifest['status'],'history_jobs':manifest['logical_jobs'],'side_effects':result['side_effects']},ensure_ascii=False))

if __name__=='__main__':main()
