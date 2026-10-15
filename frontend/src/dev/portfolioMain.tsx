import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import PortfolioAllocation from '../components/Wealth/PortfolioAllocation';
import { portfolioAdapter, portfolioExplanation, type PortfolioAdapter } from '../services/wealthPortfolio';
import type { CandidateReference, CandidateSession, PortfolioCandidate, PortfolioCandidateGroup, PortfolioFilter, PortfolioPlan, PortfolioSelection, PortfolioShare, PortfolioSnapshot } from '../types/wealthPortfolio';
import styles from '../components/Wealth/PortfolioAllocation.module.css';
declare const __PORTFOLIO_FIXTURE__: boolean;
const fixture = __PORTFOLIO_FIXTURE__;
const categories = ['quick_redeem', 'bank_wealth',  'bond_fund', 'mixed_fund', 'equity_fund'] as const;
const themes = ['high_dividend', 'bond_income', 'broad_market'];
const themeNames: Record<string, string> = { high_dividend: '高股息／红利', bond_income: '债券收益', broad_market: '宽基市场' };
const categoryNames: Record<string, string> = { quick_redeem: '快赎理财', bank_wealth: '银行理财', bond_fund: '债券基金', mixed_fund: '混合基金', equity_fund: '股票基金' };
const statusNames: Record<string, string> = { ok: '本批候选资格已核实', no_theme_match: '当前资料无主题匹配', no_category_match: '当前范围无类别匹配', no_eligible_products: '当前没有满足资格的产品', risk_mismatch: '匹配产品均超出本人风险等级', unavailable: '推荐资格或来源证据不足，暂不可用' };
const scenarios = ['R1', 'R2', 'R4', 'F18', 'only-C', 'single-share', 'holding-only', 'missing-valuation', 'source-unavailable', 'version-conflict'];
const message = (e: unknown) => e instanceof Error ? e.message : '请求失败';
const metricText = (p: Record<string, unknown>) => Object.entries(p).map(([key, raw]) => {
  const value = raw as { metric?: string; value_percent?: number | null; reason?: string; start_date?: string; end_date?: string };
  return `${key === 'one_year' ? '近一年' : '最新日'} ${value.metric ?? '指标未知'}：${value.value_percent == null ? value.reason ?? '未取得' : value.value_percent + '%'} ${value.start_date ?? ''} ${value.end_date ?? ''}`;
}).join('；');
function Dev() {
  const [scenario, setScenario] = useState('R4'), [refresh, setRefresh] = useState(0);
  const [plan, setPlan] = useState<PortfolioPlan | null>(null), [snapshot, setSnapshot] = useState<PortfolioSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null), [loading, setLoading] = useState(false);
  const [canEdit, setCanEdit] = useState(false), [writeReason, setWriteReason] = useState<string | null>('控制尚未核实');
  const [readOnly, setReadOnly] = useState(false), [invalid, setInvalid] = useState(false);
  const [adapter, setAdapter] = useState<PortfolioAdapter | null>(null), [context, setContext] = useState('');
  const [token, setToken] = useState(''), [phone, setPhone] = useState(''), [password, setPassword] = useState('');
  const [filter, setFilter] = useState<PortfolioFilter>({ mode: 'general' }), [group, setGroup] = useState<PortfolioCandidateGroup | null>(null);
  const [session, setSession] = useState<CandidateSession | null>(null), [cards, setCards] = useState<Record<string, PortfolioCandidate>>({});
  const [variants, setVariants] = useState<Record<string, string>>({}); const generation = useRef(0), busy = useRef(false);
  const [initialP, setInitialP] = useState<string | null>(null);
  const [assetTotals, setAssetTotals] = useState<{ available_cash_fen: number | null; held_fen: number | null } | null>(null);
  const selected = session ? Object.values(session.selected).map(e => ({ product_id: e.reference.product_id, product_version: e.reference.product_version, share_class: e.reference.share_class })) : [];
  const binding = session ? { session_id: session.session_id, expected_version: session.version, filter: group!.filter } : null;
  const clear = () => { generation.current++; busy.current = false; setAdapter(null); setContext(''); setPlan(null); setSnapshot(null); setAssetTotals(null); setGroup(null); setSession(null); setCards({}); setVariants({}); setError(null); setInvalid(false); setCanEdit(false); setWriteReason('控制尚未核实'); };
  useEffect(() => {
    clear(); const stamp = generation.current;
    if (!fixture && !token) return;
    setLoading(true);
    void (async () => {
      try {
        let api: PortfolioAdapter, ctx: string;
        if (fixture) {
          const response = await fetch('/dev/bootstrap?scenario=' + encodeURIComponent(scenario));
          if (!response.ok) throw new Error(`${response.status} 隔离来源启动失败`);
          const boot = await response.json(); api = portfolioAdapter(boot.token, scenario); ctx = boot.context_id;
          if (stamp === generation.current) setInitialP(boot.initial_p);
        } else { api = portfolioAdapter(token); ctx = ''; setInitialP(null); }
        const controls = await api.controls(); ctx = controls.context_id;
        if (stamp !== generation.current) return;
        setAdapter(api); setContext(ctx); setCanEdit(controls.can_edit); setWriteReason(controls.write_reason); setAssetTotals(controls.asset_totals);
        const snap = await api.snapshot(); if (snap.snapshot_id !== controls.snapshot_id) throw new Error('409 资产读取期间发生变化，请刷新事实'); if (stamp === generation.current) setSnapshot(snap);
      } catch (e) { if (stamp === generation.current) setError(message(e)); }
      finally { if (stamp === generation.current) setLoading(false); }
    })();
    return () => { generation.current++; };
  }, [scenario, refresh, token]);
  const run = async (action: () => Promise<void>) => {
    if (busy.current) return; busy.current = true; setLoading(true); setError(null); const stamp = generation.current;
    try { await action(); } catch (e) { if (stamp === generation.current) setError(message(e)); }
    finally { if (stamp === generation.current) { busy.current = false; setLoading(false); } }
  };
  const login = () => run(async () => {
    const response = await fetch('/api/v1/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ phone, password }) });
    const body = await response.json(); if (!response.ok) throw new Error(`${response.status} 登录失败`);
    setPassword(''); setPhone(''); setToken(body.access_token);
  });
  const merge = (result: PortfolioCandidateGroup) => {
    setGroup(result); setSession(result.session_binding as unknown as CandidateSession | null);
    setCards(old => ({ ...old, ...Object.fromEntries(result.items.map(c => [c.family_id, c])) }));
  };
  const recommend = (next = false, keep = selected) => run(async () => {
    if (!adapter) throw new Error('来源未就绪'); const stamp = generation.current;
    const result = await adapter.recommend({ context_id: context, filter, selected: keep, ...(next ? { cursor: session?.cursor, candidate_binding: binding } : {}) });
    if (stamp === generation.current) merge(result);
  });
  const reference = (card: PortfolioCandidate, share: PortfolioShare): CandidateReference => {
    const decisions = card.evidence.share_decisions as { product_id: string; evidence: { bank_fact: { rules_version: string | number } } }[];
    const rules = decisions?.find(d => d.product_id === share.product_id)?.evidence.bank_fact.rules_version;
    if (rules == null) throw new Error('当前份额规则版本未取得');
    return { product_id: share.product_id, product_version: share.product_version, disclosure_version: share.disclosure_version, share_class: share.share_class, rules_version: rules, target_ratio_total: '0' };
  };
  const choose = (card: PortfolioCandidate, share: PortfolioShare, remove = false) => run(async () => {
    if (!adapter || !session) throw new Error('请刷新候选'); const stamp = generation.current;
    if (remove) {
      const result = await adapter.recommend({ context_id: context, filter: group!.filter, selected: selected.filter(r => r.product_id !== share.product_id) });
      if (stamp === generation.current) merge(result); return;
    }
    const old = Object.values(session.selected).find(e => e.family_id === card.family_id);
    const result = old ? await adapter.switchShare(context, session.session_id, session.version, card.family_id, reference(card, share)) : await adapter.select(context, session.session_id, session.version, [reference(card, share)]);
    if (stamp !== generation.current) return;
    if (!result.selection.accepted) throw new Error('资格拒绝：' + result.selection.reason_codes.join('；'));
    setSession(result.session); setVariants(v => ({ ...v, [card.family_id]: share.product_id }));
  });
  const create = () => run(async () => {
    if (!adapter) throw new Error('来源未就绪'); const stamp = generation.current;
    const result = await adapter.create(context, initialP, session ? { selected, candidate_binding: binding } : {});
    if (stamp === generation.current) { setPlan(result); setInvalid(false); }
  });
  const read = () => run(async () => {
    if (!adapter || !plan) return; const stamp = generation.current;
    try { const controls = await adapter.controls(); if (stamp === generation.current) { setCanEdit(controls.can_edit); setWriteReason(controls.write_reason); }
      const result = await adapter.read(plan.portfolio_id, context); if (stamp === generation.current) { setPlan(result); setInvalid(false); } }
    catch (e) { if (stamp === generation.current) setInvalid(true); throw e; }
  });
  const revise = async (selection: PortfolioSelection) => {
    if (!adapter) throw new Error('来源未就绪'); const stamp = generation.current;
    const controls = await adapter.controls(); if (stamp !== generation.current) throw new Error('旧上下文响应已忽略');
    setCanEdit(controls.can_edit); setWriteReason(controls.write_reason);
    if (!controls.can_edit) throw new Error(controls.write_reason ?? '写入控制未知');
    return adapter.revise(selection);
  };
  const changeFilter = (f: PortfolioFilter) => { generation.current++; busy.current = false; setLoading(false); setFilter(f); setGroup(null); setSession(null); setCards({}); setVariants({}); setError(null); };
  const blocked = loading || !adapter; const writeBlocked = blocked || readOnly || !canEdit || invalid;
  const renderCard = (card: PortfolioCandidate) => {
    const old = session && Object.values(session.selected).find(e => e.family_id === card.family_id);
    const wanted = old?.reference.product_id ?? variants[card.family_id] ?? (card.evidence.default_share as CandidateReference)?.product_id;
    const share = card.share_classes.find(s => s.product_id === wanted) ?? card.share_classes[0];
    const canSwitch = card.share_classes.filter(s => s.tradable && s.share_class != null).length > 1;
    return <article key={card.family_id} className={styles.card} data-family={card.family_id}>
      <label><input type="checkbox" aria-label={'选择 ' + card.family_id} checked={!!old} disabled={blocked || !share.tradable || readOnly} onChange={e => void choose(card, share, !e.target.checked)} />{card.name} · {card.bucket}</label>
      <p data-testid={'share-' + card.family_id}>{share.product_id} {share.share_class ?? '单一份额'} · R{share.product_risk_level ?? '未知'} · {share.status}</p>
      <p>{card.reason}</p><p>来源 {share.data_source ?? '未取得'} · {share.data_as_of ?? '日期未取得'}</p>
      <p>申购费 {share.fees.purchase_fee_rate ?? '未取得'}；销售服务费 {share.fees.sales_service_fee_rate ?? '未取得'}</p><p>{metricText(share.performance)}</p>
      {canSwitch && <label>具体份额 <select aria-label={'份额 ' + card.family_id} value={share.product_id} disabled={blocked || readOnly} onChange={e => { const s = card.share_classes.find(s => s.product_id === e.target.value)!; if (old) void choose(card, s); else setVariants(v => ({ ...v, [card.family_id]: s.product_id })); }}>{card.share_classes.filter(s => s.tradable).map(s => <option key={s.product_id} value={s.product_id}>{s.product_id} {s.share_class}</option>)}</select></label>}
      <details><summary>资格证据与版本</summary><pre>{JSON.stringify(card.evidence, null, 2)}</pre></details>
    </article>;
  };
  return <main><header className={styles.panel}><h1>01 整体配置独立验收</h1><strong>{fixture ? '隔离功能测试，非真实推荐' : '比赛银行事实 · 模拟资产，非真实资金'}</strong><p>本页只形成配置草稿，不进行交易或授权。</p>
    {fixture ? <label>隔离场景 <select aria-label="场景" value={scenario} onChange={e => setScenario(e.target.value)}>{scenarios.map(s => <option key={s}>{s}</option>)}</select></label> : !token && <form onSubmit={e => { e.preventDefault(); void login(); }}><label>手机号<input aria-label="登录手机号" value={phone} onChange={e => setPhone(e.target.value)} autoComplete="off" /></label><label>密码<input aria-label="登录密码" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="off" /></label><button disabled={loading}>登录现有比赛银行</button></form>}
    <button disabled={!fixture && !token} onClick={() => { clear(); setRefresh(r => r + 1); }}>刷新事实并清除草稿</button>
    {!fixture && token && <button onClick={() => { clear(); setToken(''); }}>退出独立页面</button>}
    <label><input type="checkbox" checked={readOnly} onChange={e => setReadOnly(e.target.checked)} />只读／人工建议模式</label>
    <div data-testid="controls"><p>方案创建与修订：{canEdit ? '已核实可编辑' : portfolioExplanation(writeReason ?? '控制尚未核实，不允许编辑')}{invalid ? ' · 当前方案已失效，不可继续修改' : ''}</p>{writeReason && <details><summary>写控制证据</summary>{writeReason}</details>}</div>
    {error && <div role="alert" className={styles.error}>{portfolioExplanation(error)}<details><summary>具体原因</summary>{error}</details></div>}
  </header>
    <PortfolioAllocation contextId={context} plan={plan} snapshot={snapshot} assetTotals={assetTotals} loading={loading} readOnly={writeBlocked} controlHint={invalid ? '旧方案失效，仅供核对；请刷新事实。' : writeReason ? portfolioExplanation(writeReason) : undefined} onAdjust={revise} onPreferenceChange={revise} onAccepted={setPlan} />
    {snapshot && <section className={styles.panel}><h2>当前持仓</h2>{snapshot.positions.length ? snapshot.positions.map(p => <p key={p.position_key}>{p.product_id} · 账户 {p.account_id} · 市值 {p.market_value_fen == null ? '未知' : '¥' + (p.market_value_fen / 100).toFixed(2)}</p>) : <p>当前范围无持仓</p>}</section>}
    <section className={styles.panel}><h2>独立产品候选</h2><label>筛选模式 <select aria-label="筛选模式" value={filter.mode} disabled={loading} onChange={e => changeFilter(e.target.value === 'general' ? { mode: 'general' } : e.target.value === 'category' ? { mode: 'category', category: 'bond_fund' } : { mode: 'theme', theme_key: 'high_dividend' })}><option value="general">普通</option><option value="category">类别</option><option value="theme">主题</option></select></label>
      {filter.mode === 'category' && <select aria-label="产品类别" value={filter.category ?? ''} disabled={loading} onChange={e => changeFilter({ mode: 'category', category: e.target.value as PortfolioFilter['category'] })}>{categories.map(c => <option key={c} value={c}>{categoryNames[c]}</option>)}</select>}
      {filter.mode === 'theme' && <><label>主题<select aria-label="推荐主题" value={filter.theme_key ?? ''} disabled={loading} onChange={e => changeFilter({ mode: 'theme', theme_key: e.target.value })}><option value="" disabled>自定义组合</option>{themes.map(t => <option key={t} value={t}>{themeNames[t]}</option>)}</select></label><details><summary>主题参数与组合</summary><label>主题键<input aria-label="主题键" list="themes" value={filter.theme_key ?? filter.theme_keys?.join(',') ?? ''} disabled={loading} onChange={e => changeFilter({ mode: 'theme', ...(e.target.value.includes(',') ? { theme_keys: e.target.value.split(','), theme_operator: 'AND' } : { theme_key: e.target.value }) })} /></label><datalist id="themes">{themes.map(t => <option key={t} value={t}>{themeNames[t]}</option>)}</datalist>{filter.theme_keys?.length && <select aria-label="主题组合" value={filter.theme_operator ?? 'AND'} onChange={e => changeFilter({ ...filter, theme_operator: e.target.value as 'AND' | 'OR' })}><option>AND</option><option>OR</option></select>}</details></>}
      <button disabled={blocked} onClick={() => void recommend()}>推荐候选</button><button disabled={blocked || !session?.cursor} onClick={() => void recommend(true)}>换一批</button>
      {group && <div><p data-testid="candidate-summary">{statusNames[group.status]}{!session?.cursor && group.status === 'ok' ? ' · 池已耗尽' : ''}</p>{(group.reason_codes?.length ?? 0) > 0 && <p>{portfolioExplanation((group.reason_codes ?? []).join(';'))}</p>}<details><summary>推荐状态与全部原因详情</summary><p data-testid="candidate-status">{group.status} · {group.reason_codes?.join('；') || '本批资格与资料已核实'}</p></details></div>}
      {group?.status === 'no_theme_match' && <p>当前资料没有匹配此主题的产品。</p>}{group?.status === 'no_category_match' && <p>当前范围没有此类产品。</p>}{group?.status === 'risk_mismatch' && <p>匹配产品均超过当前正式风险等级；不能通过小权重购买。</p>}{group?.status === 'unavailable' && <p>必要评估、资料或来源未取得，推荐关闭。</p>}
      <div className={styles.grid}>{group?.items.map(renderCard)}</div>
      <h3>跨批已选 <span data-testid="selected-count">{selected.length}</span></h3><div className={styles.grid}>{session && Object.values(session.selected).map(e => cards[e.family_id] ? renderCard(cards[e.family_id]) : <p key={e.reference.product_id}>{e.reference.product_id} · {e.valid ? '已选' : '失效'} · {e.reason_codes.join('；')}</p>)}</div>
      <button disabled={writeBlocked} onClick={() => void create()}>创建整体配置草稿</button><button disabled={blocked || !plan} onClick={() => void read()}>读取当前方案</button>
    </section>
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><Dev /></React.StrictMode>);
