import {useEffect,useState} from 'react';
import type {Account} from '../../services/bank';
import * as bills from '../../services/bills';
import { accountLabel } from '../../utils/customerLabels';
export const labels:Record<string,string>={dining:'餐饮',shopping:'购物',transport:'交通',entertainment:'娱乐',subscription:'订阅',education:'学习',other:'其他'};
export const money=(fen:number)=>(fen/100).toLocaleString('zh-CN',{style:'currency',currency:'CNY'});
export const errorText=(error:unknown)=>(error as {response?:{data?:{detail?:{message?:string}}}})?.response?.data?.detail?.message||'操作失败，请刷新后重试。';
const options=Object.entries(labels).map(([id,name])=><option key={id} value={id}>{name}</option>);

export function Classification({tx,onSaved}:{tx:bills.BillTransaction;onSaved:()=>void}) {
 const [base,setBase]=useState(tx);
 const [open,setOpen]=useState(false),[category,setCategory]=useState(tx.category),[apply,setApply]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 if(tx.type!=='purchase')return null;
 const act=async(operation:()=>Promise<unknown>)=>{setBusy(true);setError('');try{await operation();setOpen(false);onSaved();}catch(e){setError(errorText(e));}finally{setBusy(false);}};
 return <div className="bill-classification"><small>分类：{labels[tx.category]||tx.category}{tx.category_source!=='original'&&' · 本人修正'}（原始：{labels[tx.raw_category||'']||tx.raw_category}）</small>
 {tx.suggested_category&&<p className="hint">建议{labels[tx.suggested_category]}：{tx.suggestion_reason}<button type="button" className="text-button" disabled={busy} onClick={()=>void act(()=>bills.correctCategory(tx,tx.suggested_category!,false))}>采纳建议</button></p>}
 {!open?<button type="button" className="text-button" onClick={()=>{setBase(tx);setCategory(tx.category);setApply(false);setOpen(true);}}>修正分类</button>:<form onSubmit={e=>{e.preventDefault();void act(()=>bills.correctCategory(base,category||null,apply));}}>
 <label>消费分类<select aria-label="当前流水修正分类" value={category} onChange={e=>{setCategory(e.target.value);if(!e.target.value)setApply(false);}}><option value="">撤销单笔修正</option>{options}</select></label>
 <label><input type="checkbox" checked={apply} disabled={!category} onChange={e=>setApply(e.target.checked)}/>同商户其他及未来消费沿用</label>
 <button disabled={busy}>保存分类</button> <button type="button" className="secondary" onClick={()=>setOpen(false)}>返回</button>
 {(tx.category_rule_version||0)>0&&<button type="button" className="text-button" disabled={busy} onClick={()=>void act(()=>bills.removeCategoryRule(base.merchant_id!,base.category_rule_version!))}>撤销商户分类规则</button>}
 </form>}{error&&<p role="alert" className="error">{error}</p>}</div>;
}

