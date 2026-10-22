import { useEffect, useRef, useState } from 'react';
import type { PortfolioPlan, PortfolioSelection, PortfolioSnapshot, PortfolioModelView } from '../../types/wealthPortfolio';
import styles from './PortfolioAllocation.module.css';
import { beijingTime } from '../../utils/time';
import { fenRatio, percentToRatio, ratioToPercent, ratioFen, portfolioExplanation, isIntegerPercentageRatio } from '../../services/wealthPortfolio';
export interface PortfolioAllocationProps {
  plan: PortfolioPlan | null; snapshot?: PortfolioSnapshot | null; modelView?: PortfolioModelView | null;
  loading?: boolean; error?: string | null; readOnly?: boolean; controlHint?: string; contextId: string;
  onAdjust: (selection: PortfolioSelection) => Promise<PortfolioPlan>;
  onPreferenceChange: (selection: PortfolioSelection) => Promise<PortfolioPlan>;
  onAccepted?: (plan: PortfolioPlan) => void;
  showProductEditor?: boolean;modelOnly?:boolean;
  assetTotals?: { available_cash_fen: number | null; held_fen: number | null } | null;
}
const names = { cash_management: '活钱', stable: '稳健', growth: '进取' };
const yuan = (fen: number | null | undefined) => fen == null ? '未知' : `¥${(fen / 100).toFixed(2)}`;
const percent = (ratio: string | number) => `${(Number(ratio) * 100).toFixed(3).replace(/0+$/, '').replace(/\.$/, '')}%`;
export default function PortfolioAllocation({ plan, snapshot, modelView, assetTotals, loading = false, error, readOnly = false, controlHint, contextId, onAdjust, onPreferenceChange, onAccepted, showProductEditor = true,modelOnly=false }: PortfolioAllocationProps) {
  const [accepted, setAccepted] = useState<PortfolioPlan | null>(plan);
  const [draft, setDraft] = useState(''); const [preference, setPreference] = useState('');
  const [pending, setPending] = useState(false); const [failure, setFailure] = useState<string | null>(null);
  const generation = useRef(0); const busy = useRef(false);
  const [percentages, setPercentages] = useState<Record<string, string>>({});
  const [cashPercent, setCashPercent] = useState('');
  useEffect(() => { generation.current++; busy.current = false; setPending(false); setAccepted(plan?.context_id === contextId ? plan : null); setDraft(''); setPreference(''); setFailure(null); }, [contextId]);
  useEffect(() => {
    if (!plan || plan.context_id !== contextId) { setAccepted(null); setPercentages({}); setCashPercent(''); return; }
    setAccepted(plan); setDraft(''); setFailure(null);
    setPercentages(Object.fromEntries((plan.advised_positions??[]).map(r => [r.product_id, ratioToPercent(plan.selected_positions.find(s => s.product_id === r.product_id)?.target_ratio_total ?? fenRatio(r.target_fen, plan.basis_fen))])));
    setCashPercent(ratioToPercent(fenRatio(plan.cash_residual_fen, plan.basis_fen)));
  }, [plan, contextId]);
  const inputView = accepted?.model_view ?? modelView; const view = inputView?.snapshot.context_id === contextId ? inputView : null;
  const inputSnapshot = view?.snapshot ?? snapshot; const snap = inputSnapshot?.context_id === contextId ? inputSnapshot : null;
  const bounds = view?.slider_bounds; const noncash = accepted ? accepted.basis_fen - accepted.buckets[0].target_fen : 0;
  const allowed = !!accepted && !!noncash && !!bounds && accepted.growth_ratio_ex_cash !== null;
  const disabled = readOnly || loading || pending || !accepted;
  const request = async (kind: 'adjustment' | 'preference') => {
    if (disabled || busy.current || !accepted) return;
    const value = kind === 'preference' ? preference : draft;
    if (!value || !Number.isFinite(Number(value)) || Number(value) < 0 || Number(value) > 1 || !isIntegerPercentageRatio(value)) { setFailure('请输入0至100的整数百分比'); return; }
    if(kind==='adjustment'&&(!bounds||Number(value)<Number(bounds[0])||Number(value)>Number(bounds[1])||ratioFen(value,noncash)===null)){setFailure('请输入当前可执行范围内、能表示为整数分的比例');return;}
    busy.current = true; setPending(true); setFailure(null); const stamp = generation.current;
    const selection: PortfolioSelection = { portfolio_id: accepted.portfolio_id, expected_version: accepted.version, snapshot_id: accepted.snapshot_id, context_id: accepted.context_id, items: accepted.selected_positions, revision_kind: kind, growth_ratio_ex_cash: value };
    try {
      const result = await (kind === 'preference' ? onPreferenceChange(selection) : onAdjust(selection));
      if (stamp !== generation.current || result.context_id !== contextId) return;
      setAccepted(result); setDraft(''); setPreference(''); onAccepted?.(result);
    } catch (e) { if (stamp === generation.current) setFailure(e instanceof Error ? e.message : '修订失败，原方案保留'); }
    finally { if (stamp === generation.current) { busy.current = false; setPending(false); } }
  };
  const ratio = draft || accepted?.growth_ratio_ex_cash || '0';
  const draftHint = draft && (!isIntegerPercentageRatio(draft) ? '请输入整数百分比，小数不能提交。' : bounds && (Number(draft)<Number(bounds[0]) || Number(draft)>Number(bounds[1])) ? '请输入当前可执行范围内的整数百分比。' : null);
  const applyProducts = async () => {
    if (disabled || busy.current || !accepted) return;
    const stamp = generation.current; busy.current = true; setPending(true); setFailure(null);
    try {
      const items = (accepted.advised_positions??[]).map(r => ({ product_id: r.product_id, product_version: r.product_version, share_class: r.share_class, target_ratio_total: percentToRatio(percentages[r.product_id] ?? '') }));
      const cash = ratioFen(percentToRatio(cashPercent), accepted.basis_fen); if (cash === null) throw new Error('现金百分比不能表示为整数分');
      const result = await onAdjust({ portfolio_id: accepted.portfolio_id, expected_version: accepted.version, snapshot_id: accepted.snapshot_id, context_id: accepted.context_id, revision_kind: 'adjustment', items, cash_residual_fen: Number(cash) });
      if (stamp !== generation.current || result.context_id !== contextId) return;
      setAccepted(result); onAccepted?.(result);
    } catch (e) { if (stamp === generation.current) setFailure(e instanceof Error ? e.message : '修订失败'); }
    finally { if (stamp === generation.current) { busy.current = false; setPending(false); } }
  };
  const draftAmounts = accepted && (accepted.advised_positions??[]).map(r => { try { return { bucket: r.bucket, fen: ratioFen(percentToRatio(percentages[r.product_id] ?? ''), accepted.basis_fen) }; } catch { return { bucket: r.bucket, fen: null }; } });
  if(modelOnly)return <section className={styles.panel} aria-label="整体配置"><h2>整体资产配置</h2>
    <p>总资产 {yuan(accepted?.basis_fen)} · 保留现金 {yuan(accepted?.planning?.reserve_fen??accepted?.cash_residual_fen)}。现金计入活钱；以下为模型推荐配置，尚未交易。</p>
    {accepted&&<><table><thead><tr><th>板块</th><th>当前资产</th><th>配置比例</th><th>目标金额</th></tr></thead><tbody>{accepted.buckets.map((b,i)=>{const suggestion=accepted.planning?.suggested_amounts_fen[i]??b.target_fen;return <tr key={b.bucket}><td>{names[b.bucket]}</td><td>{yuan(b.current_fen)}</td><td>{percent(suggestion/accepted.basis_fen)}</td><td>{yuan(suggestion)}</td></tr>;})}</tbody></table>
    <h3>本次实际执行</h3><p>旧持仓默认保留；仅为满足模型硬性限制建议赎回。以下为待确认金额，实际到账扣除费用后重新核对申购。</p>
    {(accepted.advised_positions??[]).filter(p=>p.target_fen>0||p.current_fen>0).map(p=><p key={p.product_id}>{p.name} · {p.target_fen>p.current_fen?`拟申购 ${yuan(p.target_fen-p.current_fen)}`:p.target_fen<p.current_fen?`为满足硬性限制，拟赎回约 ${yuan(p.current_fen-p.target_fen)}`:'保持现有持仓，无需交易'} · 当前 {yuan(p.current_fen)} → 目标 {yuan(p.target_fen)}</p>)}</>}
  </section>;
  return <section className={styles.panel} aria-label="整体配置">
    <h2>整体资产配置</h2><p className={styles.hint}>{controlHint || (readOnly ? '只读模式，不能修改方案' : '本人配置草稿；目标不等于购买授权')}</p>
    {(error || failure) && <div role="alert" className={styles.error}>{portfolioExplanation(failure || error!)}</div>}
    {loading && <p role="status">正在读取当前事实…</p>}
    <div className={styles.summary}><strong>整体总资产 {yuan(accepted?.basis_fen ?? snap?.total_assets_fen)}</strong><span>可支付现金 {yuan(view?.available_cash_fen ?? (snap ? assetTotals?.available_cash_fen : null))}</span><span>赎回应收 {yuan(view?.receivable_fen ?? snap?.receivable_fen)}</span><span>冻结 {yuan(view?.held_fen ?? (snap ? assetTotals?.held_fen : null))}（不重复计入总资产）</span></div>
    <p className={styles.hint}>资产状态：{(accepted?.basis_status ?? snap?.basis_status) === 'fixture_only' ? '隔离样例' : snap ? '已读取当前范围' : '尚未读取'} · 数据日期 {accepted?.as_of || snap?.as_of ? `${beijingTime(accepted?.as_of ?? snap!.as_of)} 北京时间` : '未知'}</p>
    <details className={styles.hint}><summary>资产来源</summary><p>{(accepted?.basis_status ?? snap?.basis_status) === 'fixture_only' ? '隔离测试资料' : '本人银行资产资料'} · 更新于 {accepted?.as_of || snap?.as_of ? beijingTime(accepted?.as_of ?? snap!.as_of) : '未知时间'}</p></details>
    {accepted ? <>
      <p>当前已接受方案 v<span data-testid="version">{accepted.version}</span>。</p>
      <table><thead><tr><th>板块</th><th>当前资产</th><th>初始基准</th><th>已接受目标</th><th>允许区间</th></tr></thead><tbody>{accepted.buckets.map((b, i) => <tr key={b.bucket}><td>{names[b.bucket]}</td><td>{b.current_fen === null ? '未知' : `${yuan(b.current_fen)}（${percent(b.current_fen / accepted.basis_fen)}）`}</td><td>{view ? percent(view.initial_ratios[i]) : '未知'}</td><td>{yuan(b.target_fen)}（{percent(b.target_ratio)}）<div className={styles.bar} style={{ width: percent(b.target_ratio) }} /></td><td>{percent(b.min_ratio)}–{percent(b.max_ratio)}</td></tr>)}</tbody></table>
      <p>各项范围不能任意组合，最终以服务端对整个方案的校验为准。</p>
      <h3>稳健／进取联动</h3>{draftHint && <p role="status">{draftHint}</p>}
      {allowed ? <><p>已接受进取比例（扣除活钱）<strong data-testid="accepted-p">{percent(accepted.growth_ratio_ex_cash!)}</strong></p><label>待提交进取比例<input className={styles.slider} aria-label="待提交进取比例" type="range" min={bounds![0]} max={bounds![1]} step={view?.slider_step??'0.01'} value={ratio} disabled={disabled} onChange={e => setDraft(e.target.value)} /></label><label>待提交进取百分比<input aria-label="待提交进取百分比" type="number" min={ratioToPercent(bounds![0])} max={ratioToPercent(bounds![1])} step={ratioToPercent(view?.slider_step??'0.01')} value={ratioToPercent(ratio)} disabled={disabled} onChange={e=>{try{setDraft(e.target.value?percentToRatio(e.target.value):'');}catch{setFailure('请输入有效百分比');}}}/> %</label><p>可执行范围 {percent(bounds![0])}—{percent(bounds![1])}，分母为总资产减活钱，步长 {percent(view?.slider_step??'0.01')}。</p><p data-testid="draft">草稿：稳健 {percent(1 - Number(ratio))}／进取 {percent(ratio)}；整体稳健 {percent(noncash * (1 - Number(ratio)) / accepted.basis_fen)}／进取 {percent(noncash * Number(ratio) / accepted.basis_fen)}。活钱保持 {yuan(accepted.buckets[0].target_fen)}。</p><button disabled={disabled || !draft || !isIntegerPercentageRatio(draft) || !bounds || Number(draft)<Number(bounds[0]) || Number(draft)>Number(bounds[1])} onClick={() => void request('adjustment')}>应用本页调整</button></> : <p>{noncash?'当前原U及产品规则内没有可行的整数百分比，保留已接受方案。':'当前为全活钱配置，稳健／进取比例不适用。'}</p>}
      <div><label>明确重新设置进取偏好（整数百分比）<input aria-label="重新设置进取偏好" type="number" step="1" min="0" max="100" value={preference?ratioToPercent(preference):''} onChange={e => {try{setPreference(e.target.value?percentToRatio(e.target.value):'');}catch{setFailure('请输入整数百分比');}}} disabled={disabled || !allowed} /></label><button disabled={disabled || !allowed || !preference || !isIntegerPercentageRatio(preference)} onClick={() => void request('preference')}>重新计算偏好</button></div>
      {pending && <p role="status">修订中，成功后更新已接受值…</p>}
      {(['advice', 'selected', 'holding'] as const).map(role => <div key={role}><h3>{{ advice: '建议产品目标（尚未选择）', selected: '显式新选目标（已有持仓的明确调整见下）', holding: '已有持仓维持／拟减持目标' }[role]}</h3>{(accepted.advised_positions??[]).filter(r => r.role === role).map(r => <p key={r.product_id}>{r.name} {r.original_share_class ?? ''} · 目标 {yuan(r.target_fen)} · 当前 {yuan(r.current_fen)}{r.account_ids.length ? ` · 涉及 ${r.account_ids.length} 个账户` : ''}{r.role === 'holding' && accepted.selected_positions.some(s => s.product_id === r.product_id) ? ' · 已明确选择此持仓目标' : ''}{r.holding_cap_fen !== null ? ` · 仅持有上限 ${yuan(r.holding_cap_fen)}` : ''} · 费用与成交价格以银行确认页为准</p>)}</div>)}
      <p>保留现金目标 {yuan(accepted.cash_residual_fen)}；配置中应收或冻结资金不代表可立即购买。</p>
      {showProductEditor&&<><h3>具体产品百分比草稿（占整体总资产）</h3><p>输入为百分数；提交使用精确十进制比例和整数分，服务端复核整个方案。失败不会替换已接受版本。</p>
      {(accepted.advised_positions??[]).map(r => <label key={r.product_id} className={styles.editRow}>{r.name} {r.original_share_class ?? ''}<input aria-label={'百分比 ' + r.name} value={percentages[r.product_id] ?? ''} disabled={disabled} onChange={e => setPercentages(p => ({ ...p, [r.product_id]: e.target.value }))} />%</label>)}
      <label>保留现金百分比<input aria-label="现金百分比" value={cashPercent} disabled={disabled} onChange={e => setCashPercent(e.target.value)} />%</label>
      <p data-testid="product-draft">各板块产品金额：{Object.entries(names).map(([b, name]) => { const rows = draftAmounts?.filter(r => r.bucket === b) ?? []; return `${name} ${rows.some(r => r.fen === null) ? '未知／不足1分' : yuan(Number(rows.reduce((sum, r) => sum + r.fen!, 0n)))}；`; })}现金由单独草稿表示，尾差不自动隐藏。</p>
      <button disabled={disabled} onClick={() => void applyProducts()}>应用产品百分比</button></>}
    </> : <p data-testid="no-plan">当前没有可用方案。{snap ? !snap.valuation_complete ? '持仓估值缺失，总资产与目标保持未知。' : '资产已读取；整体方案尚未生成。推荐资格与创建控制请见独立页面状态说明。' : '资产尚未读取，请查看来源状态。'}</p>}
  </section>;
}
