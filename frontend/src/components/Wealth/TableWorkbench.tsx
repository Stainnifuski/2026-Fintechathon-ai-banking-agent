import {useMemo,useState} from 'react';
import * as w from '../../services/wealth';
import OrderTable,{bankEligibility} from './OrderTable';
import type {EligibilityLoader} from './OrderTable';
import PositionTable from './PositionTable';
import {orderTableData,positionTableData} from './tableData';
import type {ProductDetailLoader} from './ProductDetail';
import RedemptionDialog from './RedemptionDialog';
import ReturnsView from './ReturnsView';
import type {ReturnsPreviewSource} from './ReturnsView';
import type {WealthReturns} from '../../services/wealthReturns';

interface Props {assets:w.Assets;scopeKey:string;panel?:'orders'|'positions'|'both';onActivity:()=>void;blockedAccounts?:string[];
  loadEligibility?:EligibilityLoader;loadDetail?:ProductDetailLoader;fixtureOnly?:boolean;refreshKey?:number;sessionId?:string;returnsPreview?:ReturnsPreviewSource}
const errorText=(e:unknown)=>(e as {response?:{data?:{detail?:{message?:string}}}})?.response?.data?.detail?.message??(e instanceof Error?e.message:'操作失败，请保留原号核对');
export default function TableWorkbench({assets,scopeKey,panel='both',onActivity,blockedAccounts=[],loadEligibility=bankEligibility,loadDetail,fixtureOnly=false,refreshKey=0,sessionId,returnsPreview}:Props){
  const businessDate=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(assets.business_at));
  const expectedAssetScope=assets.asset_summary?.asset_scope_id;
  const bankReturns=useMemo<ReturnsPreviewSource>(()=>({actualBank:true,scenarios:[],notice:'仅查询当前授权范围，不产生交易。',
    defaultDate:businessDate,scenarioNotice:()=> '整体和单产品分别核对；已赎回产品保留在历史贡献中。',
    load:async query=>{
      const result=await w.get<WealthReturns>('/returns?'+new URLSearchParams(Object.entries(query).filter(([,value])=>value!==undefined).map(([key,value])=>[key,String(value)])).toString());
      if(!expectedAssetScope||result.asset_scope_id!==expectedAssetScope)throw new Error('授权账户范围已变化，请先刷新登录账户记录，再查看收益。');
      return result;
    }
  }),[scopeKey,businessDate,refreshKey,expectedAssetScope]);
  const returnsSource=returnsPreview??(!fixtureOnly?bankReturns:undefined);
  const [cancel,setCancel]=useState<{scope:string;rows:{row:w.OrderRow;eligibility:w.CancellationEligibility}[]}|null>(null);
  const [consent,setConsent]=useState(false);const [notice,setNotice]=useState('');const [busy,setBusy]=useState(false);
  const [redeem,setRedeem]=useState<{scope:string;keys:string[]}|null>(null);
  const [returns,setReturns]=useState<{scope:string;product?:string}|null>(null);
  const orders=orderTableData(assets,scopeKey),positions=positionTableData(assets,scopeKey);
  const currentRedeem=redeem?.scope===scopeKey?positions.rows.filter(row=>redeem.keys.includes(row.position_key)):[];
  const act=async(work:()=>Promise<void>)=>{setBusy(true);setNotice('');try{await work();}catch(error){setNotice(errorText(error));}finally{setBusy(false);}};
  const prepareCancel=(rows:w.OrderRow[])=>void act(async()=>{
    setCancel(null);setConsent(false);const views=await Promise.all(rows.map(async row=>({row,eligibility:await loadEligibility(row.operation_id)})));
    if(views.some(v=>v.eligibility.operation_id!==v.row.operation_id||v.eligibility.order_digest!==v.row.digest))throw new Error('订单或撤单资格已变化，请刷新后重新核对');
    setCancel({scope:scopeKey,rows:views});
  });
  const selectRedeem=(rows:w.PositionRow[])=>{
    setRedeem({scope:scopeKey,keys:rows.map(row=>row.position_key)});setNotice('');
  };
  return <section aria-label="订单与持仓工作台">
    {notice&&<p role="alert" className="consumer-warning">{notice}</p>}
    <div hidden={panel==='positions'}><OrderTable data={orders} loadEligibility={loadEligibility} loadDetail={loadDetail} onCancel={prepareCancel} blockedAccounts={blockedAccounts} refreshKey={refreshKey}/></div>
    <div hidden={panel==='orders'||(returns?.scope===scopeKey&&!!returnsSource)}><PositionTable data={positions} loadDetail={loadDetail} onRedeem={selectRedeem} onReturns={product=>setReturns({scope:scopeKey,product})} blockedAccounts={blockedAccounts}/></div>
    {returns?.scope===scopeKey&&returnsSource&&<div hidden={panel==='orders'}><ReturnsView key={`${scopeKey}:${returns.product??'all'}`} source={returnsSource} initialProduct={returns.product} onClose={()=>setReturns(null)}/></div>}
    {cancel?.scope===scopeKey&&<div className="wealth-modal"><section role="dialog" aria-modal="true" aria-label="取消订单资格与确认" className="card"><h3>核对取消订单</h3><p>逐笔取消，结果分别记录。只释放原资金或份额占用，不增加账面余额，不产生退款流水。</p>
      {cancel.rows.map(({row,eligibility})=><article className="table-operation-row" key={row.operation_id}><strong>{row.product_name} · {row.operation_id}</strong><p>{row.direction==='purchase'?w.money(row.amount_fen):`${row.units}份`} · {eligibility.eligibility} · {eligibility.reason}</p><p>{eligibility.consequence}</p></article>)}
      {notice&&<p role="alert">{notice}</p>}
      {fixtureOnly?<p>F23只展示资格样例，不撤销银行订单。</p>:<><label><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)} disabled={busy}/>本人确认取消上述允许撤销的完整订单</label><button disabled={busy||!consent||!cancel.rows.some(v=>v.eligibility.eligibility==='ALLOWED'&&v.row.disclosure_version!=null&&!blockedAccounts.includes(v.row.account_id))} onClick={()=>void act(async()=>{
        const results:string[]=[];
        for(const {row,eligibility} of cancel.rows){
          if(eligibility.eligibility!=='ALLOWED'||blockedAccounts.includes(row.account_id)||row.disclosure_version==null){results.push(`${row.operation_id}：未执行，${row.disclosure_version==null?'缺少订单披露版本':eligibility.reason}`);continue;}
          try{const fact=await w.post<w.Order>(`/orders/${encodeURIComponent(row.operation_id)}/cancel`,{digest:eligibility.order_digest,disclosure_version:row.disclosure_version,accepted:true});results.push(`${row.operation_id}：${fact.status}`);}
          catch(error){results.push(`${row.operation_id}：${errorText(error)}；按原号刷新核对`);}
        }
        setCancel(null);setConsent(false);setNotice(results.join('；'));onActivity();
      })}>确认取消允许撤销的订单</button></>}
      <button className="secondary" disabled={busy} onClick={()=>setCancel(null)}>关闭取消核对</button>
    </section></div>}
    {!!currentRedeem.length&&<RedemptionDialog key={`${scopeKey}:${sessionId??'standalone'}`} rows={currentRedeem} contextId={assets.context_id??'legacy-v1'} fixtureOnly={fixtureOnly} sessionId={sessionId} blockedAccounts={blockedAccounts} onClose={()=>setRedeem(null)} onActivity={onActivity}/>}
  </section>;
}
