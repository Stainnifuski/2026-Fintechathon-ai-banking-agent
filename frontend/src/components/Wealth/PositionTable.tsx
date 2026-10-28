import Modal from '../Modal';
import {useEffect,useState} from 'react';
import {get} from '../../services/wealth';
import type {WealthReturns} from '../../services/wealthReturns';
import type {PositionRow,PositionTableData} from '../../services/wealth';
import {money} from '../../services/wealth';
import {scaledUnits} from './tableData';
import {useTableState} from './useTableState';
import TablePager from './TablePager';
import type {TableControl} from './tableState';
import ProductDetail from './ProductDetail';
import type {ProductDetailLoader} from './ProductDetail';
import './wealthTables.css';
interface Props {data:PositionTableData;loadDetail?:ProductDetailLoader;onRedeem:(rows:PositionRow[])=>void;onReturns:(productId?:string)=>void;blockedAccounts?:string[];control?:TableControl}
export default function PositionTable({data,loadDetail,onRedeem,onReturns,blockedAccounts=[],control}:Props){
  const [daily,setDaily]=useState<Record<string,number|null>>({});
  const snapshot=JSON.stringify(data.rows.map(r=>[r.position_key,r.units,r.market_value_fen,r.nav_date]));
  useEffect(()=>{let live=true;setDaily({});if(!data.rows.length||data.scope_key.includes('fixture'))return;
    void get<WealthReturns>('/returns?scope=all&period=day&overview=true').then(result=>{
      let scopes:unknown[]=[];try{const parsed=JSON.parse(data.scope_key);if(Array.isArray(parsed))scopes=parsed;}catch{}
      if(result.asset_scope_id!==data.scope_key&&!scopes.includes(result.asset_scope_id))return;
      if(live)setDaily(Object.fromEntries((result.performance?.positions??[]).map(p=>[p.position_key,p.yesterday_pnl_fen])));
    }).catch(()=>{});return()=>{live=false;};
  },[data.scope_key,snapshot,data.business_date]);
  const table=useTableState(data.rows,data.scope_key,row=>row.position_key,control);
  const [detail,setDetail]=useState<{scope:string;key:string}|null>(null);
  const detailRow=detail?.scope===data.scope_key?data.rows.find(row=>row.position_key===detail.key):undefined;
  const canRedeem=(row:PositionRow)=>scaledUnits(row.redeemable_units)>0n&&row.disclosure_version!=null&&!blockedAccounts.includes(row.account_id);
  return <section className="wealth-data-table" aria-label="独立持仓表"><h2>我的持仓</h2>
    <button className="secondary" onClick={()=>onReturns()}>查看整体收益</button><button disabled={!table.selected.length} onClick={()=>onRedeem(data.rows.filter(row=>table.selected.includes(row.position_key)))}>赎回已选持仓</button>
    <p className="table-scroll-hint">表格可左右滑动，查看完整资料、详情和赎回入口。</p>
    <div className="wealth-table-scroll"><table><thead><tr><th><input aria-label="勾选本页持仓" type="checkbox" checked={table.allChecked} onChange={e=>table.toggleVisible(e.target.checked)}/></th><th>产品名称</th><th>金额</th><th>昨日收益</th><th>持仓收益／率</th><th>详情／赎回</th></tr></thead><tbody>{table.rows.map(row=><tr key={row.position_key}>
      <td><input aria-label={`勾选持仓 ${row.product_name}`} type="checkbox" checked={table.selected.includes(row.position_key)} onChange={e=>table.toggle(row.position_key,e.target.checked)}/></td>
      <td>{row.product_name}</td>
      <td>{money(row.market_value_fen)}</td>
      <td>{daily[row.position_key]==null?'待更新':money(daily[row.position_key])}</td>
      <td>{money(row.unrealized_pnl_fen)}<small>{row.unrealized_pnl_fen==null||row.cost_fen<=0?'—':`${row.unrealized_pnl_fen>0?'+':''}${(row.unrealized_pnl_fen/row.cost_fen*100).toFixed(2)}%`}</small></td>
      <td><button className="secondary" onClick={()=>setDetail({scope:data.scope_key,key:row.position_key})}>查看持仓详情</button><button disabled={!canRedeem(row)} onClick={()=>onRedeem([row])}>赎回</button>{!canRedeem(row)&&<small>{blockedAccounts.includes(row.account_id)?'人工接管／暂停任务限制':row.disclosure_version==null?'产品版本资料缺失':'当前没有可赎份额'}</small>}</td>
    </tr>)}</tbody></table></div>{!data.rows.length&&<p className="consumer-empty">当前没有已确认持仓。申购即时成交成功后会刷新此表。</p>}
    <TablePager {...table} label="持仓" total={data.rows.length} selected={table.selected.length} onPage={table.changePage} onSize={table.changeSize} onClear={table.clear}/>
    {detailRow&&<PositionDetail row={detailRow} loadDetail={loadDetail} onClose={()=>setDetail(null)}/>}
  </section>;
}
export function PositionDetail({row,loadDetail,onClose}:{row:PositionRow;loadDetail?:ProductDetailLoader;onClose:()=>void;onReturns?:()=>void}){
  const [product,setProduct]=useState(false);
  return <><Modal className="wealth-modal" scope="wealth wealth-page"><section className="card" role="dialog" aria-modal="true" aria-label="持仓事实详情"><button className="secondary" onClick={onClose}>关闭持仓详情</button><h3>{row.product_name}</h3>
    <p>{row.units}份 · 市值 {money(row.market_value_fen)} · 可赎 {row.redeemable_units}份</p><p>净值 {row.nav??'未知'} · {row.nav_date??'无有效日期'} · 成本 {money(row.cost_fen)}</p>
    {row.lots.map((lot, index)=><article key={lot.lot_id}><strong>第 {index + 1} 笔买入</strong><p>持有 {lot.units} · 可赎 {lot.redeemable_units} · 锁定 {lot.locked_units} · 占用 {lot.held_units}</p><p>买入确认 {lot.acquired_date??'未知'} · 解锁 {lot.unlock_date??'未知'} · 市值 {money(lot.market_value_fen)} · 成本 {money(lot.cost_fen)}</p></article>)}
    <button onClick={()=>setProduct(true)}>查看该版本产品资料</button></section></Modal>{product&&<ProductDetail productId={row.product_id} productVersion={row.product_version} loadDetail={loadDetail} onClose={()=>setProduct(false)}/>}</>;
}
