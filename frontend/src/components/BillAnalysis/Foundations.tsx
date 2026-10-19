import { useEffect, useState } from 'react';
import type {Account} from '../../services/bank';
import {Classification} from './Decisions';
import {RecurringEditor, MerchantHistory, MerchantSplit,frequencies} from './Management';
import * as bills from '../../services/bills';
const money = (fen: number) => (fen / 100).toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' });
const errorText = (error: unknown) => (error as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message || '操作失败，请刷新后重试。';

export default function Foundations({ accounts, revision, onChanged, onManage }: { accounts:Account[]; revision: number; onChanged: () => void; onManage?: (id: string) => void }) {
  const [data, setData] = useState<bills.FoundationCatalog | null>(null);
  const [cycles, setCycles] = useState<bills.RecurringItem[]>([]);
  const [notices, setNotices] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [target, setTarget] = useState('');
  const [source, setSource] = useState('');
  const [alias, setAlias] = useState('');
  const [confirmMerge, setConfirmMerge] = useState<bills.MergePreview|null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError('');
    Promise.all([bills.foundations(), bills.recurring()]).then(([catalog, recurring]) => {
      if (!cancelled) { setData(catalog); setCycles(recurring.items); setNotices(recurring.notices); }
    }).catch(e => { if (!cancelled) setError(errorText(e)); });
    return () => { cancelled = true; };
  }, [revision, reload]);
  useEffect(() => { setConfirmMerge(null); }, [target, source]);
  const act = async (operation: () => Promise<unknown>) => {
    setBusy(true); setError('');
    try { await operation(); setReload(n => n + 1); onChanged(); return true; }
    catch (e) { setError(errorText(e)); return false; }
    finally { setBusy(false); }
  };
  const options = data?.merchants.map(m => <option key={m.merchant_id} value={m.merchant_id}>{m.name}{m.bank_verified ? '（银行已核实）' : ''}</option>);
  return <details className="bill-foundations"><summary>商户与周期收支管理</summary>
    <p className="hint">关联和标签不改变流水金额。周期预测的启用设置不会停止真实扣费。</p>
    {error && <p className="error" role="alert">{error}</p>}
    <button type="button" className="secondary" disabled={busy} onClick={() => void act(bills.refreshFoundations)}>{busy ? '正在更新…' : '刷新历史关联与周期识别'}</button>
    {data && <><p className="hint">已建立 {data.metadata_total} / {data.transactions_total} 笔流水的辅助记录 · {data.merchants.length} 个商户 · {data.tags.length} 个标签</p>
      <form className="bill-filters" onSubmit={e => { e.preventDefault(); void act(() => bills.addMerchantAlias(target, alias)).then(ok => { if (ok) setAlias(''); }); }}>
        <label>保留商户<select aria-label="保留商户" value={target} required onChange={e => setTarget(e.target.value)}><option value="">选择商户</option>{options}</select></label>
        <label>新增名称别名<input aria-label="商户别名" value={alias} onChange={e => setAlias(e.target.value)} maxLength={64} placeholder="同一商户的新名称" /></label>
        <button disabled={busy || !target || !alias.trim()}>保存别名</button>
      </form>
      {target && <p className="hint">已保存别名：{data.merchants.find(m => m.merchant_id === target)?.aliases.join('、') || '暂无'}</p>}
      <div className="bill-filters"><label>归入上述商户<select aria-label="待归并商户" value={source} onChange={e => setSource(e.target.value)}><option value="">选择另一个商户</option>{options?.filter(o => o.key !== target)}</select></label><button type="button" className="secondary" disabled={busy || !target || !source || source === target} onClick={async () => {setBusy(true);setError('');try{setConfirmMerge(await bills.previewMerge(target,source));}catch(e){setError(errorText(e));}finally{setBusy(false);}}}>归并商户…</button></div>
      {confirmMerge && <div className="bill-merge-confirm" role="group" aria-label="确认归并商户"><p>将“{data.merchants.find(m => m.merchant_id === source)?.name}”归入“{data.merchants.find(m => m.merchant_id === target)?.name}”，将移动 {confirmMerge.transaction_count} 笔流水、{confirmMerge.alias_count} 个别名。之后可在变更记录中撤销。</p><p>{confirmMerge.notices.join(" ")}</p><button disabled={busy||!confirmMerge.allowed} onClick={() => void act(() => bills.mergeMerchants(target, source,confirmMerge.preview_digest)).then(ok => { if (ok) { setConfirmMerge(null); setSource(''); } })}>确认归并</button> <button className="secondary" onClick={() => setConfirmMerge(null)}>返回</button></div>}
      {data.notices.map(n => <p className="hint" key={n}>{n}</p>)}
    </>}
    <MerchantHistory revision={revision+reload} onChanged={()=>{setReload(n=>n+1);onChanged();}}/><MerchantSplit merchantId={target} onChanged={()=>{setReload(n=>n+1);onChanged();}}/>
    <h3>已保存的周期收支</h3>
    <RecurringEditor accounts={accounts} items={cycles} onChanged={()=>{setReload(n=>n+1);onChanged();}}/>
    {!cycles.length && <p className="bill-empty">尚无周期记录。点击刷新识别，至少3次连续周期的稳定流水可形成候选。</p>}
    <div className="bill-cycle-list">{cycles.map(item => <article key={item.stream_id}><div><strong>{item.merchant} · {item.kind === 'income' ? '周期收入' : '周期支出'}</strong><p>{money(item.amount_fen)} / {frequencies[item.frequency]||item.frequency} · {item.frequency==='WEEKLY'&&item.anchor_at?new Intl.DateTimeFormat('zh-CN',{weekday:'long',timeZone:'Asia/Shanghai'}).format(new Date(item.anchor_at)):item.month_end ? '固定月末' : `约定日期${item.anchor_day}日`} · {item.source==='manual'?'本人设置':`${item.sample_count} 笔依据`}</p><small>{item.account_id} · {item.status === 'ACTIVE' ? '有效' : item.status === 'CANCELLED' ? '已取消' : '已失效'} · {item.source === 'subscription' ? '银行订阅计划' : item.source==='manual'?'本人手动计划':'历史模式估计'}</small></div>
      {item.source === 'subscription' ? <button className="secondary" disabled={!onManage || !item.subscription_id} onClick={() => item.subscription_id && onManage?.(item.subscription_id)}>管理订阅</button> : <label><input type="checkbox" aria-label={`${item.merchant} 纳入预测`} disabled={busy || item.status !== 'ACTIVE'} checked={item.enabled && item.status === 'ACTIVE'} onChange={e => void act(() => bills.setRecurring(item, e.target.checked))} />纳入预测</label>}
      <details><summary>查看周期依据</summary><ul>{item.evidence_tx_ids.map(id => <li key={id}>{id}</li>)}</ul></details>
    </article>)}</div>
    {notices.map(n => <p className="hint" key={n}>{n}</p>)}
  </details>;
}