export function BudgetPanel({month,accounts,revision,onChanged,onSelect}:{month:string;accounts:Account[];revision:number;onChanged:()=>void;onSelect:(item:bills.BudgetItem|null)=>void}) {
 const [editing,setEditing]=useState(false);
 const [data,setData]=useState<{items:bills.BudgetItem[];notices:string[]}|null>(null),[account,setAccount]=useState(''),[category,setCategory]=useState(''),[amount,setAmount]=useState(''),[threshold,setThreshold]=useState(80),[active,setActive]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[version,setVersion]=useState(0),[retry,setRetry]=useState(0);
 useEffect(()=>{let cancelled=false;setError('');bills.budgets(month).then(d=>{if(!cancelled)setData(d);}).catch(e=>{if(!cancelled)setError(errorText(e));});return()=>{cancelled=true;};},[month,revision,retry]);
 useEffect(()=>{setVersion(0);setAmount('');setEditing(false);},[month]);
 const match=data?.items.find(i=>(i.account_id||'')===account&&(i.category||'')===category);
 const edit=(item:bills.BudgetItem)=>{setEditing(true);onSelect(item);setAccount(item.account_id||'');setCategory(item.category||'');setAmount(String(item.amount_fen/100));setThreshold(item.warning_percent);setActive(item.active);setVersion(item.version);};
 const submit=async()=>{setBusy(true);setError('');try{await bills.saveBudget({month,account_id:account||null,category:category||null,amount_fen:Math.round(Number(amount)*100),warning_percent:threshold,active,expected_version:version});setVersion(0);setAmount('');setEditing(false);onSelect(null);onChanged();}catch(e){setError(errorText(e));}finally{setBusy(false);}};
 return <section className="bill-enhancement"><h3>{month} 预算与预警</h3><p className="hint">按净消费核对；当前账单的标签筛选不改变预算范围。</p>
 {error&&<p role="alert" className="error">{error} <button className="secondary" onClick={()=>setRetry(n=>n+1)}>重新加载</button></p>}
 <div className="bill-budget-list">{data?.items.map(item=><article key={item.budget_id} className={`budget-${item.status}`}><strong>{item.account_id?accountLabel(item.account_id,accounts):'全部账户'} · {item.category?labels[item.category]:'总预算'}</strong><p>预算 {money(item.amount_fen)} · 已用 {money(item.spent_fen)} · 剩余 {money(item.remaining_fen)}</p><progress max={100} value={Math.min(100,item.usage_percent)}/><p>{item.active?({exceeded:'已超支',warning:'接近预算上限',projected_exceeded:'预计月底超支',ok:'当前未触发预警'}[item.status]||item.status):'已停用'} · 使用 {item.usage_percent}% · 月底估计 {money(item.projected_spending_fen)}{!item.projection_has_variable_baseline&&'（仅已知部分，消费样本不足）'}</p><button type="button" className="secondary" onClick={()=>edit(item)}>编辑此预算</button></article>)}</div>
 {!data?.items.length&&<p className="hint">尚无预算，可先设置月度总预算。</p>}
 <button type="button" className="secondary" aria-expanded={editing} aria-controls="bill-budget-editor" onClick={()=>{setVersion(0);setAmount('');setEditing(true);onSelect(null);}}>新建预算</button>
 <form id="bill-budget-editor" hidden={!editing} onSubmit={e=>{e.preventDefault();void submit();}} className="bill-plan-form"><label>预算账户<select value={account} onChange={e=>{setAccount(e.target.value);setVersion(0);setAmount('');}}><option value="">全部账户</option>{accounts.map(a=><option key={a.account_id} value={a.account_id}>{a.card_number}</option>)}</select></label><label>预算分类<select value={category} onChange={e=>{setCategory(e.target.value);setVersion(0);setAmount('');}}><option value="">总预算</option>{options}</select></label><label>预算金额（元）<input required type="number" min="0.01" step="0.01" value={amount} onChange={e=>setAmount(e.target.value)}/></label><label>预警比例（%）<input required type="number" min={1} max={100} value={threshold} onChange={e=>setThreshold(Number(e.target.value))}/></label><label><input type="checkbox" checked={active} onChange={e=>setActive(e.target.checked)}/>启用预算</label><button disabled={busy||(!!match&&!version)}>保存预算</button><button type="button" className="secondary" onClick={()=>setEditing(false)}>收起编辑</button>{match&&!version&&<p className="hint">此范围已有预算，请先点击“编辑此预算”。</p>}</form>
 {data?.notices.map(n=><p className="hint" key={n}>{n}</p>)}</section>;
}

