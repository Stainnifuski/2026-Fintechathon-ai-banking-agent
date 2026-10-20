import {useEffect,useState} from 'react';
import type {Account} from '../../services/bank';
import * as bills from '../../services/bills';
import {beijingInput,fromBeijingInput} from '../../utils/time';
import {labels,errorText} from './Decisions';
export const frequencies:Record<string,string>={WEEKLY:'每周',MONTHLY:'每月',QUARTERLY:'每季度',YEARLY:'每年'};

export function RecurringEditor({accounts,items,onChanged}:{accounts:Account[];items:bills.RecurringItem[];onChanged:()=>void}) {
 const initial=()=>({name:'',account_id:accounts.find(a=>a.account_type==='savings')?.account_id||'',kind:'expense' as 'income'|'expense',frequency:'MONTHLY',amount:'',anchor:'',category:'other',month_end:false,enabled:true,active:true,version:0});
 const [draft,setDraft]=useState(initial),[id,setId]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const edit=(streamId:string)=>{setId(streamId);const item=items.find(i=>i.stream_id===streamId);if(!item){setDraft(initial());return;}setDraft({name:item.merchant,account_id:item.account_id,kind:item.kind,frequency:item.frequency,amount:String(item.amount_fen/100),anchor:item.anchor_at?beijingInput(new Date(item.anchor_at)):'',category:item.category||'other',month_end:item.month_end,enabled:item.enabled,active:item.status==='ACTIVE',version:item.version});};
 return <details className="bill-notices"><summary>手动建立或编辑周期计划</summary><p>适合房租、保险和固定收入。保存后用于预测，不会执行转账或扣款。</p><label>编辑已有计划<select value={id} onChange={e=>edit(e.target.value)}><option value="">建立新计划</option>{items.filter(i=>i.source==='manual').map(i=><option key={i.stream_id} value={i.stream_id}>{i.merchant} · {frequencies[i.frequency]}</option>)}</select></label>
 <form className="bill-plan-form" onSubmit={e=>{e.preventDefault();setBusy(true);setError('');bills.saveRecurringPlan({name:draft.name,account_id:draft.account_id,kind:draft.kind,frequency:draft.frequency,amount_fen:Math.round(Number(draft.amount)*100),anchor_at:fromBeijingInput(draft.anchor),month_end:draft.month_end,category:draft.category,enabled:draft.enabled,active:draft.active,expected_version:draft.version},id||undefined).then(()=>{setId('');setDraft(initial());onChanged();}).catch(e=>setError(errorText(e))).finally(()=>setBusy(false));}}>
 <label>计划名称<input aria-label="周期计划名称" required maxLength={64} value={draft.name} onChange={e=>setDraft({...draft,name:e.target.value})}/></label>
 <label>扣款／收款账户<select required value={draft.account_id} onChange={e=>setDraft({...draft,account_id:e.target.value})}><option value="">选择账户</option>{accounts.filter(a=>a.account_type==='savings').map(a=><option key={a.account_id} value={a.account_id}>{a.card_number}</option>)}</select></label>
 <label>方向<select value={draft.kind} onChange={e=>setDraft({...draft,kind:e.target.value as 'income'|'expense'})}><option value="expense">支出</option><option value="income">收入</option></select></label>
 <label>周期频率<select value={draft.frequency} onChange={e=>setDraft({...draft,frequency:e.target.value,month_end:e.target.value==='WEEKLY'?false:draft.month_end})}>{Object.entries(frequencies).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
 <label>每次金额（元）<input required type="number" min="0.01" step="0.01" value={draft.amount} onChange={e=>setDraft({...draft,amount:e.target.value})}/></label>
 <label>首次日期（北京时间）<input required type="datetime-local" value={draft.anchor} onChange={e=>setDraft({...draft,anchor:e.target.value})}/></label>
 <label>计划分类<select value={draft.category} onChange={e=>setDraft({...draft,category:e.target.value})}>{Object.entries(labels).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
 <label><input type="checkbox" checked={draft.month_end} disabled={draft.frequency==='WEEKLY'} onChange={e=>setDraft({...draft,month_end:e.target.checked})}/>固定月末</label>
 <label><input type="checkbox" checked={draft.enabled} onChange={e=>setDraft({...draft,enabled:e.target.checked})}/>纳入预测</label>
 <label><input type="checkbox" checked={draft.active} onChange={e=>setDraft({...draft,active:e.target.checked})}/>计划有效</label><button disabled={busy}>保存周期计划</button>
 </form>{error&&<p role="alert" className="error">{error}</p>}</details>;
}

export function MerchantHistory({revision,onChanged}:{revision:number;onChanged:()=>void}) {
 const [rows,setRows]=useState<bills.MerchantHistory[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[pending,setPending]=useState<string|null>(null);
 useEffect(()=>{let cancelled=false;bills.merchantHistory().then(d=>{if(!cancelled)setRows(d);}).catch(e=>{if(!cancelled)setError(errorText(e));});return()=>{cancelled=true;};},[revision]);
 return <details className="bill-notices"><summary>商户变更记录与撤销</summary>{!rows.length&&<p>尚无归并或拆分记录。</p>}{rows.map(r=><article key={r.event_id}><p>{new Date(r.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})} · {r.action==='merchant_merge'?`${r.source_name||'商户'} → ${r.target_name||'商户'}`:r.action==='merchant_unmerge'?'已撤销归并':'已拆分商户'}</p>{r.can_undo&&<button type="button" className="secondary" disabled={busy} onClick={()=>setPending(r.event_id)}>撤销这次归并…</button>}</article>)}
 {pending&&<div role="group" aria-label="确认撤销归并"><p>恢复这次归并前的商户、别名和流水归属。若归并范围已变化，系统会要求使用拆分。</p><button disabled={busy} onClick={()=>{setBusy(true);setError('');bills.undoMerchantMerge(pending).then(()=>{setPending(null);onChanged();}).catch(e=>setError(errorText(e))).finally(()=>setBusy(false));}}>确认撤销归并</button><button className="secondary" disabled={busy} onClick={()=>setPending(null)}>返回</button></div>}{error&&<p className="error" role="alert">{error}</p>}</details>;
}

export function MerchantSplit({merchantId,onChanged}:{merchantId:string;onChanged:()=>void}) {
 const [name,setName]=useState(''),[ids,setIds]=useState(''),[aliases,setAliases]=useState(''),[preview,setPreview]=useState<Awaited<ReturnType<typeof bills.previewSplit>>|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>{setPreview(null);},[merchantId,name,ids,aliases]);
 const parsedIds=ids.split(/[,，\s]+/).filter(Boolean);
 return <details className="bill-notices"><summary>按明确流水拆分商户</summary><p>先选择上方“保留商户”，再填写属于新商户的流水号。原单及关联退款会一起移动；有银行付款身份依据的流水不能拆成不同商户。</p>
 <label>新商户名称<input value={name} maxLength={64} onChange={e=>setName(e.target.value)}/></label><label>待拆分流水号（逗号或空格分隔）<textarea value={ids} maxLength={6800} onChange={e=>setIds(e.target.value)}/></label><label>一并移动的现有别名（可选，逗号分隔）<input value={aliases} onChange={e=>setAliases(e.target.value)}/></label>
 <button type="button" className="secondary" disabled={busy||!merchantId||!name.trim()||!parsedIds.length} onClick={()=>{setBusy(true);setError('');bills.previewSplit(merchantId,name,parsedIds,aliases.split(/[,，]/).map(s=>s.trim()).filter(Boolean)).then(setPreview).catch(e=>setError(errorText(e))).finally(()=>setBusy(false));}}>预览拆分</button>
 {preview&&<div role="group" aria-label="确认拆分商户"><p>将共 {preview.moving_count} 笔（含关联退款），从当前商户拆入“{name}”。当前商户共有 {preview.transaction_count} 笔。</p><p className="hint">本次涉及 {preview.moving_tx_ids.length} 笔流水。</p>{preview.notices.map(n=><p key={n}>{n}</p>)}<button disabled={busy||!preview.allowed} onClick={()=>{setBusy(true);setError('');bills.splitMerchant(merchantId,name,parsedIds,aliases.split(/[,，]/).map(s=>s.trim()).filter(Boolean),preview.preview_digest).then(()=>{setPreview(null);setIds('');setName('');onChanged();}).catch(e=>setError(errorText(e))).finally(()=>setBusy(false));}}>确认拆分</button><button className="secondary" onClick={()=>setPreview(null)}>返回</button>{!preview.allowed&&<p>所选流水有银行付款身份依据，请核对归并撤销记录。</p>}</div>}{error&&<p role="alert" className="error">{error}</p>}</details>;
}
