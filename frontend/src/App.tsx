import { FormEvent, lazy, Suspense, useEffect, useRef, useState } from 'react';
import * as bank from './services/bank';
import BeijingClock from './components/BeijingClock';
import NovaChat from './components/NovaChat';
import { SecondFactor } from './components/SecondFactor';
import { beijingTime, beijingInput, fromBeijingInput } from './utils/time';

const BillAnalysis = lazy(() => import('./components/BillAnalysis'));
const Wealth = lazy(() => import('./components/Wealth'));
const DemoTools = lazy(() => import('./components/Wealth/DemoTools'));
const CardManagement = lazy(() => import('./components/CardManagement'));
const ThirdPartyServices = lazy(() => import('./components/ThirdPartyServices'));
const MerchantDashboard = lazy(() => import('./components/MerchantDashboard'));

const newId = () => crypto.randomUUID?.() ?? `id-${Date.now()}-${Math.random()}`;
const localDateTimeInput = beijingInput;
const suggestedScheduleTime = () => localDateTimeInput(new Date(Date.now() + 2 * 60_000));
type BankingSection = 'home' | 'transfer' | 'insights' | 'cards' | 'subscriptions' | 'wealth';

function App() {
  const [user, setUser] = useState<bank.User | null>(() =>
    localStorage.getItem('token') ? JSON.parse(localStorage.getItem('bank-user') || 'null') : null
  );
  const [loginMode, setLoginMode] = useState<'login' | 'register'>('login');
  const [phone, setPhone] = useState('13800000001');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('password123');
  const [accounts, setAccounts] = useState<bank.Account[]>([]);
  const [recipients, setRecipients] = useState<bank.Recipient[]>([]);
  const [transactions, setTransactions] = useState<bank.Transaction[]>([]);
  const [bankRevision, setBankRevision] = useState(0);
  const [wealthWorkspaceEpoch,setWealthWorkspaceEpoch]=useState(0);
  const [paymentRequests, setPaymentRequests] = useState<bank.PaymentRequest[]>([]);
  const [subscriptions, setSubscriptions] = useState<bank.Subscription[]>([]);
  const [pendingSubscription, setPendingSubscription] = useState<bank.Subscription | null>(null);
  const [pendingChargeSubscription, setPendingChargeSubscription] = useState<bank.Subscription | null>(null);
  const [subscriptionCancellationToken, setSubscriptionCancellationToken] = useState('');
  const [subscriptionChargeId, setSubscriptionChargeId] = useState('');
  const [fromAccount, setFromAccount] = useState('');
  const [toUserId, setToUserId] = useState('');
  const [recipientMode, setRecipientMode] = useState<'name' | 'phone'>('name');
  const [recipientPhone, setRecipientPhone] = useState('');
  const [phoneCandidates, setPhoneCandidates] = useState<bank.PhoneRecipientCandidate[]>([]);
  const [selectedPhoneAccount, setSelectedPhoneAccount] = useState('');
  const [lookedUpPhone, setLookedUpPhone] = useState('');
  const [editingNote, setEditingNote] = useState<string | null>(null);
  const [editNoteCleared, setEditNoteCleared] = useState(false);
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [transferMode, setTransferMode] = useState<'instant' | 'scheduled'>('instant');
  const [scheduledAt, setScheduledAt] = useState('');
  const [requestPayerIds, setRequestPayerIds] = useState<string[]>([]);
  const [requestAccountId, setRequestAccountId] = useState('');
  const [requestAmount, setRequestAmount] = useState('');
  const [requestNote, setRequestNote] = useState('');
  const [splitEqually, setSplitEqually] = useState(false);
  const [status, setStatus] = useState('');
  const [chatInput, setChatInput] = useState('');
  const [chat, setChat] = useState<string[]>([]);
  const [activeTask, setActiveTask] = useState<bank.TaskSnapshot | null>(null);
  const [conversation, setConversation] = useState<bank.ChatReply | null>(null);
  const [isChatting, setIsChatting] = useState(false);
  const [retryChat, setRetryChat] = useState<bank.ChatRequest | null>(null);
  const [appView, setAppView] = useState<'banking' | 'thirdParty'>('banking');
  const [bankingSection, setBankingSection] = useState<BankingSection>('home');
  const [wealthTestMode, setWealthTestMode] = useState(false);
  const [agentOpen, setAgentOpen] = useState(false);
  const [highlightedPaymentRequest, setHighlightedPaymentRequest] = useState('');

  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [isActing, setIsActing] = useState(false);
  const [totpEnabled, setTotpEnabled] = useState(false);
  const [mfaOpen, setMfaOpen] = useState(false);
  const [mfaCode, setMfaCode] = useState('');
  const [mfaFactor, setMfaFactor] = useState<bank.SecondFactor | null>({ method: 'totp' });
  const [enrollOpen, setEnrollOpen] = useState(false);
  const [enrollData, setEnrollData] = useState<bank.TotpEnroll | null>(null);
  const [enrollCode, setEnrollCode] = useState('');
  const payerSelectorRef = useRef<HTMLDetailsElement>(null);

  const message = (error: unknown) => {
    const detail = (error as { response?: { data?: { detail?: string | { message?: string; capability?: string } } } })?.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (detail?.message) return detail.message;
    if (detail?.capability) return `${detail.capability} 暂未实现`;
    return error instanceof Error ? error.message : '请求失败，请稍后重试';
  };

  const load = async () => {
    if (!user) return;
    try {
      if (['U007', 'U008'].includes(user.user_id)) {
        const [merchantAccounts, merchantTransactions] = await Promise.all([
          bank.accounts(user.user_id),
          bank.history(),
        ]);
        setAccounts(merchantAccounts);
        setTransactions(merchantTransactions);
        setBankRevision(value => value + 1);
        return;
      }
      const [a, r, h, requests, subscriptionItems] = await Promise.all([bank.accounts(user.user_id), bank.recipients(), bank.history(), bank.paymentRequests(), bank.listSubscriptions()]);
      setAccounts(a); setRecipients(r); setTransactions(h); setPaymentRequests(requests); setSubscriptions(subscriptionItems);
      setBankRevision(value => value + 1);
      const defaultFrom = a.find(acc => acc.account_type === 'savings')?.account_id || a[0]?.account_id || '';
      setFromAccount(current => current && a.some(acc => acc.account_id === current) ? current : defaultFrom);
      setToUserId(current => current && r.some(rec => rec.user_id === current) ? current : r[0]?.user_id || '');
      setRequestPayerIds(current => current.filter(id => r.some(rec => rec.user_id === id)));
      setRequestAccountId(current => current && a.some(acc => acc.account_id === current) ? current : defaultFrom);
      bank.totpStatus().then(s => setTotpEnabled(s.enabled)).catch(() => {});
    } catch (error) {
      if (!localStorage.getItem('token')) {
        logout(); setStatus('登录已过期，请重新登录');
      } else setStatus(message(error));
    }
  };

  useEffect(() => { load(); }, [user?.user_id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const closePayerSelector = (event: MouseEvent) => {
      const selector = payerSelectorRef.current;
      if (selector?.open && event.target instanceof Node && !selector.contains(event.target)) {
        selector.open = false;
      }
    };
    document.addEventListener('mousedown', closePayerSelector);
    return () => document.removeEventListener('mousedown', closePayerSelector);
  }, []);
  useEffect(() => {
    if (!activeTask || !['WAITING_EXECUTION', 'EXECUTING', 'RECONCILING'].includes(activeTask.phase)) return;
    const timer = window.setInterval(async () => {
      try {
        const task = await bank.getTask(activeTask.task_id);
        setActiveTask(task); setStatus(task.message);
        if (task.phase === 'FINISHED') {
          if (user) localStorage.removeItem(`scheduled-task-${user.user_id}`);
          await load();
        }
      } catch (error) { setStatus(message(error)); }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [activeTask?.task_id, activeTask?.phase]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setConversation(null); setActiveTask(null); setRetryChat(null); setChat([]);
    if (!user) return;
    let cancelled = false;
    const scheduledTaskId = localStorage.getItem(`scheduled-task-${user.user_id}`);
    const activeTaskId = localStorage.getItem(`active-task-${user.user_id}`);
    const saved = localStorage.getItem(`transfer-session-${user.user_id}`);
    if (saved) bank.getConversation(saved).then(reply => {
      if (cancelled) return;
      setConversation(reply); if (!scheduledTaskId) setActiveTask(reply.task || null); setChat([`系统：${reply.message}`]);
    }).catch(error => { if (!cancelled) setStatus(message(error)); });
    const pending = localStorage.getItem(`transfer-pending-${user.user_id}`);
    if (pending) setRetryChat(JSON.parse(pending));
    if (activeTaskId && !saved && !scheduledTaskId) bank.getTask(activeTaskId).then(task => {
      if (!cancelled) setActiveTask(task);
    }).catch(() => localStorage.removeItem(`active-task-${user.user_id}`));
    if (scheduledTaskId) bank.getTask(scheduledTaskId).then(task => {
      if (cancelled) return;
      if (task.phase === 'FINISHED') localStorage.removeItem(`scheduled-task-${user.user_id}`);
      else setActiveTask(task);
    }).catch(() => localStorage.removeItem(`scheduled-task-${user.user_id}`));
    return () => { cancelled = true; };
  }, [user?.user_id]);

  const authenticate = async (event: FormEvent) => {
    event.preventDefault(); setStatus(''); setIsAuthenticating(true);
    try {
      const result = loginMode === 'login' ? await bank.login(phone, password) : await bank.register(name, phone, password);
      localStorage.setItem('token', result.access_token);
      localStorage.setItem('bank-user', JSON.stringify(result.user));
      setUser(result.user);
    } catch (error) { setStatus(`登录失败：${message(error)}`); }
    finally { setIsAuthenticating(false); }
  };

  const submitTransfer = async (event: FormEvent) => {
    event.preventDefault(); setStatus('');
    const recipient = recipients.find(item => item.user_id === toUserId);
    const phoneChoice = phoneCandidates.find(item => item.account_id === selectedPhoneAccount);
    if (!fromAccount || (recipientMode === 'name' && !recipient)
      || (recipientMode === 'phone' && (!phoneChoice || lookedUpPhone !== recipientPhone))) {
      return setStatus('请选择付款账户并核验收款人；手机号变更后请重新查询');
    }
    if (transferMode === 'scheduled') {
      if (!scheduledAt) return setStatus('请选择定时执行时间');
      if (new Date(fromBeijingInput(scheduledAt)).getTime() <= Date.now()) return setStatus('定时执行时间必须晚于当前时间');
    }
    try {
      const task = await bank.createTransferTask({
        request_id: newId(), operation_id: newId(), from_account: fromAccount,
        to_account: recipientMode === 'phone' ? phoneChoice!.account_id : recipient!.account_id,
        recipient_phone: recipientMode === 'phone' ? recipientPhone : undefined,
        amount: Number(amount), note: note.trim() ? note : null,
        note_source: note.trim() ? 'manual' : 'cleared',
        scheduled_at: transferMode === 'scheduled' ? fromBeijingInput(scheduledAt) : undefined,
      });
      if (user && transferMode === 'scheduled') {
        localStorage.setItem(`scheduled-task-${user.user_id}`, task.task_id);
      }
      setActiveTask(task); setStatus(task.message);
      if (user) localStorage.setItem(`active-task-${user.user_id}`, task.task_id);
    } catch (error) { setStatus(message(error)); }
  };

  const lookupPhone = async () => {
    setPhoneCandidates([]); setSelectedPhoneAccount(''); setLookedUpPhone('');
    try {
      const candidates = await bank.lookupPhoneRecipients(recipientPhone);
      setPhoneCandidates(candidates); setLookedUpPhone(recipientPhone);
      setSelectedPhoneAccount(candidates.length === 1 ? candidates[0].account_id : '');
      if (!candidates.length) setStatus('未找到可收款账户，请核对手机号');
    } catch (error) { setStatus(message(error)); }
  };

  const saveEditedNote = async () => {
    if (!activeTask || editingNote === null) return;
    setIsActing(true);
    try {
      const updated = await bank.modifyTask(activeTask, {
        operation_id: newId(), from_account: activeTask.operation.from_account,
        to_account: activeTask.operation.to_account, amount: activeTask.operation.amount,
        note: editNoteCleared ? null : editingNote,
        note_source: editNoteCleared ? 'cleared' : 'manual',
      });
      setActiveTask(updated); setEditingNote(null); setStatus('备注已更新，请重新确认新计划');
    } catch (error) { setStatus(message(error)); }
    finally { setIsActing(false); }
  };

  const submitPaymentRequest = async (event: FormEvent) => {
    event.preventDefault(); setStatus('');
    if (requestPayerIds.length === 0 || !requestAccountId) return setStatus('请至少选择一位付款人和收款账户');
    const amountFen = Math.round(Number(requestAmount) * 100);
    if (splitEqually && amountFen % requestPayerIds.length !== 0) {
      return setStatus('总金额无法精确均分到分，请调整金额');
    }
    setIsActing(true);
    try {
      await bank.createPaymentRequestBatch({
        payer_user_ids: requestPayerIds, receive_account_id: requestAccountId,
        amount: Number(requestAmount), split_equally: splitEqually,
        note: requestNote || undefined, idempotency_key: newId(),
      });
      setRequestAmount(''); setRequestNote(''); setStatus(`已向 ${requestPayerIds.length} 人发出收款申请`); await load();
    } catch (error) { setStatus(message(error)); }
    finally { setIsActing(false); }
  };

  const actOnPaymentRequest = async (request: bank.PaymentRequest, action: 'accept' | 'reject' | 'cancel') => {
    if (isActing) return;
    setIsActing(true); setStatus('');
    try {
      if (action === 'accept') {
        if (!fromAccount) throw new Error('请选择付款账户');
        let task = await bank.acceptPaymentRequest(request, fromAccount);
        // 第一次点击仅创建并展示安全转账任务；第二次“确认付款”才记录授权。
        // 低风险授权后立即提交银行，大额则停在 MFA 验证阶段。
        if (request.status === 'ACCEPTED' && task.allowed_actions.includes('confirm')) {
          task = await bank.confirmTask(task, newId());
        }
        if (request.status === 'ACCEPTED' && task.allowed_actions.includes('accept')) {
          task = await bank.acceptTask(task, newId());
        }
        setActiveTask(task);
        setStatus(task.message);
        await load();
        return;
      }
      else if (action === 'reject') await bank.rejectPaymentRequest(request);
      else await bank.cancelPaymentRequest(request);
      setStatus(action === 'reject' ? '已拒绝收款申请' : '已取消收款申请');
      await load();
    } catch (error) { setStatus(message(error)); }
    finally { setIsActing(false); }
  };

  const confirmSubscriptionCancellation = async () => {
    if (!pendingSubscription || !subscriptionCancellationToken || isActing) return;
    setIsActing(true); setStatus('');
    try {
      const updated = await bank.cancelSubscription(pendingSubscription, subscriptionCancellationToken);
      setSubscriptions(items => items.map(item => item.subscription_id === updated.subscription_id ? updated : item));
      setPendingSubscription(null); setSubscriptionCancellationToken('');
      setStatus(`已取消 ${updated.merchant_name} 订阅，后续不会再自动扣费`);
    } catch (error) { setStatus(message(error)); }
    finally { setIsActing(false); }
  };

  const confirmSubscriptionCharge = async () => {
    if (!pendingChargeSubscription || !subscriptionChargeId || isActing) return;
    setIsActing(true); setStatus('');
    try {
      const result = await bank.simulateSubscriptionCharge(pendingChargeSubscription, subscriptionChargeId);
      setSubscriptions(items => items.map(item => item.subscription_id === result.subscription.subscription_id ? result.subscription : item));
      setPendingChargeSubscription(null); setSubscriptionChargeId(''); setStatus(result.message);
      await load();
    } catch (error) { setStatus(message(error)); }
    finally { setIsActing(false); }
  };

  const sendChat = async (candidateId?: string, retry = false) => {
    if (!user || isChatting || (!candidateId && !retry && !chatInput.trim())) return;
    const input = candidateId ? '选择候选' : chatInput.trim();
    const payload: bank.ChatRequest = retry && retryChat ? retryChat : {
      request_id: newId(), operation_id: newId(), message: input,
      session_id: conversation?.session_id, session_version: conversation?.session_version || 0,
      candidate_id: candidateId, task_id: conversation?.task?.task_id,
      state_version: activeTask?.task_id === conversation?.task?.task_id ? activeTask?.state_version : conversation?.task?.state_version,
    };
    setIsChatting(true); setRetryChat(payload);
    localStorage.setItem(`transfer-pending-${user.user_id}`, JSON.stringify(payload));
    try {
      const reply = await bank.createChatTask(payload);
      setConversation(reply); setChatInput(''); setRetryChat(null);
      localStorage.removeItem(`transfer-pending-${user.user_id}`);
      localStorage.setItem(`transfer-session-${user.user_id}`, reply.session_id);
      setChat(items => [...items, `你：${payload.message}`, `系统：${reply.message}`]);
      if (reply.task) setActiveTask(reply.task);
      setStatus(reply.message);
    } catch (error) {
      setStatus(message(error));
      const code = (error as { response?: { status?: number } }).response?.status;
      if (code && code < 500) {
        setRetryChat(null); localStorage.removeItem(`transfer-pending-${user.user_id}`);
        if (conversation) {
          const latest = await bank.getConversation(conversation.session_id).catch(() => null);
          if (latest) { setConversation(latest); if (latest.task) setActiveTask(latest.task); }
        }
      }
    } finally { setIsChatting(false); }
  };

  const newConversation = () => {
    if (!user || retryChat || isChatting) return;
    localStorage.removeItem(`transfer-session-${user.user_id}`);
    setConversation(null); setActiveTask(null); setChat([]); setChatInput('');
  };

  const actOnTask = async (action: 'confirm' | 'cancel' | 'reconcile') => {
    if (!activeTask || isActing || isChatting || (conversation?.task?.task_id === activeTask.task_id && conversation.kind === 'clarification')) return;
    setIsActing(true);
    try {
      let task = action === 'confirm' ? await bank.confirmTask(activeTask, newId())
        : action === 'cancel' ? await bank.cancelTask(activeTask, newId())
        : await bank.reconcileTask(activeTask, newId());
      if (action === 'confirm' && task.allowed_actions.includes('accept')) {
        task = await bank.acceptTask(task, newId());
      }
      setActiveTask(task); setStatus(task.message);
      if (task.phase === 'FINISHED') {
        if (user) {
          localStorage.removeItem(`scheduled-task-${user.user_id}`);
          localStorage.removeItem(`active-task-${user.user_id}`);
        }
        setAmount(''); setNote(''); setScheduledAt(''); await load();
      }
    } catch (error) { setStatus(message(error)); }
    finally { setIsActing(false); }
  };

  const errorCode = (error: unknown) =>
    (error as { response?: { data?: { detail?: { code?: string } } } })?.response?.data?.detail?.code;

  // 提交 TOTP 验证码完成红色路径强验证。
  const submitMfa = async () => {
    if (!activeTask || !mfaFactor || mfaCode.length !== 6 || isActing) return;
    setIsActing(true); setStatus('');
    try {
      let task = await bank.verifyTask(activeTask, newId(), mfaCode, mfaFactor);
      if (task.allowed_actions.includes('accept')) {
        task = await bank.acceptTask(task, newId());
      }
      setActiveTask(task); setStatus(task.message);
      setMfaOpen(false); setMfaCode('');
      if (task.phase === 'FINISHED') {
        if (user) localStorage.removeItem(`scheduled-task-${user.user_id}`);
        setAmount(''); setNote(''); setScheduledAt(''); await load();
      }
    } catch (error) {
      setStatus(message(error));
      // 尚未绑定验证器时，直接引导去绑定。
      if (errorCode(error) === 'TOTP_NOT_ENROLLED') { setMfaOpen(false); setMfaCode(''); openEnroll(); }
    } finally { setIsActing(false); }
  };

  // 打开绑定弹窗：向后端申请新密钥与二维码。
  const openEnroll = async () => {
    if (isActing) return;
    setStatus('');
    try {
      const data = await bank.totpEnroll();
      setEnrollData(data); setEnrollCode(''); setEnrollOpen(true);
    } catch (error) { setStatus(message(error)); }
  };

  // 确认绑定：校验一次验证码后正式启用。
  const submitEnroll = async () => {
    if (enrollCode.length !== 6 || isActing) return;
    setIsActing(true); setStatus('');
    try {
      await bank.totpConfirm(enrollCode);
      setTotpEnabled(true); setEnrollOpen(false); setEnrollData(null); setEnrollCode('');
      setStatus('验证器绑定成功，后续大额转账可用它完成身份验证');
    } catch (error) { setStatus(message(error)); }
    finally { setIsActing(false); }
  };

  const logout = () => {
    localStorage.removeItem('token'); localStorage.removeItem('bank-user');
    setUser(null); setAccounts([]); setRecipients([]); setTransactions([]); setPaymentRequests([]); setSubscriptions([]); setPendingSubscription(null); setSubscriptionCancellationToken(''); setPendingChargeSubscription(null); setSubscriptionChargeId('');
    setChat([]); setActiveTask(null); setConversation(null); setRetryChat(null); setStatus('');
    setFromAccount(''); setToUserId(''); setRequestPayerIds([]); setRequestAccountId(''); setScheduledAt('');
    setRecipientMode('name'); setRecipientPhone(''); setPhoneCandidates([]); setSelectedPhoneAccount(''); setLookedUpPhone('');
    setAmount(''); setNote(''); setEditingNote(null); setEditNoteCleared(false); setTransferMode('instant');
    setRequestAmount(''); setRequestNote(''); setSplitEqually(false); setChatInput('');
    setTotpEnabled(false); setMfaOpen(false); setMfaCode(''); setEnrollOpen(false); setEnrollData(null); setEnrollCode('');
    setAppView('banking'); setBankingSection('home'); setWealthTestMode(false); setAgentOpen(false); setHighlightedPaymentRequest('');
  };

  const goToBankPayment = async (paymentRequestId?: string) => {
    setHighlightedPaymentRequest(paymentRequestId || '');
    setAppView('banking');
    setBankingSection('transfer');
    await load();
    window.setTimeout(() => document.getElementById('payment-requests')?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50);
  };

  const requestAmountNumber = Number(requestAmount) || 0;
  const splitIsExact = requestPayerIds.length > 0
    && Math.round(requestAmountNumber * 100) % requestPayerIds.length === 0;
  const perPersonAmount = splitEqually && requestPayerIds.length > 0
    ? requestAmountNumber / requestPayerIds.length
    : requestAmountNumber;
  const requestTotal = splitEqually
    ? requestAmountNumber
    : requestAmountNumber * requestPayerIds.length;
  const cashAccounts = accounts.filter(account => account.account_type === 'savings');
  const totalBalance = cashAccounts.reduce((sum, account) => sum + account.balance, 0);
  const availableBalance = cashAccounts.reduce((sum, account) => sum + ((account.available_fen ?? Math.round(account.balance * 100)) / 100), 0);
  const heldBalance = cashAccounts.reduce((sum, account) => sum + ((account.held_fen || 0) / 100), 0);
  const activeSubscriptions = subscriptions.filter(subscription => subscription.status === 'ACTIVE');
  const pendingRequests = paymentRequests.filter(request => ['PENDING', 'ACCEPTED'].includes(request.status)).length;
  const recentSpend = transactions.slice(0, 8).filter(transaction => ['transfer_out', 'purchase', 'subscription', 'investment_purchase'].includes(transaction.type)).reduce((sum, transaction) => sum + transaction.amount, 0);
  const openSection = (section: BankingSection) => { setAppView('banking'); setBankingSection(section); window.scrollTo({ top: 0, behavior: 'smooth' }); };

  if (!user) return <main className="auth"><BeijingClock /><section className="card"><h1>AI 模拟银行</h1><p>任务状态机转账演示</p><form onSubmit={authenticate}>
    {loginMode === 'register' && <label>姓名<input required value={name} onChange={e => setName(e.target.value)} /></label>}
    <label>手机号<input required value={phone} onChange={e => setPhone(e.target.value)} /></label>
    <label>密码<input required minLength={8} type="password" value={password} onChange={e => setPassword(e.target.value)} /></label>
    <button disabled={isAuthenticating}>{isAuthenticating ? '正在登录…' : loginMode === 'login' ? '登录' : '注册并登录'}</button>
  </form><button className="link" onClick={() => setLoginMode(loginMode === 'login' ? 'register' : 'login')}>{loginMode === 'login' ? '没有账号？注册' : '已有账号？登录'}</button>
  <p className="hint">演示账户：张三 13800000001 / password123</p>{status && <p className="notice">{status}</p>}</section></main>;

  if (['U007', 'U008'].includes(user.user_id)) return <><BeijingClock /><Suspense fallback={<main className="auth"><section className="card"><p>正在加载商户工作台…</p></section></main>}>
    <MerchantDashboard user={user} accounts={accounts} transactions={transactions} revision={bankRevision} onRefresh={load} onLogout={logout} />
  </Suspense></>;

  return <main className="app prototype-app">
    <div className="bank-shell">
      <aside className="bank-sidebar">
        <button className="brand" onClick={() => openSection('home')} aria-label="返回首页"><span className="brand-mark">N</span><span>NOVA<small>AI BANKING</small></span></button>
        <nav aria-label="主要功能">
          {([['home','⌂','概览'],['transfer','↗','转账与收款'],['insights','◫','账单洞察'],['cards','▣','卡片'],['subscriptions','↻','订阅'],['wealth','◇','理财']] as [BankingSection,string,string][]).map(([key,icon,label]) =>
            <button key={key} className={appView === 'banking' && bankingSection === key ? 'active' : ''} onClick={() => openSection(key)}><span>{icon}</span>{label}</button>)}
          <button className={appView === 'thirdParty' ? 'active' : ''} onClick={() => setAppView('thirdParty')}><span>＋</span>生活服务</button>
        </nav>
        <div className="sidebar-foot"><span className="security-dot" />模拟环境 · 连接正常<small>所有资金与交易均为虚构</small></div>
      </aside>
      <div className="bank-workspace">
        <header className="bank-topbar"><div><BeijingClock /><span className="workspace-label">{appView === 'thirdParty' ? '生活服务' : ({home:'财务概览',transfer:'转账与收款',insights:'账单洞察',cards:'卡片管理',subscriptions:'订阅管理',wealth:'理财规划'} as Record<BankingSection,string>)[bankingSection]}</span></div><div className="top-actions"><button className="agent-button" onClick={() => setAgentOpen(true)}><span>✦</span> 问 NOVA</button><button className="icon-button" disabled={isActing} onClick={openEnroll} title={totpEnabled ? '已绑定验证器' : '绑定验证器'}>⌾</button><button className="profile-button" onClick={logout} title="退出登录">{user.name.slice(0,1)}</button></div></header>
        {status && <p className="notice global-notice">{status}</p>}
        <div className="workspace-content">
        {appView === 'thirdParty' ? <Suspense fallback={<section className="card"><p>正在加载第三方服务…</p></section>}>
          <ThirdPartyServices revision={bankRevision} onBack={() => openSection('home')} onGoBank={goToBankPayment} />
        </Suspense> : <>
        {bankingSection === 'home' && <>
          <section className="dashboard-hero">
            <div><p className="eyebrow">YOUR MONEY, IN VIEW</p><h1>{user.name}，今天的财务状态很清晰。</h1><p>先看重点，再决定下一步。NOVA 会读取你有权查看的模拟银行事实，但敏感操作仍由你本人确认。</p><div className="hero-actions"><button onClick={() => setAgentOpen(true)}>✦ 和 NOVA 对话</button><button className="glass-button" onClick={() => openSection('transfer')}>发起转账</button></div></div>
            <div className="safe-card"><span>当前可用资金</span><strong>¥{availableBalance.toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})}</strong><small>总账面 ¥{totalBalance.toLocaleString('zh-CN',{minimumFractionDigits:2})} · 占款 ¥{heldBalance.toFixed(2)}</small><div className="safe-line"><i style={{width:`${Math.max(8,Math.min(100,totalBalance ? availableBalance/totalBalance*100 : 0))}%`}} /></div><p>基于当前账户事实，不包含未来收入。</p></div>
          </section>
          <section className="metric-grid">
            <article><span>近期支出</span><strong>¥{recentSpend.toLocaleString('zh-CN',{minimumFractionDigits:2})}</strong><small>最近 8 笔流水中的支出</small></article>
            <article><span>生效中订阅</span><strong>{activeSubscriptions.length}</strong><small>{activeSubscriptions.length ? `下次：${activeSubscriptions[0].merchant_name}` : '目前没有待续费项目'}</small></article>
            <article><span>待处理事项</span><strong>{pendingRequests + (activeTask ? 1 : 0)}</strong><small>{activeTask ? `转账任务 ${activeTask.phase}` : pendingRequests ? `${pendingRequests} 项收款申请` : '当前无需处理'}</small></article>
          </section>
          <section className="dashboard-grid">
            <article className="card overview-card"><div className="section-heading"><div><p className="eyebrow">ACCOUNTS</p><h2>账户一览</h2></div><button className="text-button" onClick={() => openSection('transfer')}>管理资金 →</button></div>{accounts.map(account => <div className="account-row" key={account.account_id}><span className="account-icon">{account.account_type === 'savings' ? '储' : '账'}</span><span><strong>{account.account_type === 'savings' ? '活期账户' : account.account_type}</strong><small>{account.card_number}</small></span><b>¥{account.balance.toLocaleString('zh-CN',{minimumFractionDigits:2})}</b></div>)}</article>
            <article className="card overview-card"><div className="section-heading"><div><p className="eyebrow">NEXT BEST ACTIONS</p><h2>值得关注</h2></div></div>
              <button className="insight-row" onClick={() => openSection('insights')}><span className="insight-icon coral">↗</span><span><strong>看懂最近的资金变化</strong><small>账单分析会解释分类、趋势和异常。</small></span><b>›</b></button>
              <button className="insight-row" onClick={() => openSection('subscriptions')}><span className="insight-icon violet">↻</span><span><strong>{activeSubscriptions.length} 项订阅正在生效</strong><small>核对下次扣费，按需取消。</small></span><b>›</b></button>
              <button className="insight-row" onClick={() => openSection('wealth')}><span className="insight-icon mint">◇</span><span><strong>把可用资金变成清晰计划</strong><small>先形成需求卡，再确认任何模拟订单。</small></span><b>›</b></button>
            </article>
          </section>
          <section className="card overview-card activity-card"><div className="section-heading"><div><p className="eyebrow">RECENT ACTIVITY</p><h2>最近流水</h2></div><button className="text-button" onClick={() => openSection('insights')}>查看分析 →</button></div>{transactions.slice(0,5).map(tx => { const outgoing=['transfer_out','purchase','subscription','investment_purchase'].includes(tx.type); return <div className="activity-row" key={tx.tx_id}><span className={`activity-mark ${outgoing?'expense':'income'}`}>{outgoing?'−':'+'}</span><span><strong>{tx.note || (outgoing?'资金支出':'资金收入')}</strong><small>{beijingTime(tx.timestamp)}</small></span><b className={outgoing?'out':'in'}>{outgoing?'-':'+'}¥{tx.amount.toFixed(2)}</b></div>;})}</section>
        </>}
        {bankingSection === 'wealth' && <><div className="page-intro wealth-intro"><div><p className="eyebrow">WEALTH</p><h1>从需求开始，而不是从产品开始。</h1><p>研究、规划、本人确认和模拟银行执行保持分离。</p></div><button className={wealthTestMode ? 'secondary' : 'test-mode-button'} onClick={() => setWealthTestMode(value => !value)}>{wealthTestMode ? '退出测试模式' : '进入测试模式'}</button></div>{wealthTestMode && <><p className="notice wealth-test-notice">测试模式已开启：下方保留团队原有的理财工作台与模拟测试工具。重置资金或删除旧轮次仍需单独确认。</p><Suspense fallback={null}><DemoTools key={user.user_id} userId={user.user_id} revision={bankRevision} changed={async()=>{setWealthWorkspaceEpoch(value=>value+1);await load();}} /></Suspense></>}<Suspense fallback={<section className="card"><p>正在加载理财模拟…</p></section>}><Wealth key={`${user.user_id}:${wealthWorkspaceEpoch}`} userId={user.user_id} accounts={accounts} revision={bankRevision} onActivity={load} /></Suspense></>}
        {bankingSection === 'insights' && <><div className="page-intro"><p className="eyebrow">INSIGHTS</p><h1>让每一笔变化都有解释。</h1><p>账单分析保持只读，不会通过问答直接修改账户。</p></div><Suspense fallback={<section className="card"><p>正在加载账单分析…</p></section>}><BillAnalysis key={user.user_id} accounts={accounts} revision={bankRevision} /></Suspense></>}
        {bankingSection === 'transfer' && <><div className="page-intro"><p className="eyebrow">MOVE MONEY</p><h1>转账、收款和任务状态集中在一处。</h1><p>先生成确认信息；高风险操作仍需要本人验证。</p></div><section className="grid">
      <div className="card"><h2>我的账户</h2>{accounts.map(a => <div className="account" key={a.account_id}><span>{a.account_type === 'savings' ? '活期账户' : a.account_type} · {a.card_number}</span><strong>¥{a.balance.toFixed(2)}</strong>{a.available_fen != null && <small>可用 ¥{(a.available_fen / 100).toFixed(2)} · 占款 ¥{((a.held_fen || 0) / 100).toFixed(2)}</small>}<small>今日已转 ¥{a.daily_transfer_used.toFixed(2)} / ¥{a.daily_transfer_limit.toFixed(2)}</small></div>)}</div>
      <div className="card"><h2>创建转账任务</h2><form onSubmit={submitTransfer}>
        <label>付款账户<select value={fromAccount} onChange={e => setFromAccount(e.target.value)}>{accounts.map(a => <option value={a.account_id} key={a.account_id}>{a.card_number}（¥{a.balance.toFixed(2)}）</option>)}</select></label>
        <label>收款方式<select value={recipientMode} onChange={e => setRecipientMode(e.target.value as 'name' | 'phone')}><option value="name">按姓名选择</option><option value="phone">按手机号查询</option></select></label>
        {recipientMode === 'name'
          ? <label>收款人<select value={toUserId} onChange={e => setToUserId(e.target.value)}>{recipients.map(r => <option value={r.user_id} key={r.user_id}>{r.name} · {r.bank}</option>)}</select></label>
          : <><label>手机号<input value={recipientPhone} onChange={e => { setRecipientPhone(e.target.value); setPhoneCandidates([]); setSelectedPhoneAccount(''); setLookedUpPhone(''); }} /></label>
            <button type="button" className="secondary" onClick={lookupPhone}>查询收款账户</button>
            {phoneCandidates.length > 0 && <label>收款账户<select value={selectedPhoneAccount} onChange={e => setSelectedPhoneAccount(e.target.value)}><option value="">请选择脱敏候选</option>{phoneCandidates.map(candidate => <option value={candidate.account_id} key={candidate.account_id}>{candidate.label}</option>)}</select></label>}</>}
        <label>金额（元）<input required min="0.01" step="0.01" type="number" value={amount} onChange={e => setAmount(e.target.value)} /></label>
        <label>备注（可选）<input maxLength={256} value={note} onChange={e => setNote(e.target.value)} /></label>
        <label>执行方式<select value={transferMode} onChange={e => { const mode = e.target.value as 'instant' | 'scheduled'; setTransferMode(mode); if (mode === 'scheduled' && !scheduledAt) setScheduledAt(suggestedScheduleTime()); }}><option value="instant">立即转账</option><option value="scheduled">定时转账</option></select></label>
        {transferMode === 'scheduled' && <><label>执行时间<input required min={localDateTimeInput(new Date())} type="datetime-local" value={scheduledAt} onChange={e => setScheduledAt(e.target.value)} /><small>点击右侧日历图标选择日期和时间，例如：2026/09/26 21:31。系统按北京时间执行。</small></label><button type="button" className="secondary" onClick={() => setScheduledAt(suggestedScheduleTime())}>快速设置为约 2 分钟后</button></>}
        <button disabled={!fromAccount || (recipientMode === 'name' ? !toUserId : !selectedPhoneAccount) || isActing}>生成确认信息</button>
      </form></div>
    </section>
    <section className="grid">
      <div className="card"><h2>发起收款申请</h2><form onSubmit={submitPaymentRequest}>
        <label>向谁收款（可多选）<details className="multi-select" ref={payerSelectorRef}><summary>{requestPayerIds.length ? `已选择 ${requestPayerIds.length} 人：${recipients.filter(r => requestPayerIds.includes(r.user_id)).map(r => r.name).join('、')}` : '请选择联系人'}</summary><div>{recipients.map(r => <label className="check-option" key={r.user_id}><input type="checkbox" checked={requestPayerIds.includes(r.user_id)} onChange={e => setRequestPayerIds(ids => e.target.checked ? [...ids, r.user_id] : ids.filter(id => id !== r.user_id))} />{r.name} · {r.phone}</label>)}</div></details></label>
        <label>收款账户<select value={requestAccountId} onChange={e => setRequestAccountId(e.target.value)}>{accounts.map(a => <option value={a.account_id} key={a.account_id}>{a.card_number}</option>)}</select></label>
        <label>{splitEqually ? 'AA 总金额（元）' : '每人收款金额（元）'}<input required min="0.01" step="0.01" type="number" value={requestAmount} onChange={e => setRequestAmount(e.target.value)} /></label>
        <button type="button" className={splitEqually ? '' : 'secondary'} onClick={() => setSplitEqually(value => !value)}>AA 均分：{splitEqually ? '已开启' : '未开启'}</button>
        {requestPayerIds.length > 0 && requestAmountNumber > 0 && <p className={splitEqually && !splitIsExact ? 'error' : 'notice'}>{splitEqually && !splitIsExact ? '该金额无法精确均分到分，请调整金额' : `将向每人收款 ¥${perPersonAmount.toFixed(2)}，共 ${requestPayerIds.length} 人，总申请金额 ¥${requestTotal.toFixed(2)}`}</p>}
        <label>备注（可选）<input maxLength={256} value={requestNote} onChange={e => setRequestNote(e.target.value)} /></label>
        <button disabled={requestPayerIds.length === 0 || !requestAccountId || (splitEqually && !splitIsExact) || isActing}>发出申请</button>
      </form></div>
      <div className="card" id="payment-requests"><h2>收款申请</h2>{paymentRequests.some(request => ['PENDING', 'ACCEPTED'].includes(request.status) && request.payer_user_id === user.user_id) && <label>付款账户<select value={fromAccount} onChange={e => setFromAccount(e.target.value)}>{accounts.map(a => <option value={a.account_id} key={a.account_id}>{a.card_number}（¥{a.balance.toFixed(2)}）</option>)}</select></label>}{paymentRequests.length === 0 ? <p className="hint">暂无收款申请</p> : <ul className="history">{paymentRequests.map(request => {
        const incoming = request.payer_user_id === user.user_id;
        const canPay = incoming && ['PENDING', 'ACCEPTED'].includes(request.status);
        return <li className={highlightedPaymentRequest === request.payment_request_id ? 'payment-request-highlight' : ''} key={request.payment_request_id}><span>{incoming ? `${request.requester_name} 向你收款` : `向 ${request.payer_name} 收款`} {request.note && `· ${request.note}`}<small>{request.status} · {beijingTime(request.created_at)}</small></span><strong>¥{request.amount.toFixed(2)}</strong>{(canPay || request.status === 'PENDING') && <div className="confirm">{canPay ? <><button disabled={isActing} onClick={() => actOnPaymentRequest(request, 'accept')}>{request.status === 'ACCEPTED' ? '确认付款' : '接受申请'}</button>{request.status === 'PENDING' && <button className="secondary" disabled={isActing} onClick={() => actOnPaymentRequest(request, 'reject')}>拒绝</button>}</> : <button className="secondary" disabled={isActing} onClick={() => actOnPaymentRequest(request, 'cancel')}>取消</button>}</div>}</li>;
      })}</ul>}<p className="hint">接受申请后先核对转账任务，再确认付款；需要强验证的任务会停留在身份验证阶段。</p></div>
    </section>
    {activeTask && <section className="card task-focus">
      <h2>转账任务 · {activeTask.phase}</h2><p>{activeTask.message}</p>
      {activeTask.confirmation_card && <p>从 {activeTask.confirmation_card.from_account} 向 {activeTask.confirmation_card.recipient_name}{activeTask.confirmation_card.recipient_phone_masked && `（${activeTask.confirmation_card.recipient_phone_masked}）`} 转入 {activeTask.confirmation_card.to_account} <strong>¥{activeTask.confirmation_card.amount.toFixed(2)}</strong> CNY · 备注：{activeTask.confirmation_card.note ?? '无'} · 风险级别 {activeTask.confirmation_card.risk_level}</p>}
      {activeTask.confirmation_card && activeTask.allowed_actions.includes('modify') && conversation?.task?.task_id !== activeTask.task_id && <>
        {editingNote === null
          ? <button className="secondary" onClick={() => { setEditingNote(activeTask.confirmation_card?.note || ''); setEditNoteCleared(activeTask.confirmation_card?.note_source === 'cleared'); }}>修改或清空备注</button>
          : <div className="row"><input maxLength={256} disabled={editNoteCleared} value={editingNote} onChange={e => setEditingNote(e.target.value)} /><label><input type="checkbox" checked={editNoteCleared} onChange={e => setEditNoteCleared(e.target.checked)} />清空</label><button disabled={isActing || (!editNoteCleared && !editingNote.trim())} onClick={saveEditedNote}>保存并重新确认</button><button className="secondary" onClick={() => setEditingNote(null)}>返回</button></div>}
      </>}
      {activeTask.scheduled_at && <p className="notice">计划执行时间：{beijingTime(activeTask.scheduled_at)}</p>}
      <p className="hint">应用操作：{activeTask.operation.status} · 银行状态：{activeTask.operation.bank_status || '尚未受理'} {activeTask.operation.bank_tx_id && `· 银行流水 ${activeTask.operation.bank_tx_id}`} · 最终备注：{activeTask.operation.note ?? '无'}</p>
      <div className="confirm">
        {activeTask.allowed_actions.includes('cancel') && <button className="secondary" disabled={isActing || isChatting || (conversation?.task?.task_id === activeTask.task_id && conversation.kind === 'clarification')} onClick={() => actOnTask('cancel')}>取消任务</button>}
        {activeTask.allowed_actions.includes('confirm') && <button disabled={isActing || isChatting || (conversation?.task?.task_id === activeTask.task_id && conversation.kind === 'clarification')} onClick={() => actOnTask('confirm')}>{activeTask.scheduled_at ? '确认定时转账' : '确认并提交'}</button>}
        {activeTask.allowed_actions.includes('verify') && <button disabled={isActing || isChatting || (conversation?.task?.task_id === activeTask.task_id && conversation.kind === 'clarification')} onClick={() => { setMfaCode(''); setMfaFactor({ method: 'totp' }); setMfaOpen(true); }}>身份验证</button>}
        {activeTask.allowed_actions.includes('reconcile') && <button disabled={isActing || isChatting || (conversation?.task?.task_id === activeTask.task_id && conversation.kind === 'clarification')} onClick={() => actOnTask('reconcile')}>核对银行结果</button>}
      </div>
    </section>}
    <section className="grid"><div className="card"><h2>即时转账对话</h2><p className="hint">例如：给手机号13800000002转300元，备注房租；改成500元；清空备注。当前模式：{conversation?.mode === 'model' ? '模型理解' : '离线规则（有限表达）'}。</p><button className="secondary" disabled={isChatting || !!retryChat} onClick={newConversation}>新建转账会话</button><div className="chat">{chat.map((item,index)=><p key={index}>{item}</p>)}</div>{conversation?.candidates.map(candidate=><button key={candidate.candidate_id} disabled={isChatting || !!retryChat} onClick={()=>sendChat(candidate.candidate_id)}>{candidate.label}</button>)}<div className="row"><input value={chatInput} disabled={isChatting || !!retryChat} onChange={e=>setChatInput(e.target.value)} onKeyDown={e=>e.key==='Enter'&&!retryChat&&sendChat()} placeholder="给手机号13800000002转300元，备注聚餐"/><button disabled={isChatting || !!retryChat} onClick={()=>sendChat()}>发送</button></div>{retryChat&&!isChatting&&<button onClick={()=>sendChat(undefined,true)}>重试原消息（避免重复建单）</button>}</div>
      <div className="card"><h2>最近流水</h2>{transactions.length === 0 ? <p className="hint">暂无流水</p> : <ul className="history">{transactions.slice(0, 8).map(tx => {
        const outgoing = ['transfer_out', 'purchase', 'subscription', 'investment_purchase'].includes(tx.type);
        const label = tx.type === 'investment_purchase' ? '理财申购' : tx.type === 'investment_redemption' ? '理财赎回到账' : tx.type === 'transfer_out' ? '转出' : tx.type === 'transfer_in' ? '转入' : tx.type === 'subscription' ? '订阅扣费' : tx.type === 'purchase' ? '消费' : tx.type === 'refund' ? '退款' : tx.type === 'salary' ? '工资' : tx.type;
        const statusLabel = ({ completed: '已完成', pending: '处理中', failed: '失败', cancelled: '已取消' } as Record<string, string>)[tx.status] || tx.status;
        const counterparty = recipients.find(recipient => recipient.account_id === tx.counterparty)?.name || tx.counterparty;
        return <li key={tx.tx_id}><span>{label}{counterparty && ` · ${counterparty}`} {tx.note && `· ${tx.note}`}<small>{beijingTime(tx.timestamp)} · {statusLabel}</small></span><strong className={tx.status !== 'completed' ? 'hint' : outgoing ? 'out' : 'in'}>{outgoing ? '-' : '+'}¥{tx.amount.toFixed(2)}</strong></li>;
      })}</ul>}</div></section></>}
    {bankingSection === 'cards' && <><div className="page-intro"><p className="eyebrow">CARDS</p><h1>卡片控制，清楚而克制。</h1><p>低风险设置与高风险操作明确分层。</p></div><Suspense fallback={<section className="card"><p>正在加载卡片管理…</p></section>}><CardManagement key={user.user_id} accounts={accounts} revision={bankRevision} onActivity={load} /></Suspense></>}
    {bankingSection === 'subscriptions' && <><div className="page-intro"><p className="eyebrow">SUBSCRIPTIONS</p><h1>看清每一笔周期支出。</h1><p>取消前展示影响，历史流水不会被删除。</p></div><section className="card subscription-card">
      <h2>订阅管理</h2>
      {subscriptions.length === 0 ? <p className="hint">暂未识别到订阅扣费</p> : <ul className="history">{subscriptions.map(subscription => {
        const active = subscription.status === 'ACTIVE';
        return <li key={subscription.subscription_id}><span><strong>{subscription.merchant_name}</strong><small>{subscription.debit_account_label} · 每月 ¥{subscription.amount.toFixed(2)}</small><small>{active ? `下次扣费：${beijingTime(subscription.next_charge_at)}` : `已于 ${subscription.cancelled_at ? beijingTime(subscription.cancelled_at) : '-'} 取消`}</small>{subscription.source_transaction_id && <small>识别依据：历史流水 {subscription.source_transaction_id}</small>}</span><div className="subscription-action"><strong className={active ? 'in' : 'hint'}>{active ? '生效中' : '已取消'}</strong>{active ? <><button disabled={isActing} onClick={() => { setPendingSubscription(null); setPendingChargeSubscription(subscription); setSubscriptionChargeId(newId()); }}>模拟扣费</button><button className="secondary" disabled={isActing} onClick={async () => {
          setIsActing(true); setPendingSubscription(null); setSubscriptionCancellationToken('');
          setPendingChargeSubscription(null); setSubscriptionChargeId('');
          try {
            const preview = await bank.previewSubscriptionCancellation(subscription);
            setPendingSubscription(preview.subscription);
            setSubscriptionCancellationToken(preview.confirmation_token);
          } catch (error) { setStatus(message(error)); }
          finally { setIsActing(false); }
        }}>取消订阅</button></> : <small>不会继续扣费</small>}</div></li>;
      })}</ul>}
      {pendingSubscription && <div className="subscription-confirm"><p><strong>确认取消 {pendingSubscription.merchant_name}？</strong></p><p>扣费账户：{pendingSubscription.debit_account_label} · 每月 ¥{pendingSubscription.amount.toFixed(2)}<br />下次扣费：{beijingTime(pendingSubscription.next_charge_at)}</p><p className="hint">确认后将停止后续模拟自动扣费，历史扣费流水不会删除。</p><div className="confirm"><button className="secondary" disabled={isActing} onClick={() => { setPendingSubscription(null); setSubscriptionCancellationToken(''); }}>返回</button><button disabled={isActing} onClick={confirmSubscriptionCancellation}>{isActing ? '正在取消…' : '确认取消'}</button></div></div>}
      {pendingChargeSubscription && <div className="subscription-confirm"><p><strong>模拟扣除 {pendingChargeSubscription.merchant_name} ¥{pendingChargeSubscription.amount.toFixed(2)}？</strong></p><p className="hint">确认后将真实减少演示账户余额、生成订阅流水，并把下次扣费日推进一个月。</p><div className="confirm"><button className="secondary" disabled={isActing} onClick={() => { setPendingChargeSubscription(null); setSubscriptionChargeId(''); }}>返回</button><button disabled={isActing} onClick={confirmSubscriptionCharge}>{isActing ? '正在扣费…' : '确认模拟扣费'}</button></div></div>}
    </section></>}
    </>}
        </div>
      </div>
    </div>
    <NovaChat key={user.user_id} name={user.name} open={agentOpen} onClose={() => setAgentOpen(false)} onNavigate={section => {openSection(section); setAgentOpen(false)}} />
    {mfaOpen && <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }} onClick={() => { if (!isActing) { setMfaOpen(false); setMfaCode(''); } }}>
      <div className="card" style={{ width: 360, maxWidth: '90vw' }} onClick={e => e.stopPropagation()}>
        <h2>🔐 身份验证</h2>
        {activeTask && <SecondFactor purpose="transfer" referenceId={activeTask.task_id} factor={mfaFactor} onChange={setMfaFactor} disabled={isActing} />}
        <input inputMode="numeric" maxLength={6} value={mfaCode} placeholder="6 位验证码" onChange={e => setMfaCode(e.target.value.replace(/\D/g, '').slice(0, 6))} onKeyDown={e => e.key === 'Enter' && submitMfa()} />
        <div className="confirm">
          <button className="secondary" disabled={isActing} onClick={() => { setMfaOpen(false); setMfaCode(''); }}>取消</button>
          <button disabled={isActing || !mfaFactor || mfaCode.length !== 6} onClick={submitMfa}>{isActing ? '验证中…' : '提交验证'}</button>
        </div>
      </div>
    </div>}
    {enrollOpen && enrollData && <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }} onClick={() => { if (!isActing) { setEnrollOpen(false); setEnrollData(null); setEnrollCode(''); } }}>
      <div className="card" style={{ width: 400, maxWidth: '90vw' }} onClick={e => e.stopPropagation()}>
        <h2>🔐 绑定验证器</h2>
        <p className="hint">用 Google Authenticator 扫描二维码，或手动输入下方密钥，然后回填一次验证码完成绑定。</p>
        <div style={{ textAlign: 'center' }}><img src={enrollData.qr_data_uri} alt="TOTP 二维码" style={{ width: 200, height: 200 }} /></div>
        <p className="hint">手动输入密钥：<code>{enrollData.secret}</code></p>
        <input inputMode="numeric" maxLength={6} value={enrollCode} placeholder="输入 App 显示的 6 位验证码" onChange={e => setEnrollCode(e.target.value.replace(/\D/g, '').slice(0, 6))} onKeyDown={e => e.key === 'Enter' && submitEnroll()} />
        <div className="confirm">
          <button className="secondary" disabled={isActing} onClick={() => { setEnrollOpen(false); setEnrollData(null); setEnrollCode(''); }}>取消</button>
          <button disabled={isActing || enrollCode.length !== 6} onClick={submitEnroll}>{isActing ? '绑定中…' : '确认绑定'}</button>
        </div>
      </div>
    </div>}
  </main>;
}

export default App;
