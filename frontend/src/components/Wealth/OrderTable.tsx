import {useEffect,useState} from 'react';
import type {CancellationEligibility,OrderRow,OrderTableData} from '../../services/wealth';
import {get,money} from '../../services/wealth';
import {beijingTime} from '../../utils/time';
import {metricText} from './tableData';
import {useTableState} from './useTableState';
import TablePager from './TablePager';
import ProductDetail from './ProductDetail';
import type {ProductDetailLoader} from './ProductDetail';
import './wealthTables.css';

export type EligibilityLoader=(id:string)=>Promise<CancellationEligibility>;
export const bankEligibility:EligibilityLoader=id=>get(`/orders/${encodeURIComponent(id)}/cancellation-eligibility`);
const statusLabels:Record<string,string>={ACCEPTED:'已受理，待结算',PENDING_PRICING:'等待计价',PENDING_CASH:'等待赎回到账',SETTLED:'已结算',CANCELLED:'已撤单',REJECTED:'已拒绝',UNKNOWN:'结果未知，请按原号核对'};
interface Props {data:OrderTableData;loadEligibility?:EligibilityLoader;loadDetail?:ProductDetailLoader;onCancel:(rows:OrderRow[])=>void;blockedAccounts?:string[];refreshKey?:number}
export default function OrderTable({data,loadEligibility=bankEligibility,loadDetail,onCancel,blockedAccounts=[],refreshKey=0}:Props){
  const table=useTableState(data.rows,data.scope_key,row=>row.operation_id);
  const [detail,setDetail]=useState<{scope:string;row:OrderRow}|null>(null);
  const [eligibility,setEligibility]=useState<Record<string,CancellationEligibility>>({});
  const pageKey=JSON.stringify(table.rows.map(row=>[row.operation_id,row.status,row.digest]));
  useEffect(()=>{let active=true;setEligibility({});
    void Promise.all(table.rows.map(async row=>{
      try{const e=await loadEligibility(row.operation_id);if(active&&e.operation_id===row.operation_id)setEligibility(s=>({...s,[row.operation_id]:e}));}
      catch{if(active)setEligibility(s=>({...s,[row.operation_id]:{operation_id:row.operation_id,eligibility:'UNKNOWN',reason:'撤单资格查询失败，请刷新后核对',order_digest:'',version_id:row.version_id,deadline:null,consequence:''}}));}
    }));return()=>{active=false;};
  },[data.scope_key,pageKey,loadEligibility,refreshKey]);
  const allowed=(row:OrderRow)=>row.disclosure_version!=null&&eligibility[row.operation_id]?.eligibility==='ALLOWED'&&eligibility[row.operation_id].order_digest===row.digest&&!blockedAccounts.includes(row.account_id);
  const selectedRows=data.rows.filter(row=>table.selected.includes(row.operation_id));
  return <section className="wealth-data-table" aria-label="独立订单表"><h2>我的订单</h2><p>申购和赎回申请都在这里。已受理不等于已结算；取消资格按银行合同和当前时点核对。</p>
    <button disabled={!selectedRows.length} onClick={()=>onCancel(selectedRows)}>核对已选订单的取消资格</button>
    <p className="table-scroll-hint">表格可左右滑动，查看完整资料、详情和取消入口。</p>
    <div className="wealth-table-scroll"><table><thead><tr><th><input aria-label="勾选本页订单" type="checkbox" checked={table.allChecked} onChange={e=>table.toggleVisible(e.target.checked)}/></th><th>产品／份额</th><th>操作／申请</th><th>申请时间／状态</th><th>最新行情</th><th>详情／取消</th></tr></thead><tbody>{table.rows.map(row=><tr key={row.operation_id}>
      <td><input aria-label={`勾选订单 ${row.operation_id}`} type="checkbox" checked={table.selected.includes(row.operation_id)} onChange={e=>table.toggle(row.operation_id,e.target.checked)}/></td>
      <td>{row.product_name}{row.share_class&&` · ${row.share_class}类`}<small>{row.product_id} v{row.product_version} · {row.account_id}</small><small>原号 {row.operation_id}</small></td>
      <td>{row.direction==='purchase'?'申购':'赎回'}<small>{row.direction==='purchase'?money(row.amount_fen):`${row.units}份`}</small></td>
      <td>{row.accepted_at?beijingTime(row.accepted_at):'时间资料缺失'}<small>{statusLabels[row.status]??row.status}</small></td>
      <td>{metricText(row.daily_metric)}<small>{row.daily_metric?.date??'日期未提供'}{row.daily_metric?.previous_date&&` / 前次 ${row.daily_metric.previous_date}`}</small></td>
      <td><button className="secondary" onClick={()=>setDetail({scope:data.scope_key,row})}>查看订单详情</button><button disabled={!allowed(row)} onClick={()=>onCancel([row])}>取消订单</button><small>{blockedAccounts.includes(row.account_id)?'账户存在人工接管／暂停任务':eligibility[row.operation_id]?.reason??'正在核对撤单资格…'}</small></td>
    </tr>)}</tbody></table></div>{!data.rows.length&&<p className="consumer-empty">当前资产范围没有理财订单；选择产品并完成本人确认后，申请会出现在这里。</p>}
    <TablePager {...table} label="订单" total={data.rows.length} selected={table.selected.length} onPage={table.changePage} onSize={table.changeSize} onClear={table.clear}/>
    {detail?.scope===data.scope_key&&<OrderDetail row={data.rows.find(row=>row.operation_id===detail.row.operation_id)??detail.row} loadDetail={loadDetail} onClose={()=>setDetail(null)}/>}
  </section>;
}
function OrderDetail({row,loadDetail,onClose}:{row:OrderRow;loadDetail?:ProductDetailLoader;onClose:()=>void}){
  const [product,setProduct]=useState(false);
  return <><div className="wealth-modal"><section className="card" role="dialog" aria-modal="true" aria-label="订单事实详情"><button className="secondary" onClick={onClose}>关闭订单详情</button><h3>{row.product_name} · {row.operation_id}</h3>
    <p>账户 {row.account_id} · 产品 {row.product_id} v{row.product_version} · {statusLabels[row.status]??row.status}</p><p>{row.direction==='purchase'?`申请申购 ${money(row.amount_fen)}`:`申请赎回 ${row.units}份`}</p>
    <p>计价日 {row.pricing_date} · 确认日 {row.confirm_date} · 到账日 {row.cash_date}</p><p>实际费用 {money(row.details.actual?.fee_fen)} · 实际份额 {row.details.actual?.units??'尚未确认'} · 实际赎回到账 {money(row.details.actual?.cash_fen)}</p>
    <p>等待原因 {row.details.waiting_reason??'无已记录原因'}。未结算值不代表0；实际结果以银行回读为准。</p><button onClick={()=>setProduct(true)}>查看该版本产品资料</button></section></div>
    {product&&<ProductDetail productId={row.product_id} productVersion={row.product_version} loadDetail={loadDetail} onClose={()=>setProduct(false)}/>}</>;
}
