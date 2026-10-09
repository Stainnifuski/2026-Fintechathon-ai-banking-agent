import {useState} from 'react';
import * as w from '../../services/wealth';
import OrderTable,{bankEligibility} from './OrderTable';
import type {EligibilityLoader} from './OrderTable';
import PositionTable from './PositionTable';
import {orderTableData,positionTableData,scaledUnits} from './tableData';
import type {ProductDetailLoader} from './ProductDetail';
import SingleTradePreview from './SingleTradePreview';
import ReturnsView from './ReturnsView';
import type {ReturnsPreviewSource} from './ReturnsView';

interface Props {assets:w.Assets;scopeKey:string;panel?:'orders'|'positions'|'both';onActivity:()=>void;blockedAccounts?:string[];
  loadEligibility?:EligibilityLoader;loadDetail?:ProductDetailLoader;fixtureOnly?:boolean;refreshKey?:number;onRedeemSingle?:(row:w.PositionRow)=>void;returnsPreview?:ReturnsPreviewSource}
const errorText=(e:unknown)=>(e as {response?:{data?:{detail?:{message?:string}}}})?.response?.data?.detail?.message??(e instanceof Error?e.message:'操作失败，请保留原号核对');
export default function TableWorkbench({assets,scopeKey,panel='both',onActivity,blockedAccounts=[],loadEligibility=bankEligibility,loadDetail,fixtureOnly=false,refreshKey=0,onRedeemSingle,returnsPreview}:Props){
  const [cancel,setCancel]=useState<{scope:string;rows:{row:w.OrderRow;eligibility:w.CancellationEligibility}[]}|null>(null);
  const [consent,setConsent]=useState(false);const [notice,setNotice]=useState('');const [busy,setBusy]=useState(false);
  const [redeem,setRedeem]=useState<{scope:string;keys:string[]}|null>(null);const [inputs,setInputs]=useState<Record<string,string>>({});
  const [preview,setPreview]=useState<{scope:string;data:w.Preview}|null>(null);
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
    if(rows.length===1&&onRedeemSingle){onRedeemSingle(rows[0]);return;}
    setRedeem({scope:scopeKey,keys:rows.map(row=>row.position_key)});setInputs({});setNotice('');
  };
  return <section aria-label="订单与持仓工作台">
    {notice&&<p role="alert" className="consumer-warning">{notice}</p>}
    <div hidden={panel==='positions'}><OrderTable data={orders} loadEligibility={loadEligibility} loadDetail={loadDetail} onCancel={prepareCancel} blockedAccounts={blockedAccounts} refreshKey={refreshKey}/></div>
    <div hidden={panel==='orders'||(returns?.scope===scopeKey&&!!returnsPreview)}><PositionTable data={positions} loadDetail={loadDetail} onRedeem={selectRedeem} onReturns={product=>setReturns({scope:scopeKey,product})} blockedAccounts={blockedAccounts}/></div>
    {returns?.scope===scopeKey&&returnsPreview&&<div hidden={panel==='orders'}><ReturnsView key={`${scopeKey}:${returns.product??'all'}`} source={returnsPreview} initialProduct={returns.product} onClose={()=>setReturns(null)}/></div>}
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
    {!!currentRedeem.length&&<div className="wealth-modal"><section role="dialog" aria-modal="true" aria-label="已选持仓赎回输入" className="card"><h3>填写赎回份额</h3><p>每只产品分别填写，不自动均摊；可赎上限不等于全部持仓。按市值输入将在 B4 接入。</p>
      {notice&&<p role="alert">{notice}</p>}
      {currentRedeem.map(row=><article className="table-operation-row" key={row.position_key}><strong>{row.product_name} · {row.account_id} · v{row.product_version}</strong><p>持有 {row.units}份 · 可赎 {row.redeemable_units}份 · 锁定 {row.locked_units} · 占用 {row.held_units}</p>
        <label>本只赎回份额<input aria-label={`${row.position_key}赎回份额`} inputMode="decimal" value={inputs[row.position_key]??''} onChange={e=>setInputs(s=>({...s,[row.position_key]:e.target.value}))}/></label>
        <button disabled={busy||!inputs[row.position_key]||scaledUnits(row.redeemable_units)===0n||blockedAccounts.includes(row.account_id)||row.disclosure_version==null} onClick={()=>void act(async()=>{
          const count=scaledUnits(inputs[row.position_key]);if(count<=0n||count>scaledUnits(row.redeemable_units))throw new Error('份额须为正且不超过本只可赎数量；不会自动缩小');
          if(fixtureOnly){setNotice('F23份额输入有效，仅用于交互核对；样例不会调用银行预览或生成订单。');return;}
          const result=await w.post<w.Preview>('/previews',{operation_id:'WO'+crypto.randomUUID().replace(/-/g,''),direction:'redeem',product_id:row.product_id,product_version:row.product_version,disclosure_version:row.disclosure_version,account_id:row.account_id,amount_fen:0,units:inputs[row.position_key]});
          setPreview({scope:scopeKey,data:result});
        })}>预览本只普通赎回</button>
      </article>)}<button className="secondary" disabled={busy} onClick={()=>setRedeem(null)}>关闭赎回输入</button>
    </section></div>}
    {preview?.scope===scopeKey&&<SingleTradePreview key={preview.data.authorization_id} preview={preview.data} onClose={()=>setPreview(null)} onActivity={()=>{setRedeem(null);onActivity();}}/>}
    {returns?.scope===scopeKey&&!returnsPreview&&<div className="wealth-modal"><section role="dialog" aria-modal="true" aria-label="实际收益接入说明" className="card"><h3>{returns.product?`${returns.product} · 单产品实际收益`:'整体实际收益'}</h3><p>收益视图已在独立开发预览提供契约样例；登录账户的真实历史收益计算将在 B3d 后续接入，包含已赎回部分，并提供日／月／年／累计和日期切换。当前浮动收益与产品净值涨跌不能替代此结果。</p><button className="secondary" onClick={()=>setReturns(null)}>关闭收益说明</button></section></div>}
  </section>;
}
