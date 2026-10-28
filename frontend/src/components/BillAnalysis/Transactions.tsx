import {useEffect,useState} from 'react';
import type {Account} from '../../services/bank';
import * as bills from '../../services/bills';
import {accountLabel,counterpartyLabel} from '../../utils/customerLabels';
import {TransactionAnnotations} from './Foundations';
import {labels,money,errorText} from './Decisions';

const types:Record<string,string>={salary:'工资',purchase:'消费',subscription:'订阅扣费',refund:'退款',transfer_in:'转入',transfer_out:'转出'};
const time=(s:string)=>new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
export type DetailFilter={category?:string;tx_ids?:string;label:string};

export default function Transactions({result,accounts,filter,page,onPage,selected,onSelect,onReset,revision,onSaved}:{
  result:bills.BillAnalysis;accounts:Account[];filter:DetailFilter;page:number;onPage:(page:number)=>void;
  selected:string|null;onSelect:(id:string)=>void;onReset:()=>void;revision:number;onSaved:()=>void;
}) {
  const [rows,setRows]=useState<bills.BillPage|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[retry,setRetry]=useState(0),[detailOpen,setDetailOpen]=useState(false);
  useEffect(()=>{let live=true;setBusy(true);setError('');bills.details({...result.query,category:filter.category||result.query.category},page,result.as_of,filter.tx_ids)
    .then(data=>{if(live)setRows(data);}).catch(e=>{if(live)setError(errorText(e));}).finally(()=>{if(live)setBusy(false);});return()=>{live=false;};},[result,filter,page,retry]);
  useEffect(()=>{setDetailOpen(!!selected);},[selected]);
  return <section id="bill-transactions" aria-label="交易明细工作区">
    <div className="bill-heading"><div><h3>{filter.label}</h3><p className="hint">打开详情后可整理分类、标签及关联交易，选中流水会同步给 NOVA。</p></div><button type="button" className="secondary" onClick={onReset}>重置明细筛选</button></div>
    {busy&&<p role="status">正在加载流水…</p>}{error&&<p role="alert" className="error">{error} <button onClick={()=>setRetry(n=>n+1)}>重试</button></p>}
    {rows&&<><div className="bill-table" aria-busy={busy}><table><thead><tr><th>时间 / 对手方</th><th>类型 / 分类</th><th>账户</th><th>金额</th><th>操作</th></tr></thead><tbody>{rows.items.map(tx=><tr key={tx.tx_id} className={selected===tx.tx_id?'is-selected':undefined}>
      <td>{counterpartyLabel(tx.counterparty)}<small>{time(tx.timestamp)}</small></td><td>{types[tx.type]||'交易'}<small>{labels[tx.category]||'其他'}{tx.tags?.length?` · ${tx.tags.map(t=>t.name).join('、')}`:''}</small></td><td>{accountLabel(tx.account_id,accounts)}</td>
      <td>{['purchase','subscription','transfer_out'].includes(tx.type)?'−':'+'}{money(tx.amount_fen)}</td><td><button type="button" className="secondary" aria-pressed={selected===tx.tx_id} disabled={busy} onClick={()=>{onSelect(tx.tx_id);setDetailOpen(true);}}>查看详情</button></td>
    </tr>)}</tbody></table></div>{!rows.total&&<p className="bill-empty">暂无符合筛选条件的流水。</p>}
    <div className="bill-pagination"><span>共 {rows.total} 笔 · 第 {rows.page} / {Math.max(1,Math.ceil(rows.total/rows.page_size))} 页</span><button type="button" className="secondary" disabled={page<=1||busy} onClick={()=>onPage(page-1)}>上一页</button><button type="button" className="secondary" disabled={page*rows.page_size>=rows.total||busy} onClick={()=>onPage(page+1)}>下一页</button></div></>}
    {selected&&<TransactionDetail key={selected} id={selected} open={detailOpen} onClose={()=>setDetailOpen(false)} onOpen={()=>setDetailOpen(true)} revision={revision} onSaved={onSaved}/>}
  </section>;
}

function TransactionDetail({id,open,onClose,onOpen,revision,onSaved}:{id:string;open:boolean;onClose:()=>void;onOpen:()=>void;revision:number;onSaved:()=>void}) {
  const [tx,setTx]=useState<bills.BillTransaction|null>(null),[error,setError]=useState(''),[retry,setRetry]=useState(0);
  useEffect(()=>{let live=true;setError('');bills.transaction(id).then(data=>{if(live)setTx(data);}).catch(e=>{if(live)setError(errorText(e));});return()=>{live=false;};},[id,revision,retry]);
  return <><button type="button" className="text-button" hidden={open} onClick={onOpen}>重新打开所选交易详情</button><aside className="bill-detail-panel" aria-label="交易详情" hidden={!open}>
    <div className="bill-heading"><h3>交易详情</h3><button type="button" className="secondary" onClick={onClose}>收起详情</button></div>
    {error&&<p role="alert" className="error">{error} <button onClick={()=>setRetry(n=>n+1)}>重新加载</button></p>}
    {tx?<><dl className="bill-facts"><div><dt>交易对手</dt><dd>{counterpartyLabel(tx.counterparty)}</dd></div><div><dt>金额</dt><dd>{money(tx.amount_fen)}</dd></div><div><dt>时间</dt><dd>{time(tx.timestamp)}</dd></div><div><dt>备注</dt><dd>{tx.note||'无备注'}</dd></div></dl><TransactionAnnotations tx={tx} onSaved={onSaved}/></>:!error&&<p role="status">正在读取详情…</p>}
  </aside></>;
}
