import { beijingTime } from '../../utils/time';
import { useEffect, useState, useRef } from 'react';
import * as w from '../../services/wealth';
import type { Account, SecondFactor as Factor } from '../../services/bank';
import { SecondFactor } from '../SecondFactor';
import './styles.css';
import Research from './Research';
import OperatorPanel from './OperatorPanel';
import ProductDetail from './ProductDetail';
import Recommendation from './Recommendation';

interface Props { accounts: Account[]; revision: number; onActivity: () => void; userId: string }
type Tab = 'workbench' | 'assets';
const id = () => `WO${crypto.randomUUID().replace(/-/g, '')}`;
const statusLabel: Record<string, string> = { ACCEPTED: '已受理／资金或份额占用', PENDING_PRICING: '等待计价',
  PENDING_CASH: '等待赎回到账', SETTLED: '已结算', CANCELLED: '已撤单', UNKNOWN: '结果未知，须原号核对',
  AWAITING_CONFIRMATION: '等待本人确认', WAITING_SETTLEMENT: '等待结算', COMPLETED: '完成', PAUSED: '已暂停',
  PARTIALLY_COMPLETED: '部分完成', PARTIAL_COMPLETED: '部分完成', WAITING: '等待下一步', RECONCILING: '核对已提交结果', FAILED: '失败，需处理', EXPIRED: '已失效', EXECUTING: '执行中', SUBMITTING: '正在提交' };
