import { useEffect, useState } from 'react';
import * as w from '../../services/wealth';
import type { Account } from '../../services/bank';
import './styles.css';
import Research from './Research';

interface Props { accounts: Account[]; revision: number; onActivity: () => void; userId: string }
type Tab = 'assets' | 'products' | 'plans' | 'orders';
const id = () => `WO${crypto.randomUUID().replace(/-/g, '')}`;
const statusLabel: Record<string, string> = { ACCEPTED: '已受理／资金或份额占用', PENDING_PRICING: '等待计价',
  PENDING_CASH: '等待赎回到账', SETTLED: '已结算', CANCELLED: '已撤单', UNKNOWN: '结果未知，须原号核对',
  AWAITING_CONFIRMATION: '等待本人确认', WAITING_SETTLEMENT: '等待结算', COMPLETED: '完成', PAUSED: '已暂停',
  PARTIALLY_COMPLETED: '部分完成', EXECUTING: '执行中', SUBMITTING: '正在提交' };
const disclosureLabels: Record<string, string> = { version: '披露版本', nature: '产品性质', manager: '模拟管理机构', seller: '展示与销售角色', risk: '风险说明', fees: '费用', liquidity: '流动性', holding_period_days: '最低持有天数', pricing: '计价规则', settlement: '结算与到账', cancellation: '撤单窗口', limitations: '模拟范围' };
const displayDisclosure = (value: unknown): string => value == null ? '未提供' : Array.isArray(value) ? value.map(displayDisclosure).join('；') : typeof value === 'object' ? Object.entries(value).map(([key, item]) => `${key}：${displayDisclosure(item)}`).join('；') : String(value);
const errorText = (error: unknown) => {
  const message = (error as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message;
  return message || (error instanceof Error ? error.message : '请求失败，请保留原操作号核对');
};

export default function Wealth({ accounts, revision, onActivity, userId }: Props) {
  const [tab, setTab] = useState<Tab>('assets');
  const [products, setProducts] = useState<w.Product[]>([]);
  const [assets, setAssets] = useState<w.Assets | null>(null);
  const [assessment, setAssessment] = useState<w.Assessment | null>(null);
  const [questionnaire, setQuestionnaire] = useState<w.Questionnaire | null>(null);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [adult, setAdult] = useState(false);
  const [profile, setProfile] = useState<w.Profile | null>(null);
  const [income, setIncome] = useState('0'); const [expenses, setExpenses] = useState('0'); const [debt, setDebt] = useState('0');
  const [externalName, setExternalName] = useState(''); const [externalValue, setExternalValue] = useState('0'); const [externalCategory, setExternalCategory] = useState('bond');
  const [goalName, setGoalName] = useState(''); const [goalAmount, setGoalAmount] = useState(''); const [goalDate, setGoalDate] = useState('2027-01-01');
  const [editingGoal, setEditingGoal] = useState<w.Goal | null>(null);
  const [goalLiquidity, setGoalLiquidity] = useState('immediate'); const [goalPriority, setGoalPriority] = useState('1');
  const [goalSource, setGoalSource] = useState(''); const [goalAssigned, setGoalAssigned] = useState('0');
  const [plans, setPlans] = useState<w.Plan[]>([]);
  const [plan, setPlan] = useState<w.Plan | null>(null);
  const [account, setAccount] = useState(''); const [budget, setBudget] = useState('20000'); const [reserve, setReserve] = useState('5000');
  const [horizon, setHorizon] = useState('90'); const [excluded, setExcluded] = useState<string[]>([]); const [kind, setKind] = useState<'new_cash' | 'adjustment'>('new_cash');
  const [selected, setSelected] = useState<string[]>([]);
  const [product, setProduct] = useState(''); const [direction, setDirection] = useState<'purchase' | 'redeem'>('purchase');
  const [amount, setAmount] = useState('1000'); const [units, setUnits] = useState('');
  const [preview, setPreview] = useState<w.Preview | null>(null); const [consent, setConsent] = useState(false); const [code, setCode] = useState('');
  const [pending, setPending] = useState<{ authorization_id: string; command: w.Command; status: string }[]>([]);
  const [executions, setExecutions] = useState<{ authorization_id: string; operation_id: string; status: string }[]>([]);
  const [adopted, setAdopted] = useState<string[]>([]);
  const [childAmounts, setChildAmounts] = useState<Record<string, string>>({});
  const [verificationMode, setVerificationMode] = useState<w.VerificationMode | null>(null);
  const [groupPreview, setGroupPreview] = useState<w.PlanPreview | null>(null);
  const [groupConsent, setGroupConsent] = useState(false);
  const [groupStage, setGroupStage] = useState<'PREVIEW' | 'CONFIRMED' | 'VERIFIED'>('PREVIEW');
  const [busy, setBusy] = useState(false); const [notice, setNotice] = useState('');
  const [chatInput, setChatInput] = useState(''); const [chat, setChat] = useState<w.ChatReply | null>(null);
  const [session, setSession] = useState<{ session_id: string; version: number } | null>(null);

  const load = async () => {
    const [p, a, r, q, f, plansList, pendingList, executionsList, history, mode] = await Promise.all([
      w.get<w.Product[]>('/products'), w.get<w.Assets>('/assets'), w.get<w.Assessment | null>('/assessments/current'),
      w.get<w.Questionnaire>('/questionnaire'), w.get<w.Profile>('/profile'), w.get<w.Plan[]>('/plans'),
      w.get<typeof pending>('/previews'), w.get<typeof executions>('/executions'),
      w.get<{ session_id: string; version: number; history: { reply: w.ChatReply }[] } | null>('/messages/history'), w.get<w.VerificationMode>('/verification-mode')]);
    setVerificationMode(mode);
    setProducts(p); setAssets(a); setAssessment(r); setQuestionnaire(q); setProfile(f); setPlans(plansList); setPending(pendingList);
    setProduct(current => p.some(item => item.product_id === current) ? current : p[0]?.product_id || '');
    setAccount(current => a.cash.some(item => item.account_id === current) ? current : a.cash.find(item => item.account_type !== 'credit')?.account_id || '');
    setExecutions(executionsList);
    if (history) { setSession({ session_id: history.session_id, version: history.version }); setChat(current => current || history.history[history.history.length - 1]?.reply || null); }
  };
  useEffect(() => { load().catch(error => setNotice(errorText(error))); }, [revision, userId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (profile) { setIncome(String((profile.data.monthly_income_fen || 0) / 100)); setExpenses(String((profile.data.monthly_expenses_fen || 0) / 100)); setDebt(String((profile.data.liabilities_fen || 0) / 100)); } }, [profile?.version]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (plan) { setAdopted(plan.items.map(item => item.command.operation_id)); setChildAmounts(Object.fromEntries(plan.items.map(item => [item.command.operation_id, item.command.direction === 'purchase' ? String(item.command.amount_fen / 100) : item.command.units]))); } }, [plan?.plan_id, plan?.version]); // eslint-disable-line react-hooks/exhaustive-deps
  const act = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setNotice('');
    try { await action(); await load(); } catch (error) { setNotice(errorText(error)); } finally { setBusy(false); }
  };
  const showPreview = async (command: w.Command) => {
    const view = await w.post<w.Preview>('/previews', command);
    setPreview(view); setConsent(false); setCode('');
  };
  const planRequest = (): w.PlanRequest => ({ kind, account_id: account, version: plan?.version || 1,
    budget_fen: w.fen(budget), reserve_fen: w.fen(reserve), horizon_days: Number(horizon), excluded_products: excluded });
  const previewAdopted = async () => {
    if (!plan) return;
    const picks = plan.items.filter(item => adopted.includes(item.command.operation_id)).map(item => ({ operation_id: item.command.operation_id,
      ...(item.command.direction === 'purchase' ? { amount_fen: w.fen(childAmounts[item.command.operation_id]) } : { units: childAmounts[item.command.operation_id] }) }));
    const current = await w.post<w.Plan>(`/plans/${plan.plan_id}/adopt`, { version: plan.version, items: picks });
    setPlan(current); setPreview(null);
    if (!current.items.length) { setNotice('方案全部保留现金，无需交易确认。'); return; }
    const views = await w.post<w.PlanPreview>(`/plans/${current.plan_id}/preview`, { version: current.version });
    setGroupPreview(views); setGroupConsent(false); setGroupStage('PREVIEW');
  };
  const send = async (text: string) => {
    const storageKey = `wealth-message-${userId}`;
    const old = localStorage.getItem(storageKey);
    const body = old ? JSON.parse(old) : { message: text, ...session, client_message_id: crypto.randomUUID() };
    if (body.message !== text) throw new Error('上一次请求尚未核对，请重试原消息。');
    localStorage.setItem(storageKey, JSON.stringify(body));
    let reply: w.ChatReply;
    try { reply = await w.post<w.ChatReply>('/messages', body); }
    catch (error) {
      const detail = (error as { response?: { data?: { detail?: { code?: string } } } })?.response?.data?.detail;
      if (detail?.code === 'STALE_PLAN' || detail?.code === 'IDEMPOTENCY_CONFLICT') { localStorage.removeItem(storageKey); await load(); }
      throw error;
    }
    localStorage.removeItem(storageKey);
    setChat(reply); setChatInput('');
    if (reply.session_id && reply.version) setSession({ session_id: reply.session_id, version: reply.version });
    if (reply.plan) { setPlan(reply.plan); setTab('plans'); }
    if (reply.command) await showPreview(reply.command);
  };
  const accountOptions = assets?.cash.filter(a => a.account_type !== 'credit').map(a => <option key={a.account_id} value={a.account_id}>{accounts.find(acc => acc.account_id === a.account_id)?.card_number || `模拟账户 ${a.account_id.slice(-4)}`}</option>);
  const chosen = products.find(p => p.product_id === product);
  const assetSources = [...(assets?.cash.filter(a => a.account_type !== 'credit').map(a => ({ ref: `CASH:${a.account_id}`, label: `银行现金 ${w.money(a.available_fen)}` })) || []),
    ...(assets?.lots.map(lot => ({ ref: `LOT:${lot.lot_id}`, label: `${lot.product_name} ${w.money(lot.market_value_fen)}` })) || []),
    ...(profile?.data.external_assets?.map(asset => ({ ref: `EXT:${asset.asset_id}`, label: `${asset.label}（外部手填）` })) || [])];

  return <section className="card wealth" aria-label="理财模拟">
    <div className="wealth-title"><div><h2>理财模拟</h2><p>比赛模拟环境／模拟资金 · 请仅输入虚构数据 · 本金可能亏损</p></div><button className="secondary" disabled={busy} onClick={() => act(async () => { onActivity(); })}>刷新银行事实</button></div>
    {verificationMode?.simulated && <p className="notice">演示授权：理财申购／赎回属于高风险操作。点击按钮模拟支付密码授权，不采集或验证真实密码、短信码、指纹或人脸。</p>}
    <nav aria-label="理财功能">{([['assets', '资产与目标'], ['products', '产品探索'], ['plans', '配置方案'], ['orders', '持仓与订单']] as [Tab, string][]).map(([key, title]) =>
      <button key={key} className={tab === key ? '' : 'secondary'} onClick={() => setTab(key)}>{title}</button>)}</nav>
    {notice && <p role="alert" className="notice">{notice}</p>}
    <Research onChoose={pid => { setProduct(pid); setTab('products'); }} onSwitch={async () => { setSession(null); setChat(null); setPreview(null); setPlan(null); setGroupPreview(null); localStorage.removeItem(`wealth-message-${userId}`); await load(); }} />
    <div className="wealth-layout"><div>
      {tab === 'assets' && <>
        <h3>银行资产</h3><p>合计 {w.money(assets?.bank_assets_fen)} · 赎回应收 {w.money(assets?.receivable_fen)} · 已实现盈亏 {w.money(assets?.realized_pnl_fen)}</p>
        {assets?.concentration && <details><summary>组合集中度与历史假设</summary><p>A/C按同一底层家族合并；{assets.concentration.overconcentrated_families.length ? `超过60%的家族：${assets.concentration.overconcentrated_families.join('、')}` : '未发现超过60%的家族；不代表风险已分散'}</p>{Object.entries(assets.concentration.families_fen).map(([family,amount]) => <p key={family}>{family}：{w.money(amount)}</p>)}<p>实际持仓收益率：缺完整现金流资料，未计算。</p>{assets.historical_hypothesis?.metrics && <p>当前金额作假设起点配置：单位净值区间变动 {assets.historical_hypothesis.metrics.return == null ? '不可算' : `${(Number(assets.historical_hypothesis.metrics.return)*100).toFixed(2)}%`}；回撤 {assets.historical_hypothesis.metrics.max_drawdown == null ? '不可算' : `${(Number(assets.historical_hypothesis.metrics.max_drawdown)*100).toFixed(2)}%`}</p>}<p>{assets.historical_hypothesis?.notice || assets.concentration.notice}</p></details>}
        {assets?.cash.map(a => <p key={a.account_id}>账户尾号 {accounts.find(acc => acc.account_id === a.account_id)?.card_number.slice(-4)}：账面 {w.money(a.balance_fen)}，占款 {w.money(a.held_fen)}，可用 {w.money(a.available_fen)} {a.account_type === 'credit' && '（信用账户不可投资）'}</p>)}
        <h3>本人风险评估</h3><p>{assessment ? `模拟承受能力 R${assessment.capacity_level} · ${assessment.confirmed ? '已确认' : '待确认'} · 有效至 ${assessment.expires_at.slice(0, 10)}` : '尚未评估，仍可查看客观产品资料'}</p>
        <details><summary>填写／重新评估（本人回答，单日最多2次）</summary>
          {questionnaire?.questions.map(question => <label key={question.id}>{question.title}<select value={answers[question.id] ?? ''} onChange={e => setAnswers({ ...answers, [question.id]: Number(e.target.value) })}><option value="" disabled>请选择本人答案</option>{question.options.map((option, index) => <option key={option} value={index}>{option}</option>)}</select></label>)}
          <label className="wealth-check"><input type="checkbox" checked={adult} onChange={e => setAdult(e.target.checked)} />我是成年、完全民事行为能力的模拟客户，以上由本人回答</label>
          <button disabled={busy || !adult || Object.keys(answers).length !== 9} onClick={() => act(async () => {
            const result = await w.post<w.Assessment>('/assessments', { questionnaire_version: questionnaire?.version,
              profile_version: questionnaire?.profile_version, adult, full_capacity: adult, answers }); setAssessment(result);
          })}>提交本人答案</button>
        </details>
        {assessment && !assessment.confirmed && <button disabled={busy} onClick={() => act(async () => { await w.post(`/assessments/${assessment.assessment_id}/confirm`, {}); })}>本人确认上述评估结果</button>}
        <details><summary>虚构财务画像与外部资产（修改后需重新评估）</summary>
          <label>月收入（元）<input value={income} onChange={e => setIncome(e.target.value)} /></label><label>月支出（元）<input value={expenses} onChange={e => setExpenses(e.target.value)} /></label><label>负债（元）<input value={debt} onChange={e => setDebt(e.target.value)} /></label>
          <label>外部模拟资产名称<input value={externalName} onChange={e => setExternalName(e.target.value)} /></label><label>模拟估值（元）<input value={externalValue} onChange={e => setExternalValue(e.target.value)} /></label>
          <label>资产类别<select value={externalCategory} onChange={e => setExternalCategory(e.target.value)}>{['cash', 'bond', 'mixed', 'equity', 'index', 'other'].map(category => <option key={category}>{category}</option>)}</select></label>
          <button disabled={busy} onClick={() => act(async () => { await w.put('/profile', { version: profile?.version,
            monthly_income_fen: w.fen(income), monthly_expenses_fen: w.fen(expenses), liabilities_fen: w.fen(debt),
            external_assets: [...(profile?.data.external_assets || []), ...(externalName ? [{ asset_id: id(), label: externalName, category: externalCategory, amount_fen: w.fen(externalValue), currency: 'CNY' }] : [])] }); setExternalName(''); })}>保存模拟画像</button>
          <p>用户手填来源 · 更新 {profile?.data.updated_at || '尚未保存'}</p>
          {profile?.data.external_assets?.map(asset => <p key={asset.asset_id}>{asset.label} · {w.money(asset.amount_fen)} · 仅用于诊断，不可交易 <button className="secondary" disabled={busy} onClick={() => act(async () => { await w.put('/profile', { version: profile?.version, monthly_income_fen: w.fen(income), monthly_expenses_fen: w.fen(expenses), liabilities_fen: w.fen(debt), external_assets: profile.data.external_assets?.filter(item => item.asset_id !== asset.asset_id) }); })}>移除这笔外部资产</button></p>)}
        </details>
        <h3>目标</h3>{profile?.goals.map(goal => <p key={goal.goal_id}>{goal.title} · {w.money(goal.amount_fen)} · {goal.due_date} · 已标记 {w.money(goal.allocations.reduce((sum, allocation) => sum + allocation.amount_fen, 0))} <button className="secondary" onClick={() => { setEditingGoal(goal); setGoalName(goal.title); setGoalAmount(String(goal.amount_fen / 100)); setGoalDate(goal.due_date); setGoalLiquidity(goal.liquidity); setGoalPriority(String(goal.priority)); setGoalSource(''); setGoalAssigned('0'); }}>编辑目标</button></p>)}
        <label>虚构目标名称<input value={goalName} onChange={e => setGoalName(e.target.value)} /></label><label>目标金额（元）<input value={goalAmount} onChange={e => setGoalAmount(e.target.value)} /></label><label>到期日期<input type="date" value={goalDate} onChange={e => setGoalDate(e.target.value)} /></label>
        <label>流动性<select value={goalLiquidity} onChange={e => setGoalLiquidity(e.target.value)}><option value="immediate">近期随时使用</option><option value="flexible">可等待到账</option><option value="long_term">长期用途</option></select></label><label>优先级（1最高）<select value={goalPriority} onChange={e => setGoalPriority(e.target.value)}>{[1, 2, 3, 4, 5].map(level => <option key={level}>{level}</option>)}</select></label>
        <label>规划标签来源（不冻结资金）<select value={goalSource} onChange={e => setGoalSource(e.target.value)}><option value="">不分配／保留已有标签</option>{assetSources.map(source => <option key={source.ref} value={source.ref}>{source.label}</option>)}</select></label><label>本来源分配金额（元）<input value={goalAssigned} onChange={e => setGoalAssigned(e.target.value)} /></label>
        {editingGoal?.allocations.map(allocation => <p key={allocation.asset_ref}>{allocation.asset_ref.split(':')[0]} · {w.money(allocation.amount_fen)} <button className="secondary" onClick={() => setEditingGoal({ ...editingGoal, allocations: editingGoal.allocations.filter(item => item.asset_ref !== allocation.asset_ref) })}>移除标签（保存后生效）</button></p>)}
        <button disabled={busy || !goalName} onClick={() => act(async () => { const allocations = (editingGoal?.allocations || []).filter(item => item.asset_ref !== goalSource); if (goalSource && w.fen(goalAssigned) > 0) allocations.push({ asset_ref: goalSource, amount_fen: w.fen(goalAssigned) }); await w.post('/goals', { goal_id: editingGoal?.goal_id || null, version: editingGoal?.version || 1, title: goalName, amount_fen: w.fen(goalAmount), due_date: goalDate, liquidity: goalLiquidity, priority: Number(goalPriority), allocations }); setGoalName(''); setEditingGoal(null); })}>{editingGoal ? '保存目标修改' : '添加目标'}</button><button className="secondary" onClick={() => { setEditingGoal(null); setGoalName(''); setGoalSource(''); }}>清空目标表单</button>
      </>}
      {tab === 'products' && <>
        <h3>{products.length} 个当前上下文模拟产品</h3>{products.map(p => <article key={p.product_id} className="wealth-product"><label className="wealth-check"><input type="checkbox" checked={selected.includes(p.product_id)} onChange={e => setSelected(e.target.checked ? [...selected, p.product_id].slice(-3) : selected.filter(value => value !== p.product_id))} />{p.name} · {p.product_id}</label><p>产品风险 R{p.product_risk_level} · 净值 {p.nav ?? '未知'}（{p.nav_date ?? '未知'}） · 最低持有 {p.rules.holding_days} 天</p><details><summary>查看规则与披露</summary>{Object.entries(p.disclosure).map(([key, value]) => <p key={key}>{disclosureLabels[key] || key}：{displayDisclosure(value)}</p>)}</details></article>)}
        <button disabled={busy || selected.length < 2} onClick={() => act(async () => { const rows = await w.get<w.Product[]>(`/compare?product_ids=${selected.join(',')}`); setChat({ mode: 'offline', kind: 'comparison', products: rows, message: '按服务端产品规则客观比较' }); })}>对比所选产品</button>
        <h3>单产品预览</h3><label>方向<select value={direction} onChange={e => setDirection(e.target.value as typeof direction)}><option value="purchase">申购</option><option value="redeem">赎回（正式份额）</option></select></label>
        <label>产品<select value={product} onChange={e => setProduct(e.target.value)}>{products.map(p => <option key={p.product_id} value={p.product_id}>{p.name}</option>)}</select></label><label>本人付款／到账账户<select value={account} onChange={e => setAccount(e.target.value)}>{accountOptions}</select></label>
        {direction === 'purchase' ? <label>总申购金额（元，含费用）<input value={amount} onChange={e => setAmount(e.target.value)} /></label> : <label>赎回份额（最多4位小数）<input value={units} onChange={e => setUnits(e.target.value)} /></label>}
        <button disabled={busy || !chosen} onClick={() => act(async () => { if (chosen) await showPreview({ operation_id: id(), direction,
          product_id: product, product_version: chosen.version, disclosure_version: chosen.disclosure.version, account_id: account,
          amount_fen: direction === 'purchase' ? w.fen(amount) : 0, units: direction === 'redeem' ? units : '0.0000' }); })}>生成估算预览（不扣款）</button>
      </>}
      {tab === 'plans' && <>
        <h3>新资金配置／现有持仓调整</h3><label>方案类型<select value={kind} onChange={e => setKind(e.target.value as typeof kind)}><option value="new_cash">新资金配置</option><option value="adjustment">现有持仓诊断与调整</option></select></label>
        <label>资金账户<select value={account} onChange={e => setAccount(e.target.value)}>{accountOptions}</select></label><label>本次现金预算（元）<input value={budget} onChange={e => setBudget(e.target.value)} /></label><label>预算内保留现金（元）<input value={reserve} onChange={e => setReserve(e.target.value)} /></label><label>预计使用期限（天）<input value={horizon} onChange={e => setHorizon(e.target.value)} /></label>
        <details><summary>排除不采用的产品</summary>{products.map(p => <label key={p.product_id} className="wealth-check"><input type="checkbox" checked={excluded.includes(p.product_id)} onChange={e => setExcluded(e.target.checked ? [...excluded, p.product_id] : excluded.filter(value => value !== p.product_id))} />{p.name}</label>)}</details>
        <button disabled={busy} onClick={() => act(async () => { setPlan(await w.post<w.Plan>('/plans', planRequest())); })}>生成新方案</button>
        {plan && <><button className="secondary" disabled={busy || !['AWAITING_CONFIRMATION', 'PAUSED'].includes(plan.status)} onClick={() => act(async () => { setPlan(await w.put<w.Plan>(`/plans/${plan.plan_id}`, planRequest())); setPreview(null); })}>按当前表单修改方案（旧确认失效）</button><h3>方案 v{plan.version} · {statusLabel[plan.status] || plan.status}</h3><p>保留现金 {w.money(plan.configuration.retained_cash_fen)} · 拟投入 {w.money(plan.configuration.investment_fen)}</p><p>{plan.next_action}</p>{plan.history?.map(stage => <section key={stage.version}><h4>原方案 v{stage.version} 的成功事实</h4>{stage.items.map(item => <p key={item.command.operation_id}>{item.command.product_id} · {statusLabel[item.status] || item.status} · {item.command.direction === 'redeem' ? `实际到账 ${w.money(item.bank_result?.details.actual?.cash_fen)}` : `实际份额 ${item.bank_result?.details.actual?.units || '等待确认'}`} · 原号 {item.command.operation_id}</p>)}</section>)}
          {plan.diagnostic && <><p>{plan.diagnostic.note}</p><p>近期目标预算缺口 {w.money(plan.diagnostic.goal_shortfall_fen)}（不代表目标已达成）</p><div className="wealth-table"><table><thead><tr><th>类别</th><th>当前金额</th><th>模拟目标</th><th>差额</th></tr></thead><tbody>{(plan.diagnostic.allocation || []).map(item => <tr key={item.category}><td>{item.category}</td><td>{w.money(item.current_fen)}</td><td>{item.target_percent}% · {w.money(item.target_fen)}</td><td>{w.money(item.difference_fen)}</td></tr>)}</tbody></table></div></>}
          {plan.items.map(item => <article className="wealth-product" key={item.command.operation_id}><p>{item.command.product_id} · {item.command.direction === 'purchase' ? `申购 ${w.money(item.command.amount_fen)}` : `赎回 ${item.command.units} 份`} · {statusLabel[item.status] || item.status}</p><p>{item.reason}</p>{item.status === 'AWAITING_CONFIRMATION' && <><label className="wealth-check"><input type="checkbox" checked={adopted.includes(item.command.operation_id)} onChange={e => setAdopted(e.target.checked ? [...adopted, item.command.operation_id] : adopted.filter(value => value !== item.command.operation_id))} />采用这笔</label><label>{item.command.direction === 'purchase' ? '采用金额（元，可减少）' : '采用份额（可减少）'}<input value={childAmounts[item.command.operation_id] || ''} onChange={e => setChildAmounts({ ...childAmounts, [item.command.operation_id]: e.target.value })} /></label></>}<button disabled={busy || item.status !== 'AWAITING_CONFIRMATION'} onClick={() => act(() => showPreview(item.command))}>查看单笔详情（可选）</button></article>)}
          <button disabled={busy || plan.status !== 'AWAITING_CONFIRMATION'} onClick={() => act(previewAdopted)}>预览采用方案（统一确认）</button>
          <button className="secondary" disabled={busy} onClick={() => act(async () => { setPlan(await w.get<w.Plan>(`/plans/${plan.plan_id}/progress`)); })}>核对逐笔进度</button></>}
        <h3>已保存方案</h3>{plans.map(item => <button key={item.plan_id} className="secondary" onClick={() => { setPlan(item); setBudget(String(item.request.budget_fen / 100)); setReserve(String(item.request.reserve_fen / 100)); setHorizon(String(item.request.horizon_days)); setKind(item.request.kind); setExcluded(item.request.excluded_products); }}>{item.plan_id.slice(-8)} · v{item.version} · {statusLabel[item.status] || item.status}</button>)}
      </>}
      {tab === 'orders' && <>
        <h3>持仓批次（不含外部模拟资产）</h3>{assets?.lots.length === 0 && <p>尚无结算持仓，受理后等待模拟结算。</p>}{assets?.lots.map(lot => <article key={lot.lot_id} className="wealth-product"><p>{lot.product_name} · {lot.units} 份 · 可赎回 {lot.redeemable_units} · 锁定 {lot.locked_units} · 占用 {lot.held_units}</p><p>成本 {w.money(lot.cost_fen)} · 估值 {w.money(lot.market_value_fen)} · 浮动盈亏 {w.money(lot.unrealized_pnl_fen)}（{lot.nav_date}）</p><button disabled={busy} onClick={() => { setProduct(lot.product_id); setAccount(lot.account_id); setDirection('redeem'); setUnits(lot.units); setTab('products'); }}>按全部持有份额预览赎回</button></article>)}
        <h3>订单</h3>{assets?.orders.map(order => <article key={order.operation_id} className="wealth-product"><p>{order.version_id} · {order.direction} · {statusLabel[order.status] || order.status}</p><p>计价 {order.pricing_date} · 确认 {order.confirm_date}{order.direction === 'redeem' ? ` · 赎回到账 ${order.cash_date}` : ''}</p><p>实际净值 {order.details.actual_nav || '未确认'} · 实际费用 {w.money(order.details.actual?.fee_fen)} · {order.direction === 'redeem' ? `实际到账 ${w.money(order.details.actual?.cash_fen)}` : `实际份额 ${order.details.actual?.units || '等待确认'}`}</p>{order.details.verification?.simulated && <p className="notice">模拟授权订单 · 未进行真实身份验证</p>}<p className="hint">原操作号 {order.operation_id}</p>
          <button className="secondary" disabled={busy} onClick={() => act(async () => { const fact = await w.get<w.Order>(`/orders/${order.operation_id}`); setNotice(statusLabel[fact.status] || fact.status); })}>原号核对</button>
          {['ACCEPTED', 'PENDING_PRICING'].includes(order.status) && <button className="secondary" disabled={busy} onClick={() => { if (window.confirm('确认撤销这笔模拟订单？已计价或超过窗口将拒绝。')) act(async () => { await w.post(`/orders/${order.operation_id}/cancel`, { digest: order.digest, disclosure_version: Number(order.version_id.split(':')[1]), accepted: true }); onActivity(); }); }}>明确撤单</button>}</article>)}
        <h3>提交观察与原号恢复</h3>{executions.filter(item => ['UNKNOWN', 'SUBMITTING'].includes(item.status)).map(item => <article className="wealth-product" key={item.operation_id}><p>{statusLabel[item.status]} · {item.operation_id}</p><button disabled={busy} onClick={() => act(async () => { const fact = await w.post<w.Order>(`/previews/${item.authorization_id}/execute`, {}); setNotice(statusLabel[fact.status] || fact.status); onActivity(); })}>核对并恢复原号（同号同参）</button></article>)}
        <h3>刷新后可恢复的预览</h3>{pending.map(item => <button key={item.authorization_id} className="secondary" disabled={busy} onClick={() => act(() => showPreview(item.command))}>{item.command.product_id} · {item.status} · 重新读取预览</button>)}
      </>}
    </div><aside><h3>理财助手</h3><p className="hint">对话模式：{chat?.mode === 'model' ? '真实模型理解' : chat?.mode === 'offline' ? '离线规则（有限表达）' : '随首次回复显示'}。例如：我有两万元，三个月可能要用，留五千备用。只生成待确认方案。</p>
      <label>需求<input value={chatInput} maxLength={500} onChange={e => setChatInput(e.target.value)} /></label><button disabled={busy || !chatInput.trim()} onClick={() => act(() => send(chatInput))}>发送需求</button><button className="secondary" disabled={busy || !session} onClick={() => act(() => send('继续'))}>评估后继续原需求</button>
      {chat?.citations?.map(c => <details key={c.document_id}><summary>{c.title} · {c.source_type} · {c.published_at || '发布时间未核实'}</summary><p>{c.excerpt}</p>{c.source_url && <a href={c.source_url} target="_blank" rel="noreferrer">官方原始来源</a>}<small>版本 {c.version} · 摘要指纹 {c.content_hash.slice(0,12)}</small></details>)}
      {chat && <><p>{chat.message}</p>{chat.products && <div className="wealth-table"><table><thead><tr><th>产品</th><th>风险</th><th>申购费率</th><th>持有期</th><th>赎回到账</th></tr></thead><tbody>{chat.products.map(p => <tr key={p.product_id}><td>{p.name}</td><td>R{p.product_risk_level}</td><td>{p.rules.purchase_fee_rate}</td><td>{p.rules.holding_days}天</td><td>{p.rules.cash_business_days}营业日</td></tr>)}</tbody></table></div>}</>}
      <p className="hint">对话不接收验证码，不会因“好的”而交易；页面确认后仍由银行再次检查。</p></aside></div>
    {preview && <div className="wealth-modal"><section role="dialog" aria-label="本人订单确认" className="card"><h3>比赛模拟环境／模拟资金 · 本人确认</h3><p>{preview.product_name} · {preview.command.direction === 'purchase' ? `申购总支出 ${w.money(preview.command.amount_fen)}` : `赎回 ${preview.command.units} 份`}</p><p>估算净值 {preview.estimated_nav || '未知'}（{preview.estimated_nav_date || '未知'}）· 估算费用 {w.money(preview.estimate?.fee_fen)} · 估算份额 {preview.estimate?.units || '不适用'} · 估算到账 {w.money(preview.estimate?.cash_fen)}</p><p>计价日 {preview.pricing_date} · 确认日 {preview.confirm_date} · 到账日 {preview.cash_date}</p><details open><summary>产品披露版本 {preview.command.disclosure_version}</summary>{Object.entries(preview.disclosure).map(([key, value]) => <p key={key}>{disclosureLabels[key] || key}：{displayDisclosure(value)}</p>)}</details><p>实际份额/到账以结算为准，预览不保证收益。状态：{preview.authorization_status}，确认有效至 {new Date(preview.expires_at).toLocaleTimeString()}</p>
      <label className="wealth-check"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />本人已阅读本笔披露，理解本金可能亏损、费用和流动性，同意上述订单</label>
      <p>资金账户 {accounts.find(item => item.account_id === preview.command.account_id)?.card_number || '本人模拟账户'} · 产品风险 R{preview.product_risk_level} · 操作风险：高风险（RED）</p><p>{preview.suitability ? `本人承受能力 R${preview.suitability.capacity_level}，评估有效至 ${preview.suitability.expires_at.slice(0, 10)}，服务端匹配通过` : '赎回既有持仓不要求重新取得高风险产品申购资格'}</p>
      {preview.authorization_status === 'AWAITING_CONFIRMATION' && <button disabled={busy || !consent} onClick={() => act(async () => { const result = await w.post<{ status: string }>(`/previews/${preview.authorization_id}/confirm`, { digest: preview.digest, disclosure_version: preview.command.disclosure_version, accepted: true }); setPreview({ ...preview, authorization_status: result.status }); })}>确认这笔订单</button>}
      {preview.authorization_status === 'AWAITING_MFA' && (verificationMode?.simulated ? <><p>高风险授权（模拟支付密码）：无需输入任何密码；点击仅记录本次订单的模拟授权。</p><button disabled={busy} onClick={() => act(async () => { await w.post(`/previews/${preview.authorization_id}/verify/demo`, {}); if (preview.command.plan_id) setPreview({ ...preview, authorization_status: 'VALID' }); else { const fact = await w.post<w.Order>(`/previews/${preview.authorization_id}/execute`, {}); setPreview(null); setNotice(`模拟验证 · ${statusLabel[fact.status] || fact.status}`); onActivity(); } })}>通过支付密码形式授权（模拟）并提交</button></> : <><label>验证器6位验证码（只进入安全验证）<input inputMode="numeric" autoComplete="off" value={code} maxLength={6} onChange={e => setCode(e.target.value.replace(/\D/g, ''))} /></label><p className="hint">每笔使用新的验证码；已使用时请等待验证器更新。</p><button disabled={busy || code.length !== 6} onClick={() => act(async () => { const result = await w.post<{ status: string }>(`/previews/${preview.authorization_id}/verify`, { proof: code }); setCode(''); setPreview({ ...preview, authorization_status: result.status }); })}>强验证本笔订单</button></>)}
      {preview.authorization_status === 'VALID' && (preview.command.plan_id ? <p>本笔已验证。关闭后完成其他子单验证，再受理方案。</p> : <button disabled={busy} onClick={() => act(async () => { const fact = await w.post<w.Order>(`/previews/${preview.authorization_id}/execute`, {}); setPreview(null); setNotice(statusLabel[fact.status] || fact.status); onActivity(); })}>受理这笔模拟订单</button>)}
      <button className="secondary" disabled={busy} onClick={() => { setPreview(null); setCode(''); }}>关闭</button></section></div>}
    {groupPreview && !preview && <div className="wealth-modal"><section role="dialog" aria-label="整份方案预览与确认" className="card">
      <h3>整份采用方案 · v{groupPreview.version}</h3><p>申购总支出 {w.money(groupPreview.items.reduce((sum, item) => sum + item.command.amount_fen, 0))} · 申购保留现金 {w.money(plan?.configuration.retained_cash_fen)} · 共 {groupPreview.items.length} 笔</p>
      <p>每笔金额、费用和到账均由银行预检；预计赎回款不能提前买入，实际净值和成交以结算为准。</p>
      {groupPreview.items.map(item => <article key={item.authorization_id} className="wealth-product"><h4>{item.product_name} · {item.command.direction === 'purchase' ? `申购 ${w.money(item.command.amount_fen)}` : `赎回 ${item.command.units} 份`}</h4>
        <p>付款账户 {accounts.find(a => a.account_id === item.command.account_id)?.card_number} · 产品风险 R{item.product_risk_level} · 操作风险：高风险（RED）</p>
        <p>{item.suitability ? `本人承受能力 R${item.suitability.capacity_level} · 评估有效至 ${item.suitability.expires_at.slice(0, 10)}` : '赎回既有持仓'} · 服务端预检通过</p>
        <p>估算净值 {item.estimated_nav}（{item.estimated_nav_date}） · 估算费用 {w.money(item.estimate?.fee_fen)} · {item.command.direction === 'purchase' ? `估算份额 ${item.estimate?.units}` : `估算到账 ${w.money(item.estimate?.cash_fen)}`}</p>
        <p>计价 {item.pricing_date} · 确认 {item.confirm_date} · 到账 {item.cash_date} · 确认有效至 {new Date(item.expires_at).toLocaleTimeString()}</p>
        <details><summary>查看本笔完整披露 v{item.command.disclosure_version}</summary>{Object.entries(item.disclosure).map(([key, value]) => <p key={key}>{disclosureLabels[key] || key}：{displayDisclosure(value)}</p>)}</details>
        {!verificationMode?.simulated && groupStage !== 'PREVIEW' && <button disabled={busy} onClick={() => act(() => showPreview(item.command))}>真实验证这笔订单</button>}
      </article>)}
      {groupStage === 'PREVIEW' && <><label className="wealth-check"><input type="checkbox" checked={groupConsent} onChange={e => setGroupConsent(e.target.checked)} />本人已阅读以上所有选中订单及逐笔披露，理解本金亏损、费用和流动性，确认采用整份方案</label>
        <button disabled={busy || !groupConsent} onClick={() => act(async () => { await w.post(`/plans/${groupPreview.plan_id}/confirm`, { version: groupPreview.version, items: groupPreview.items.map(item => ({ authorization_id: item.authorization_id, digest: item.digest, disclosure_version: item.command.disclosure_version, accepted: true })) }); setGroupStage('CONFIRMED'); })}>一次确认整份方案</button></>}
      {groupStage === 'CONFIRMED' && verificationMode?.simulated && <><p>高风险授权（模拟支付密码）：无需输入密码；本次授权绑定以上全部选中订单，数据库逐笔保留模拟记录。</p><button disabled={busy} onClick={() => act(async () => { await w.post(`/plans/${groupPreview.plan_id}/verify/demo`, { version: groupPreview.version }); setGroupStage('VERIFIED'); const current = await w.post<w.Plan>(`/plans/${groupPreview.plan_id}/execute`, { version: groupPreview.version }); setPlan(current); setGroupPreview(null); setNotice(`模拟验证 · ${statusLabel[current.status] || current.status}`); onActivity(); })}>通过支付密码形式授权（模拟）并提交方案</button></>}
      {(groupStage === 'VERIFIED' || (groupStage === 'CONFIRMED' && !verificationMode?.simulated)) && <button disabled={busy} onClick={() => act(async () => { setPlan(await w.post<w.Plan>(`/plans/${groupPreview.plan_id}/execute`, { version: groupPreview.version })); setGroupPreview(null); onActivity(); })}>受理／恢复整份方案（原号核对）</button>}
      <button className="secondary" disabled={busy} onClick={() => setGroupPreview(null)}>关闭预览</button>
    </section></div>}
  </section>;
}