export function AnomalyPanel({result,onChanged,onDrill,onSelect}:{result:bills.BillAnalysis;onChanged:()=>void;onDrill:(item:bills.Anomaly)=>void;onSelect:(item:bills.Anomaly)=>void}) {
 const [showAll,setShowAll]=useState(false);
 const pending=result.anomalies.filter(a=>a.review_status==='pending');
 return <section className="bill-alerts"><h3>待核对提示 · {pending.length}</h3><label><input type="checkbox" checked={showAll} onChange={e=>setShowAll(e.target.checked)}/>显示已核对记录</label>{!(showAll?result.anomalies:pending).length&&<p className="hint">没有尚待核对的提示；这不代表所有交易均无异常。</p>}
 {(showAll?result.anomalies:pending).map(item=><AnomalyCard key={item.anomaly_id} item={item} result={result} onChanged={onChanged} onDrill={()=>onDrill(item)} onSelect={()=>onSelect(item)}/>)}</section>;
}
function AnomalyCard({item,result,onChanged,onDrill,onSelect}:{item:bills.Anomaly;result:bills.BillAnalysis;onChanged:()=>void;onDrill:()=>void;onSelect:()=>void}) {
 const [base,setBase]=useState({item,result});
 const [status,setStatus]=useState<bills.Anomaly['review_status']>(item.review_status),[note,setNote]=useState(item.review_note),[busy,setBusy]=useState(false),[error,setError]=useState('');
 return <article className="bill-review" onFocusCapture={onSelect}><div><strong>{item.title}</strong><p>{item.reason}</p>{item.evidence_changed&&<p>依据发生变化，需要重新核对。</p>}<button type="button" className="secondary" onClick={onDrill}>查看依据</button>
 <p hidden={item.review_version===base.item.review_version&&item.fingerprint===base.item.fingerprint} className="hint">核对记录已更新，保存前请重新读取。<button type="button" className="text-button" onClick={()=>{setBase({item,result});setStatus(item.review_status);setNote(item.review_note);}}>重新读取核对记录</button></p>
 <form onSubmit={e=>{e.preventDefault();setBusy(true);setError('');bills.reviewAnomaly(base.item,base.result,status,note).then(response=>{setBase(current=>({...current,item:{...current.item,review_version:response.data.version,review_status:status,review_note:note}}));onChanged();}).catch(e=>setError(errorText(e))).finally(()=>setBusy(false));}}><label>核对状态<select value={status} onChange={e=>setStatus(e.target.value as bills.Anomaly['review_status'])}><option value="pending">待处理</option><option value="normal">已核实正常</option><option value="resolved">已解决</option></select></label><label>核对备注<input maxLength={256} value={note} onChange={e=>setNote(e.target.value)} placeholder="例如：本人分两次购买"/></label><button disabled={busy}>保存核对结果</button></form>{error&&<p role="alert" className="error">{error}</p>}</div></article>;
}

export function ForecastHistory({scenario,dirty,mode='history',revision=0,onSaved}:{scenario?:bills.CashflowScenario;dirty?:boolean;mode?:'save'|'history';revision?:number;onSaved?:()=>void}) {
 const [data,setData]=useState<{items:bills.ForecastComparison[];notices:string[]}|null>(null),[retry,setRetry]=useState(0),[busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>{if(mode==='save')return;let live=true;setError('');bills.forecasts().then(d=>{if(live)setData(d);}).catch(e=>{if(live)setError(errorText(e));});return()=>{live=false;};},[retry,revision,mode]);
 const hypothetical=!scenario||dirty||scenario.reductions.some(r=>r.percent>0)||scenario.excluded_subscription_ids.length>0||!!scenario.one_off_events?.length;
 return <section className="bill-forecast-history" aria-label={mode==='save'?'保存基准预测':'预测复盘记录'}>
 {mode==='save'?<><h3>保存基准预测</h3><p className="hint">保存当前基准，预测结束后可在“预测复盘”核对实际收支。</p><button type="button" disabled={busy||hypothetical} onClick={()=>{if(!scenario)return;setBusy(true);setError('');bills.saveForecast(scenario).then(()=>{setRetry(n=>n+1);onSaved?.();}).catch(e=>setError(errorText(e))).finally(()=>setBusy(false));}}>保存当前基准预测</button>{hypothetical&&<p className="hint">请先恢复并计算基准，再保存。</p>}{retry>0&&<p role="status">基准已保存，可展开预测复盘查看。</p>}</>:<><h3>预测复盘</h3><p className="hint">按保存时的账户和预测区间比较；至少3个独立预测期后可校准消费估计。</p>{!data?.items.length&&<p className="bill-empty">暂无保存的基准预测。</p>}
 {data?.items.map(item=><article className="bill-record" key={item.snapshot_id}>{item.scope_changed?<p>预测账户范围已变化，无法直接比较。</p>:<><strong>{new Date(item.start_at!).toLocaleDateString('zh-CN')} — {new Date(item.end_at!).toLocaleDateString('zh-CN')} · {item.complete?'已结束':'观察中'}</strong><p>预计净变化 {money(item.predicted_net_flow_fen!)} · 已发生净变化 {money(item.actual_net_flow_fen!)}{item.error_fen!==null&&item.error_fen!==undefined&&` · 偏差 ${money(item.error_fen)}`}</p></>}</article>)}{data?.notices.map(n=><p className="hint" key={n}>{n}</p>)}</>}
 {error&&<p className="error" role="alert">{error} <button onClick={()=>setRetry(n=>n+1)}>重新加载</button></p>}
 </section>;
}