const disclosureLabels: Record<string, string> = { version: '披露版本', nature: '产品性质', manager: '模拟管理机构', seller: '展示与销售角色', risk: '风险说明', fees: '费用', liquidity: '流动性', holding_period_days: '最低持有天数', pricing: '计价规则', settlement: '结算与到账', cancellation: '撤单窗口', limitations: '模拟范围' };
const displayDisclosure = (value: unknown): string => value == null ? '未提供' : Array.isArray(value) ? value.map(displayDisclosure).join('；') : typeof value === 'object' ? Object.entries(value).map(([key, item]) => `${key}：${displayDisclosure(item)}`).join('；') : String(value);
const errorText = (error: unknown) => {
  const message = (error as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message;
  return message || (error instanceof Error ? error.message : '请求失败，请保留原操作号核对');
};

function RedemptionQuote({preview}:{preview:w.Preview}) {
  if(preview.command.direction!=='redeem')return null;
  if(preview.command.redemption_mode!=='quick')return <p>普通赎回：按合同等待计价、确认和到账；预计款不能用于买入。</p>;
  const quote=preview.estimate;
  return <section aria-label="快赎银行绑定报价"><strong>模拟快赎 · 同事务立即到账 · 成功后不可撤</strong>
    <p>本次绑定到账 {w.money(preview.command.quoted_cash_fen)} · 费用 {w.money(quote?.fee_fen)} · 净值可用时间 {quote?.nav_available_at?beijingTime(quote.nav_available_at):'未知，需重新预览'}</p>
    <p>北京时间窗口 {quote?.contract?`${quote.contract.start}—${quote.contract.end}（结束不含端点）`:'待银行核验'}；{quote?.contract?.window==='trading_day'?'仅模拟交易日':'自然日，维护窗口除外'}。</p>
    <p>本自然日已支付 {w.money(quote?.daily_used_fen)} · 受理前剩余额度 {w.money(quote?.daily_remaining_fen)}，同轮次所有账户共享。银行受理时重新核验窗口、净值与额度；不自动切换普通赎回。</p></section>;
}

export default function Wealth({ accounts, revision, onActivity, userId }: Props) {
  const [detailProduct,setDetailProduct]=useState<string|null>(null);
  const [tab, setTab] = useState<Tab>('workbench');
  const [history, setHistory] = useState<{user: string; reply: w.ChatReply}[]>([]);
  const [tasks, setTasks] = useState<w.WealthTask[]>([]);
  const [redeeming,setRedeeming]=useState<{product_id:string;account_id:string;name:string}|null>(null);
  const [redemptionMode,setRedemptionMode]=useState<'standard'|'quick'>('standard');
  const [units,setUnits]=useState('');
  const [products, setProducts] = useState<w.Product[]>([]);
  const [assets, setAssets] = useState<w.Assets | null>(null);
  const [assessment, setAssessment] = useState<w.Assessment | null>(null);
  const [questionnaire, setQuestionnaire] = useState<w.Questionnaire | null>(null);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [adult, setAdult] = useState(false);
  const [plan, setPlan] = useState<w.Plan | null>(null);
  const [preview, setPreview] = useState<w.Preview | null>(null); const [consent, setConsent] = useState(false); const [code, setCode] = useState('');
  const [executions, setExecutions] = useState<{ authorization_id: string; operation_id: string; status: string }[]>([]);
  const [factor, setFactor] = useState<Factor | null>({method:'totp'});
  const [groupPreview, setGroupPreview] = useState<w.PlanPreview | null>(null);
  const [groupConsent, setGroupConsent] = useState(false);
  const [groupStage, setGroupStage] = useState<'PREVIEW' | 'CONFIRMED' | 'VERIFIED'>('PREVIEW');
  const [busy, setBusy] = useState(false); const [notice, setNotice] = useState('');
  const [chatInput, setChatInput] = useState(''); const [chat, setChat] = useState<w.ChatReply | null>(null);
  const [session, setSession] = useState<{ session_id: string; version: number } | null>(null);

  const sessionRef=useRef<{session_id:string;version:number}|null>(null);
  const load=async()=>{
    const [p,a,r,q,e,t]=await Promise.all([w.get<w.Product[]>('/products'),w.get<w.Assets>('/assets'),w.get<w.Assessment|null>('/assessments/current'),w.get<w.Questionnaire>('/questionnaire'),w.get<typeof executions>('/executions'),w.get<{items:w.WealthTask[]}>('/tasks')]);
    setProducts(p);setAssets(a);setAssessment(r);setQuestionnaire(q);setExecutions(e);setTasks(t.items);
  };
  const newConversation=async()=>{
    setHistory([]);setChat(null);setPlan(null);setPreview(null);setGroupPreview(null);setConsent(false);setGroupConsent(false);setChatInput('');setSession(null);setRedeeming(null);setDetailProduct(null);setNotice('');
    localStorage.removeItem(`wealth-message-${userId}`);
    const next=await w.post<{session_id:string;version:number}>('/sessions',{});sessionRef.current=next;setSession(next);
  };
  useEffect(()=>{let live=true;let owned:string|undefined;
    setHistory([]);setChat(null);setPlan(null);setPreview(null);setGroupPreview(null);setConsent(false);setGroupConsent(false);setRedeeming(null);setDetailProduct(null);
    localStorage.removeItem(`wealth-message-${userId}`);
    void w.post<{session_id:string;version:number}>('/sessions',{}).then(next=>{owned=next.session_id;if(live){sessionRef.current=next;setSession(next);}else void w.post(`/sessions/${owned}/close`,{});}).catch(e=>{if(live)setNotice(errorText(e));});
    return ()=>{live=false;const closing=sessionRef.current?.session_id||owned;if(closing)void w.post(`/sessions/${closing}/close`,{}).catch(()=>{});sessionRef.current=null;};
  },[userId]);
  useEffect(()=>{void load().catch(e=>setNotice(errorText(e)));},[revision,userId]);
  useEffect(()=>{const timer=window.setInterval(()=>void load().catch(()=>{}),5000);return()=>window.clearInterval(timer);},[userId]);
  const act = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setNotice('');
    try { await action(); await load(); } catch (error) { setNotice(errorText(error)); } finally { setBusy(false); }
  };
  const showPreview = async (command: w.Command) => {
    const view = await w.post<w.Preview>('/previews', command);
    if(sessionRef.current)await w.post(`/sessions/${sessionRef.current.session_id}/preview-link`,{authorization_id:view.authorization_id});
    setPreview(view); setConsent(false); setCode('');setFactor({method:'totp'});
  };
  const previewAdopted = async () => {
    if (!plan) return;
    if (!plan.items.length) { setNotice('方案全部保留现金，无需交易确认。'); return; }
    const views = await w.post<w.PlanPreview>(`/plans/${plan.plan_id}/preview`, { version: plan.version });
    setGroupPreview(views); setGroupConsent(false); setGroupStage('PREVIEW');setCode('');setFactor({method:'totp'});
  };
  const send = async (text: string) => {
    if(!sessionRef.current)throw new Error('会话正在初始化');
    const body={message:text,...sessionRef.current,client_message_id:crypto.randomUUID()};
    const reply=await w.post<w.ChatReply>('/messages',body);
    setHistory(h=>[...h,{user:text,reply}]);
    setChat(reply); setChatInput('');
    if (reply.plan || reply.command) {setPreview(null);setGroupPreview(null);setConsent(false);setGroupConsent(false);}
    if (reply.kind === 'assets') setTab('assets');
    else if (reply.kind === 'assessment_required') setTab('assets');
    if(reply.session_id&&reply.version){const next={session_id:reply.session_id,version:reply.version};sessionRef.current=next;setSession(next);}
    if(reply.plan || (reply.requirement_card && reply.requirement_card.version!==chat?.requirement_card?.version)) {
      setPlan(reply.plan||null);
      setPreview(null);setGroupPreview(null);setConsent(false);setGroupConsent(false);
    }
    if (reply.command && reply.task?.status !== 'PAUSED') await showPreview(reply.command);
  };
  const chosen=products.find(p=>p.product_id===redeeming?.product_id);
  return <section className="card wealth" aria-label="理财模拟">
      <OperatorPanel />
    <div className="wealth-title"><div><h2>理财模拟</h2><p>比赛模拟环境／模拟资金 · 请仅输入虚构数据 · 本金可能亏损</p></div><button className="secondary" disabled={busy} onClick={() => act(async () => { onActivity(); })}>刷新银行事实</button></div>
    <section aria-label="当前银行事实" className="notice">
      <strong>当前银行事实 · {assets?.scope === 'ordinary' ? '普通账户／现实业务时间' : assets?.scope === 'disposable_simulation' ? '可处置模拟轮次' : '范围待查询'}</strong>
      <p>查询时间（北京时间）：{assets?.as_of ? beijingTime(assets.as_of) : '尚未查询'} · 业务时间：{assets?.business_at ? beijingTime(assets.business_at) : '未知'}</p>
      {assets?.cash.map(account => <p key={account.account_id}>账户 {account.account_id} · 账面 {w.money(account.balance_fen)} · 占款 {w.money(account.held_fen)} · 可用 {w.money(account.available_fen)}{account.account_type === 'credit' && '（不可投资）'}</p>)}
      <p>在途赎回应收：{assets ? w.money(assets.receivable_fen) : '尚未查询'}（到账前不可用于买入）</p>
    </section>
    <p className="notice">理财申购／赎回属于高风险操作，本人确认后须完成验证器或短信验证码验证。默认短信为模拟发送，不连接运营商。</p>
    <details><summary>产品资料与数据工具</summary><Research onChoose={setDetailProduct} onSwitch={async()=>{await newConversation();await load();onActivity();}}/></details>
    {notice && <p role="alert" className="notice">{notice}</p>}
    <div className="wealth-layout"><div>
      <details open={tab==='assets'}><summary>本人风险评估</summary>
        <h3>本人风险评估</h3><p>{assessment ? `模拟承受能力 R${assessment.capacity_level} · ${assessment.confirmed ? '已确认' : '待确认'} · 有效至 ${assessment.expires_at.slice(0, 10)}` : '尚未评估，仍可查看客观产品资料'}</p>
        <details><summary>填写／重新评估（本人回答，单日最多2次）</summary>
          {questionnaire?.questions.map(question => <label key={question.id}>{question.title}<select value={answers[question.id] ?? ''} onChange={e => setAnswers({ ...answers, [question.id]: Number(e.target.value) })}><option value="" disabled>请选择本人答案</option>{question.options.map((option, index) => <option key={option} value={index}>{option}</option>)}</select></label>)}
          <label className="wealth-check"><input type="checkbox" checked={adult} onChange={e => setAdult(e.target.checked)} />我是成年、完全民事行为能力的模拟客户，以上由本人回答</label>
          <button disabled={busy || !adult || Object.keys(answers).length !== 9} onClick={() => act(async () => {
            const result = await w.post<w.Assessment>('/assessments', { questionnaire_version: questionnaire?.version,
              profile_version: questionnaire?.profile_version, adult, full_capacity: adult, answers }); setAssessment(result);
          })}>提交本人答案</button>
        </details>
        {assessment && !assessment.confirmed && <button disabled={busy} onClick={() => act(async () => { await w.post(`/assessments/${assessment.assessment_id}/confirm`, {}); if(session && chat?.kind==='assessment_required')await send('继续'); })}>本人确认上述评估结果并继续原需求</button>}
      </details>
      <h3>持仓</h3><div className="wealth-table"><table aria-label="银行持仓"><thead><tr><th>产品／状态</th><th>持有／可赎份额</th><th>冻结／在途</th><th>操作</th></tr></thead><tbody>
        {assets?.lots.map(lot=><tr key={lot.lot_id}><td>{lot.product_name}<small>{w.money(lot.market_value_fen)} · 净值 {lot.nav_date||'未知'}</small></td><td>{lot.units}<small>可赎 {lot.redeemable_units}</small></td><td>锁定 {lot.locked_units}<small>占用 {lot.held_units}</small></td><td><button className="secondary" disabled={busy||Number(lot.redeemable_units)<=0} onClick={()=>{setRedeeming({product_id:lot.product_id,account_id:lot.account_id,name:lot.product_name});setUnits('');setRedemptionMode('standard');}}>赎回</button></td></tr>)}
        {assets?.orders.filter(order=>!['SETTLED','CANCELLED','REJECTED'].includes(order.status)).map(order=><tr key={order.operation_id}><td>{products.find(p=>p.product_id===order.version_id.split(':')[0])?.name||order.version_id.split(':')[0]}<small>{order.direction==='purchase'?'申购已提交':'赎回已提交'} · {statusLabel[order.status]||order.status}</small></td><td>{order.direction==='purchase'?'待确认份额':`赎回 ${order.units}`}<small>在途份额不可赎</small></td><td>{order.direction==='purchase'?w.money(order.amount_fen):`到账 ${order.cash_date}`}<small>依产品规则处理</small></td><td>处理中</td></tr>)}
      </tbody></table></div>{!assets?.lots.length&&!assets?.orders.some(o=>!['SETTLED','CANCELLED','REJECTED'].includes(o.status))&&<p>暂无持仓或在途申请。</p>}
        <button className="secondary" disabled={busy} onClick={()=>act(async()=>{await load();setNotice('已核对未完成执行任务；选择续接只读取进度，不使用旧授权。');})}>查询未完成任务</button>
        {tasks.filter(t=>!['COMPLETED','CANCELLED','PARTIAL_COMPLETED','FAILED'].includes(t.status)).map(t=><article className="wealth-product" key={t.task_id}><p>未完成任务 {t.task_id.slice(-8)} · {statusLabel[t.status]||t.status}</p><button disabled={busy||!session} onClick={()=>act(async()=>{const reply=await w.post<w.ChatReply>(`/sessions/${sessionRef.current!.session_id}/continue`,{task_id:t.task_id});setChat(reply);sessionRef.current={session_id:reply.session_id!,version:reply.version!};setSession(sessionRef.current);setHistory(h=>[...h,{user:'查询并续接未完成任务',reply}]);})}>查询并续接此任务</button></article>)}
    </div><aside><h3>理财对话工作台</h3><button className="secondary" disabled={busy} onClick={()=>act(newConversation)}>新对话</button><p className="hint">对话模式：{chat?.mode === 'model' ? '真实模型理解' : chat?.mode === 'offline' ? '离线规则（有限表达）' : '随首次回复显示'}。先说您的需求，助手会查询银行事实；交易仍需本人确认与TOTP／短信验证。</p>
      {plan && <section aria-label="当前方案"><h3>当前方案 v{plan.version} · {statusLabel[plan.status] || plan.status}</h3>
        <Recommendation plan={plan} products={products} onDetail={setDetailProduct}/>
        <button disabled={busy || plan.status !== 'AWAITING_CONFIRMATION' || !plan.items.length} onClick={() => act(previewAdopted)}>{plan.stage==='SELL'?'统一确认当前减持':'一键申购全部'}</button>
      </section>}
      <div className="wealth-history" aria-label="当前对话">{history.map((entry,index) => <article key={`${entry.reply.client_message_id || index}`}><strong>您：</strong>{entry.user}<p><strong>助手：</strong>{entry.reply.message}</p></article>)}</div>
      {tasks.filter(task => task.task_id===chat?.task?.task_id && !['COMPLETED','CANCELLED'].includes(task.status)).map(task => <article className="wealth-product" key={task.task_id}>
        <h4>当前任务 · v{task.version}</h4><p>{statusLabel[task.status] || task.status} · {task.control_mode === 'AGENT' ? '助手处理' : task.control_mode === 'HANDOFF_PENDING' ? '等待人工领取' : `人工处理中（${task.owner_id}）`}</p>

        <p>{task.waiting_reason === 'BANK_SETTLEMENT' ? '等待银行实际结算或到账；预计资金不能用于买入。' : task.waiting_reason === 'PERSONAL_CONFIRMATION' ? '请核对订单并本人确认。' : task.waiting_reason === 'NEW_BUY_PLAN_AND_CONFIRMATION' ? '赎回已到账，需重新生成买入方案并确认授权。' : task.waiting_reason === 'ARCHIVED_RUN' ? '旧测试轮次已归档。' : task.waiting_reason === 'REVALIDATION_REQUIRED' ? '继续前将重新核验银行事实和授权。' : ''}</p>
        {!['PARTIAL_COMPLETED','FAILED'].includes(task.status) && (['pause','resume','rollback','handoff','cancel'] as const).map(action => <button className="secondary" key={action} disabled={busy || task.control_mode === 'HUMAN'} onClick={() => act(async () => {
          if (action === 'cancel' && !window.confirm('取消未提交的任务部分？已受理及完成订单仍保留原号核对，不撤销账务。')) return;
          await w.post(`/tasks/${task.task_id}/${action}`, {request_id:crypto.randomUUID(), expected_version:task.version});
        })}>{{pause:'暂停',resume:'继续任务',rollback:'返回未提交方案',handoff:'申请人工接管',cancel:'取消未提交部分'}[action]}</button>)}
      </article>)}

      <label>需求<input value={chatInput} maxLength={500} onChange={e => setChatInput(e.target.value)} /></label><button disabled={busy || !chatInput.trim()} onClick={() => act(() => send(chatInput))}>发送需求</button><button className="secondary" disabled={busy || !session} onClick={() => act(() => send('继续'))}>评估后继续原需求</button>
      {chat?.citations?.map(c => <details key={c.document_id}><summary>{c.title} · {c.source_type} · {c.published_at || '发布时间未核实'}</summary><p>{c.excerpt}</p>{c.source_url && <a href={c.source_url} target="_blank" rel="noreferrer">官方原始来源</a>}<small>版本 {c.version} · 摘要指纹 {c.content_hash.slice(0,12)}</small></details>)}
      <p className="hint">对话不接收验证码，不会因“好的”而交易；页面确认后仍由银行再次检查。</p></aside></div>
    {detailProduct && <ProductDetail productId={detailProduct} onClose={()=>setDetailProduct(null)}/>}
    {redeeming && <div className="wealth-modal"><section className="card" role="dialog" aria-label="持仓赎回"><h3>{redeeming.name} · 赎回</h3><label>赎回份额<input value={units} onChange={e=>setUnits(e.target.value)}/></label><label>赎回方式<select value={redemptionMode} onChange={e=>setRedemptionMode(e.target.value as 'standard'|'quick')}><option value="standard">普通赎回</option><option value="quick" disabled={!chosen?.rules.quick_redemption}>快赎（成功后不可撤）</option></select></label><button disabled={busy||!units||!chosen} onClick={()=>act(async()=>{await showPreview({operation_id:id(),direction:'redeem',product_id:redeeming.product_id,product_version:chosen!.version,disclosure_version:chosen!.disclosure.version,account_id:redeeming.account_id,amount_fen:0,units,...(redemptionMode==='quick'?{command_version:2,redemption_mode:'quick'} as const:{})});setRedeeming(null);})}>预览并确认赎回</button><button className="secondary" onClick={()=>setRedeeming(null)}>关闭</button></section></div>}
    {preview && <div className="wealth-modal"><section role="dialog" aria-label="本人订单确认" className="card"><h3>比赛模拟环境／模拟资金 · 本人确认</h3><p>{preview.product_name} · {preview.command.direction === 'purchase' ? `申购总支出 ${w.money(preview.command.amount_fen)}` : `赎回 ${preview.command.units} 份`}</p><p>估算净值 {preview.estimated_nav || '未知'}（{preview.estimated_nav_date || '未知'}）· 估算费用 {w.money(preview.estimate?.fee_fen)} · 估算份额 {preview.estimate?.units || '不适用'} · 估算到账 {w.money(preview.estimate?.cash_fen)}</p><p>计价日 {preview.pricing_date} · 确认日 {preview.confirm_date} · 到账日 {preview.cash_date}</p><details open><summary>产品披露版本 {preview.command.disclosure_version}</summary>{Object.entries(preview.disclosure).map(([key, value]) => <p key={key}>{disclosureLabels[key] || key}：{displayDisclosure(value)}</p>)}</details><p>实际份额/到账以结算为准，预览不保证收益。状态：{preview.authorization_status}，确认有效至 {beijingTime(preview.expires_at)}</p>
      <RedemptionQuote preview={preview}/>
      <label className="wealth-check"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />本人已阅读本笔披露，理解本金可能亏损、费用和流动性，同意上述订单</label>
      <p>资金账户 {accounts.find(item => item.account_id === preview.command.account_id)?.card_number || preview.command.account_id} · 产品风险 R{preview.product_risk_level} · 操作风险：高风险（RED）</p><p>{preview.suitability ? `本人承受能力 R${preview.suitability.capacity_level}，评估有效至 ${preview.suitability.expires_at.slice(0, 10)}，服务端匹配通过` : '赎回既有持仓不要求重新取得高风险产品申购资格'}</p>
      {preview.authorization_status === 'AWAITING_CONFIRMATION' && <button disabled={busy || !consent} onClick={() => act(async () => { const result = await w.post<{ status: string }>(`/previews/${preview.authorization_id}/confirm`, { digest: preview.digest, disclosure_version: preview.command.disclosure_version, accepted: true }); setPreview({ ...preview, authorization_status: result.status }); })}>确认这笔订单</button>}
      {preview.authorization_status === 'AWAITING_MFA' && <>
        <SecondFactor key={preview.authorization_id} purpose="wealth" referenceId={preview.authorization_id} factor={factor} onChange={setFactor} disabled={busy}/>
        <label>6位验证码（仅进入安全验证）<input inputMode="numeric" autoComplete="off" value={code} maxLength={6} onChange={e=>setCode(e.target.value.replace(/\D/g,''))}/></label>
        <button disabled={busy || !factor || code.length!==6} onClick={()=>act(async()=>{
          if(!factor)return;
          const result=await w.post<{status:string}>(`/previews/${preview.authorization_id}/verify`,{proof:code,method:factor.method,challenge_id:factor.method==='sms'?factor.challengeId:undefined});
          setCode('');setPreview({...preview,authorization_status:result.status});
          if(!preview.command.plan_id){
            const fact=await w.post<w.Order>(`/previews/${preview.authorization_id}/execute`,{});
            setPreview(null);setNotice(fact.status==='SETTLED'?'赎回成功，余额与持仓已更新。':['ACCEPTED','PENDING_PRICING','PENDING_CASH'].includes(fact.status)?'赎回申请已受理，持仓表展示份额冻结及在途状态。':'赎回未成功：'+(statusLabel[fact.status]||fact.status));onActivity();
          }
        })}>验证并提交</button>
      </>}
      {preview.authorization_status === 'VALID' && (preview.command.plan_id ? <p>本笔已验证。关闭后完成其他子单验证，再受理方案。</p> : <button disabled={busy} onClick={() => act(async () => { const fact = await w.post<w.Order>(`/previews/${preview.authorization_id}/execute`, {}); setPreview(null); setNotice(statusLabel[fact.status] || fact.status); onActivity(); })}>受理这笔模拟订单</button>)}
      <button className="secondary" disabled={busy} onClick={() => { setPreview(null); setCode(''); }}>关闭</button></section></div>}
    {groupPreview && !preview && <div className="wealth-modal"><section role="dialog" aria-label="整份方案预览与确认" className="card">
      <h3>当前方案 · v{groupPreview.version}</h3><p>申购总支出 {w.money(groupPreview.items.reduce((sum, item) => sum + item.command.amount_fen, 0))} · 未分配金额 {w.money(plan?.configuration.retained_cash_fen)} · 共 {groupPreview.items.length} 笔</p>
      <p>每笔金额、费用和到账均由银行预检；预计赎回款不能提前买入，实际净值和成交以结算为准。</p>
      {groupPreview.items.map(item => <article key={item.authorization_id} className="wealth-product"><h4>{item.product_name} · {item.command.direction === 'purchase' ? `申购 ${w.money(item.command.amount_fen)}` : `赎回 ${item.command.units} 份`}</h4>
        <p>付款账户 {accounts.find(a => a.account_id === item.command.account_id)?.card_number || item.command.account_id} · 产品风险 R{item.product_risk_level} · 操作风险：高风险（RED）</p>
        <p>{item.suitability ? `本人承受能力 R${item.suitability.capacity_level} · 评估有效至 ${item.suitability.expires_at.slice(0, 10)}` : '赎回既有持仓'} · 服务端预检通过</p>
        <p>估算净值 {item.estimated_nav}（{item.estimated_nav_date}） · 估算费用 {w.money(item.estimate?.fee_fen)} · {item.command.direction === 'purchase' ? `估算份额 ${item.estimate?.units}` : `估算到账 ${w.money(item.estimate?.cash_fen)}`}</p>
        <RedemptionQuote preview={item}/>
        <p>计价 {item.pricing_date} · 确认 {item.confirm_date} · 到账 {item.cash_date} · 确认有效至 {beijingTime(item.expires_at)}</p>
        <details><summary>查看本笔完整披露 v{item.command.disclosure_version}</summary>{Object.entries(item.disclosure).map(([key, value]) => <p key={key}>{disclosureLabels[key] || key}：{displayDisclosure(value)}</p>)}</details>
      </article>)}
      {groupStage === 'PREVIEW' && <><label className="wealth-check"><input type="checkbox" checked={groupConsent} onChange={e => setGroupConsent(e.target.checked)} />本人已阅读以上所有选中订单及逐笔披露，理解本金亏损、费用和流动性，确认采用整份方案</label>
        <button disabled={busy || !groupConsent} onClick={() => act(async () => { await w.post(`/plans/${groupPreview.plan_id}/confirm`, { version: groupPreview.version, items: groupPreview.items.map(item => ({ authorization_id: item.authorization_id, digest: item.digest, disclosure_version: item.command.disclosure_version, accepted: true })) }); setGroupStage('CONFIRMED'); })}>一次确认整份方案</button></>}
      {groupStage === 'CONFIRMED' && <>
        <SecondFactor key={groupPreview.verification_reference_id} purpose="wealth" referenceId={groupPreview.verification_reference_id} factor={factor} onChange={setFactor} disabled={busy}/>
        <label>6位验证码（验证整份方案）<input inputMode="numeric" autoComplete="off" value={code} maxLength={6} onChange={e=>setCode(e.target.value.replace(/\D/g,''))}/></label>
        <button disabled={busy || !factor || code.length!==6} onClick={()=>act(async()=>{
          if(!factor)return;
          await w.post(`/plans/${groupPreview.plan_id}/verify`,{version:groupPreview.version,proof:code,method:factor.method,challenge_id:factor.method==='sms'?factor.challengeId:undefined});
          setCode('');setGroupStage('VERIFIED');
          const current=await w.post<w.Plan>(`/plans/${groupPreview.plan_id}/execute`,{version:groupPreview.version});setPlan(current);setGroupPreview(null);
          const label=plan?.stage==='SELL'?'赎回':'申购';
          setNotice(current.items.every(i=>['ACCEPTED','PENDING_PRICING','PENDING_CASH','SETTLED'].includes(i.status))?`${label}成功（申请已受理）；持仓表展示当前份额及在途状态。`:`${label}未全部成功，请查看当前任务提示。`);onActivity();
        })}>一次验证并提交整份方案</button>
      </>}
      {groupStage==='VERIFIED' && <button disabled={busy} onClick={()=>act(async()=>{setPlan(await w.post<w.Plan>(`/plans/${groupPreview.plan_id}/execute`,{version:groupPreview.version}));setGroupPreview(null);onActivity();})}>恢复提交（沿用原操作号）</button>}
      <button className="secondary" disabled={busy} onClick={() => setGroupPreview(null)}>关闭预览</button>
    </section></div>}
  </section>;
}