export function TransactionAnnotations({ tx, onSaved }: { tx: bills.BillTransaction; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [linked, setLinked] = useState<bills.BillTransaction | null>(null);
  const showLink = async (id: string) => {
    setError(''); setLinked(null);
    try { setLinked(await bills.transaction(id)); } catch (e) { setError(errorText(e)); }
  };
  return <div className="bill-annotations">
    {tx.merchant_id && <small title={tx.merchant_id}>商户：{tx.merchant_name} · {tx.merchant_id.slice(-8)}</small>}
    {tx.type === 'refund' && (tx.original_tx_id ? <button type="button" className="text-button" onClick={() => void showLink(tx.original_tx_id!)}>查看退款原单</button> : <small>退款未关联原单</small>)}
    {tx.refund_tx_ids?.map(id => <button key={id} type="button" className="text-button" onClick={() => void showLink(id)}>查看关联退款</button>)}
    {tx.paired_tx_id && <button type="button" className="text-button" onClick={() => void showLink(tx.paired_tx_id!)}>查看本人互转对应流水</button>}
    {linked && <div className="bill-linked"><strong>{linked.type === 'refund' ? '退款' : '关联流水'}</strong><small>{linked.tx_id} · {linked.counterparty} · {money(linked.amount_fen)} · {new Date(linked.timestamp).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai'})}</small><button type="button" className="text-button" onClick={() => setLinked(null)}>收起关联流水</button></div>}
    {tx.type==='purchase'&&!!tx.refunded_fen&&<small>累计已退 {money(tx.refunded_fen)} · 剩余未退 {money(tx.refundable_fen||0)}</small>}
    <Classification tx={tx} onSaved={onSaved}/>
    {tx.recurring_stream_id && <small>已关联周期记录</small>}
    <div className="bill-tag-list">{(tx.tags || []).map(t => <span key={t.tag_id}>{t.name}</span>)}</div>
    {!editing ? <button type="button" className="text-button" onClick={() => { setValue((tx.tags || []).map(t => t.name).join('，')); setEditing(true); setError(''); }}>编辑标签</button> : <form onSubmit={e => { e.preventDefault(); setBusy(true); setError(''); const tags=value.split(/[,，]/).map(s=>s.trim()).filter(Boolean); bills.saveTags(tx,tags).then(()=>{setEditing(false);onSaved();}).catch(e=>setError(errorText(e))).finally(()=>setBusy(false)); }}>
      <label>标签（逗号分隔，最多10个）<input aria-label={`${tx.tx_id} 标签`} value={value} onChange={e => setValue(e.target.value)} maxLength={249} /></label><button disabled={busy}>保存标签</button> <button type="button" className="secondary" disabled={busy} onClick={() => setEditing(false)}>返回</button>
    </form>}
    {error && <p className="error" role="alert">{error}</p>}
  </div>;
}
