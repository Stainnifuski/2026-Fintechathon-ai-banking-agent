import {useState} from 'react';
import type {PositionRow,PositionTableData} from '../../services/wealth';
import {money} from '../../services/wealth';
import {metricText,scaledUnits} from './tableData';
import {useTableState} from './useTableState';
import TablePager from './TablePager';
import ProductDetail from './ProductDetail';
import type {ProductDetailLoader} from './ProductDetail';
import './wealthTables.css';
interface Props {data:PositionTableData;loadDetail?:ProductDetailLoader;onRedeem:(rows:PositionRow[])=>void;onReturns:(productId?:string)=>void;blockedAccounts?:string[]}
export default function PositionTable({data,loadDetail,onRedeem,onReturns,blockedAccounts=[]}:Props){
  const table=useTableState(data.rows,data.scope_key,row=>row.position_key);
  const [detail,setDetail]=useState<{scope:string;key:string}|null>(null);
  const detailRow=detail?.scope===data.scope_key?data.rows.find(row=>row.position_key===detail.key):undefined;
  const canRedeem=(row:PositionRow)=>scaledUnits(row.redeemable_units)>0n&&row.disclosure_version!=null&&!blockedAccounts.includes(row.account_id);
  return <section className="wealth-data-table" aria-label="独立持仓表"><h2>我的持仓</h2><p>同账户、同产品版本的买入批次合为一行。尚未确认的申请仅在订单表显示；可赎份额已扣除锁定和占用。</p>
    <button className="secondary" onClick={()=>onReturns()}>查看整体收益</button><button disabled={!table.selected.length} onClick={()=>onRedeem(data.rows.filter(row=>table.selected.includes(row.position_key)))}>赎回已选持仓</button>
    <p className="table-scroll-hint">表格可左右滑动，查看完整资料、详情和赎回入口。</p>
    <div className="wealth-table-scroll"><table><thead><tr><th><input aria-label="勾选本页持仓" type="checkbox" checked={table.allChecked} onChange={e=>table.toggleVisible(e.target.checked)}/></th><th>产品／份额</th><th>持有／可赎</th><th>市值／整体占比</th><th>收益／行情</th><th>详情／赎回</th></tr></thead><tbody>{table.rows.map(row=><tr key={row.position_key}>
      <td><input aria-label={`勾选持仓 ${row.position_key}`} type="checkbox" checked={table.selected.includes(row.position_key)} onChange={e=>table.toggle(row.position_key,e.target.checked)}/></td>
      <td>{row.product_name}{row.share_class&&` · ${row.share_class}类`}<small>{row.product_id} v{row.product_version} · {row.account_id}</small><small>{row.lot_ids.length}个买入批次</small></td>
      <td>{row.units}份<small>可赎 {row.redeemable_units}份</small><small>锁定 {row.locked_units} · 占用 {row.held_units}</small></td>
      <td>{money(row.market_value_fen)}<small>{row.total_ratio==null?'整体估值不完整':`${(Number(row.total_ratio)*100).toFixed(2)}% · 占全部资产`}</small></td>
      <td><button className="table-link" onClick={()=>onReturns(row.product_id)}>当前浮动收益 {money(row.unrealized_pnl_fen)}</button><small>浮动收益不等于累计实际收益</small><small>{metricText(row.daily_metric)}</small><small>{row.daily_metric?.date??'无有效日期'}{row.daily_metric?.previous_date&&` / 前次 ${row.daily_metric.previous_date}`}</small></td>
      <td><button className="secondary" onClick={()=>setDetail({scope:data.scope_key,key:row.position_key})}>查看持仓详情</button><button disabled={!canRedeem(row)} onClick={()=>onRedeem([row])}>赎回</button>{!canRedeem(row)&&<small>{blockedAccounts.includes(row.account_id)?'人工接管／暂停任务限制':row.disclosure_version==null?'产品版本资料缺失':'当前没有可赎份额'}</small>}</td>
    </tr>)}</tbody></table></div>{!data.rows.length&&<p className="consumer-empty">当前没有已确认持仓。已受理、待计价的申购仍可在订单表查询。</p>}
    <TablePager {...table} label="持仓" total={data.rows.length} selected={table.selected.length} onPage={table.changePage} onSize={table.changeSize} onClear={table.clear}/>
    {detailRow&&<PositionDetail row={detailRow} loadDetail={loadDetail} onClose={()=>setDetail(null)}/>}
  </section>;
}
function PositionDetail({row,loadDetail,onClose}:{row:PositionRow;loadDetail?:ProductDetailLoader;onClose:()=>void}){
  const [product,setProduct]=useState(false);
  return <><div className="wealth-modal"><section className="card" role="dialog" aria-modal="true" aria-label="持仓事实详情"><button className="secondary" onClick={onClose}>关闭持仓详情</button><h3>{row.product_name} · {row.account_id}</h3>
    <p>{row.product_id} v{row.product_version} · {row.units}份 · 市值 {money(row.market_value_fen)} · 可赎 {row.redeemable_units}份</p><p>净值 {row.nav??'未知'} · {row.nav_date??'无有效日期'} · 成本 {money(row.cost_fen)}</p>
    {row.lots.map(lot=><article key={lot.lot_id}><strong>批次 {lot.lot_id}</strong><p>持有 {lot.units} · 可赎 {lot.redeemable_units} · 锁定 {lot.locked_units} · 占用 {lot.held_units}</p><p>买入确认 {lot.acquired_date??'未知'} · 解锁 {lot.unlock_date??'未知'} · 市值 {money(lot.market_value_fen)} · 成本 {money(lot.cost_fen)}</p></article>)}
    <button onClick={()=>setProduct(true)}>查看该版本产品资料</button></section></div>{product&&<ProductDetail productId={row.product_id} productVersion={row.product_version} loadDetail={loadDetail} onClose={()=>setProduct(false)}/>}</>;
}
