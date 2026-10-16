import {useState,useRef,useEffect} from 'react';
import * as w from '../../services/wealth';
import {canCancelOrder} from '../../services/wealthCancellation';
import type {CancellationRow} from '../../services/wealthCancellation';
export type {CancellationRow} from '../../services/wealthCancellation';
const errorText=(e:unknown)=>(e as {response?:{data?:{detail?:{message?:string}}}})?.response?.data?.detail?.message??(e instanceof Error?e.message:'操作失败，请保留原号核对');
export default function OrderCancellationDialog({rows,blockedAccounts=[],fixtureOnly=false,onClose,onComplete}:{
  rows:CancellationRow[];blockedAccounts?:string[];fixtureOnly?:boolean;onClose:()=>void;onComplete:(message:string)=>void;
}){
  const [consent,setConsent]=useState(false),[busy,setBusy]=useState(false);
  const active=useRef(true);
  useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
  const allowed=(view:CancellationRow)=>canCancelOrder(view,blockedAccounts);
  const cancel=async()=>{
    if(busy||!consent||fixtureOnly)return;
    setBusy(true);const results:string[]=[];
    try{
      for(const view of rows){
        if(!active.current)break;
        const {row,eligibility}=view;
        if(!allowed(view)){results.push(`${row.operation_id}：未执行，资格/订单版本未匹配、资料缺失或账户受人工控制`);continue;}
        try{
          const fact=await w.post<w.Order>(`/orders/${encodeURIComponent(row.operation_id)}/cancel`,{digest:eligibility.order_digest,disclosure_version:row.disclosure_version,accepted:true});
          results.push(`${row.operation_id}：${fact.status}`);
        }catch(error){results.push(`${row.operation_id}：${errorText(error)}；按原号刷新核对`);}
      }
      if(active.current)onComplete(results.join('；'));
    }finally{if(active.current)setBusy(false);}
  };
  return <div className="wealth-modal"><section role="dialog" aria-modal="true" aria-label="取消订单资格与确认" className="card"><h3>核对取消订单</h3><p>逐笔取消，结果分别记录。只释放原资金或份额占用，不增加账面余额，不产生退款流水。</p>
    {rows.map(view=><article className="table-operation-row" key={view.row.operation_id}><strong>{view.row.product_name} · {view.row.operation_id}</strong><p>{view.row.direction==='purchase'?w.money(view.row.amount_fen):`${view.row.units}份`} · {view.eligibility.eligibility} · {view.eligibility.reason}</p><p>{view.eligibility.consequence}</p>{!allowed(view)&&<p>此笔暂不可取消：资格/版本未匹配、资料缺失或账户受人工控制。请刷新订单后核对。</p>}</article>)}
    {fixtureOnly?<p>F23只展示资格样例，不撤销银行订单。</p>:<><label><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)} disabled={busy}/>本人确认取消上述允许撤销的完整订单</label><button disabled={busy||!consent||!rows.some(allowed)} onClick={()=>void cancel()}>确认取消允许撤销的订单</button></>}
    <button className="secondary" disabled={busy} onClick={onClose}>关闭取消核对</button>
  </section></div>;
}
