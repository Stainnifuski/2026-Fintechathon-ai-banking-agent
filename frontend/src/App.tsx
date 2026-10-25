import {ManualTaskWorkspace} from './components/NovaTaskPresentation';
import { FormEvent, useCallback, lazy, Suspense, useEffect, useRef, useState } from 'react';
import * as bank from './services/bank';
import {manualDrafts} from './services/wealthManualDraft';
import {post as wealthPost} from './services/wealth';
import {AUTHENTICATION_EXPIRED_EVENT,authenticationExpiredMessage} from './utils/sessionAuthentication';
import BeijingClock from './components/BeijingClock';
import SecurityNotice from './components/SecurityNotice';
import TransferContacts, { type TransferChoice } from './components/TransferContacts';
import AddContactForm from './components/AddContactForm';
import AssetSummaryCard from './components/AssetSummary';
import {useAssetSummary} from './components/AssetSummary/useAssetSummary';
import NovaChat from './components/NovaChat';
import LoginPage from './components/LoginPage';
import FinancialOverview from './components/FinancialOverview';
import './homeWorkspace.css';
import './subscriptions.css';
import './cards.css';
import './transfers.css';
import {flushSync} from 'react-dom';
import type { NovaPageContext, NovaPromptRequest } from './services/nova';
import { SecondFactor } from './components/SecondFactor';
import { beijingTime, beijingInput, fromBeijingInput } from './utils/time';
import { counterpartyLabel } from './utils/customerLabels';

const Audit = lazy(() => import('./components/Audit'));
const BillAnalysis = lazy(() => import('./components/BillAnalysis'));
const Wealth = lazy(() => import('./components/Wealth'));
const PortfolioConsumerPreview = import.meta.env.DEV ? lazy(() => import('./components/Wealth/PortfolioConsumerPreview')) : null;
const wealthConsumerPreviewEnabled = import.meta.env.DEV && new URLSearchParams(window.location.search).get('wealth-dev') === '1';
const DemoTools = lazy(() => import('./components/Wealth/DemoTools'));
const CardManagement = lazy(() => import('./components/CardManagement'));
const ThirdPartyServices = lazy(() => import('./components/ThirdPartyServices'));
const MerchantDashboard = lazy(() => import('./components/MerchantDashboard'));

const newId = () => crypto.randomUUID?.() ?? `id-${Date.now()}-${Math.random()}`;
const localDateTimeInput = beijingInput;
const suggestedScheduleTime = () => localDateTimeInput(new Date(Date.now() + 2 * 60_000));
type BankingSection = 'home' | 'transfer' | 'insights' | 'cards' | 'subscriptions' | 'wealth' | 'audit';

function App() {
  const [showWealthPreview,setShowWealthPreview]=useState(wealthConsumerPreviewEnabled);
  const [user, setUser] = useState<bank.User | null>(() =>
    localStorage.getItem('token') ? JSON.parse(localStorage.getItem('bank-user') || 'null') : null
  );
  useEffect(()=>{window.dispatchEvent(new Event('nova-owner-change'));},[user?.user_id]);
  useEffect(()=>{const navigate=(event:Event)=>{const detail=(event as CustomEvent<{destination:string;service?:'flower'|'cake'}>).detail;const destination=detail.destination;setManualTab(destination==='calendar'?'calendar':detail.service);if(destination==='calendar'||destination==='thirdParty'){setAppView('thirdParty');}else openSection(destination as BankingSection);setAgentExpanded(false);};window.addEventListener('nova-manual-navigate',navigate);return()=>window.removeEventListener('nova-manual-navigate',navigate);},[]);
  const [manualTab,setManualTab]=useState<'calendar'|'flower'|'cake'|undefined>();
  const [loginMode, setLoginMode] = useState<'login' | 'register'>('login');
  const [phone, setPhone] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [accounts, setAccounts] = useState<bank.Account[]>([]);
  const [recipients, setRecipients] = useState<bank.Recipient[]>([]);
  const [transferHistoryRecipients, setTransferHistoryRecipients] = useState<bank.TransferHistoryRecipient[]>([]);
  const [transactions, setTransactions] = useState<bank.Transaction[]>([]);
  const [bankRevision, setBankRevision] = useState(0);
  const [novaPrompts, setNovaPrompts] = useState<NovaPromptRequest[]>([]);
  useEffect(() => { setNovaPrompts([]); }, [user?.user_id]);
  const [wealthWorkspaceEpoch,setWealthWorkspaceEpoch]=useState(0);
  const [workspaceReady,setWorkspaceReady]=useState<string|null>(null);
  const [workspaceError,setWorkspaceError]=useState('');
  const [workspaceRetry,setWorkspaceRetry]=useState(0);
  const workspaceBoot=useRef<{owner:string;promise:Promise<unknown>}|null>(null);
  useEffect(()=>{
    let live=true;setWorkspaceReady(null);setWorkspaceError('');
    if(!user){workspaceBoot.current=null;return;}
    const owner=user.user_id;
    if(workspaceBoot.current?.owner!==owner){
      manualDrafts.clear();
      workspaceBoot.current={owner,promise:wealthPost('/workspace/reset',{})};
    }
    void workspaceBoot.current.promise.then(()=>{if(live)setWorkspaceReady(owner);}).catch(()=>{
      if(live)setWorkspaceError('新操作轮次尚未准备完成，请点击重试；已受理交易保持不变。');
    });
    return()=>{live=false;};
  },[user?.user_id,workspaceRetry]);
  const [paymentRequests, setPaymentRequests] = useState<bank.PaymentRequest[]>([]);
  const [subscriptions, setSubscriptions] = useState<bank.Subscription[]>([]);
  const [showCancelledSubscriptions, setShowCancelledSubscriptions] = useState(false);
  const activeSubscriptions = subscriptions.filter(item => item.status === 'ACTIVE');
  const cancelledSubscriptions = subscriptions.filter(item => item.status === 'CANCELLED');
  const [pendingSubscription, setPendingSubscription] = useState<bank.Subscription | null>(null);
  const [pendingChargeSubscription, setPendingChargeSubscription] = useState<bank.Subscription | null>(null);
  const [subscriptionCancellationToken, setSubscriptionCancellationToken] = useState('');
  const [subscriptionChargeId, setSubscriptionChargeId] = useState('');
  const [fromAccount, setFromAccount] = useState('');
  const [recipientName, setRecipientName] = useState('');
  const [recipientMode, setRecipientMode] = useState<'card' | 'phone'>('card');
  const [recipientNumbers, setRecipientNumbers] = useState({ card: '', phone: '' });
  const [recipientAccountIds, setRecipientAccountIds] = useState({ card: '', phone: '' });
  const [recipientPanel, setRecipientPanel] = useState<'people' | 'settings' | null>(null);
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
  const [bankingSection, setBankingSection] = useState<BankingSection>(wealthConsumerPreviewEnabled ? 'wealth' : 'home');
  const [wealthTestMode, setWealthTestMode] = useState(false);
  const [agentOpen, setAgentOpen] = useState(() => window.matchMedia('(min-width: 1101px)').matches);
  const [agentExpanded, setAgentExpanded] = useState(!wealthConsumerPreviewEnabled);
  const modeTransition = useRef(false);
  const [desktopLayout,setDesktopLayout]=useState(()=>window.matchMedia('(min-width: 1101px)').matches);
  const [novaControls,setNovaControls]=useState<HTMLDivElement|null>(null);
  useEffect(()=>{const media=window.matchMedia('(min-width: 1101px)'); const update=()=>setDesktopLayout(media.matches); media.addEventListener('change',update); return()=>media.removeEventListener('change',update);},[]);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [overviewError, setOverviewError] = useState('');
  const [selectedCardId, setSelectedCardId] = useState<string>();
  const [pendingCardOperationId, setPendingCardOperationId] = useState<string>();
  const [selectedSubscriptionId, setSelectedSubscriptionId] = useState<string>();
  useEffect(() => {
    setSelectedSubscriptionId(current => subscriptions.some(item => item.subscription_id === current && item.status === 'ACTIVE') ? current : undefined);
  }, [subscriptions]);
  useEffect(() => { setShowCancelledSubscriptions(false); }, [user?.user_id]);
  const [billPageContext, setBillPageContext] = useState<NovaPageContext>({ version: 1, page: 'insights' });
  const [thirdPartyPageContext, setThirdPartyPageContext] = useState<NovaPageContext>({ version: 1, page: 'thirdParty' });
  const updateThirdPartyPageContext = useCallback((context: NovaPageContext) => setThirdPartyPageContext(context), []);
  const updateBillPageContext = useCallback((context: NovaPageContext) => setBillPageContext(context), []);
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
  const assetSummary=useAssetSummary(user&&!['U007','U008'].includes(user.user_id)?user.user_id:null,bankRevision,
    appView==='banking'&&['home','wealth'].includes(bankingSection)?bankingSection:null);
  const bankShellRef = useRef<HTMLDivElement>(null);

  const message = (error: unknown) => {
    const detail = (error as { response?: { data?: { detail?: string | { message?: string; capability?: string } } } })?.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (detail?.message) return detail.message;
    if (detail?.capability) return `${detail.capability} 暂未实现`;
    return error instanceof Error ? error.message : '请求失败，请稍后重试';
  };

  const load = async () => {
    if (!user) return;
    setOverviewLoading(true); setOverviewError('');
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
      const [a, r, h, requests, subscriptionItems, recentRecipients] = await Promise.all([bank.accounts(user.user_id), bank.recipients(), bank.history(), bank.paymentRequests(), bank.listSubscriptions(), bank.transferRecipientHistory()]);
      setAccounts(a); setRecipients(r); setTransferHistoryRecipients(recentRecipients); setTransactions(h); setPaymentRequests(requests); setSubscriptions(subscriptionItems);
      setBankRevision(value => value + 1);
      const defaultFrom = a.find(acc => acc.account_type === 'savings')?.account_id || a[0]?.account_id || '';
      setFromAccount(current => current && a.some(acc => acc.account_id === current) ? current : defaultFrom);
      setRequestPayerIds(current => current.filter(id => r.some(rec => rec.user_id === id) || recentRecipients.some(rec => rec.user_id === id)));
      setRequestAccountId(current => current && a.some(acc => acc.account_id === current) ? current : defaultFrom);
      bank.totpStatus().then(s => setTotpEnabled(s.enabled)).catch(() => {});
    } catch (error) {
      if (!localStorage.getItem('token')) {
        logout(); setStatus('登录已过期，请重新登录');
      } else { setStatus(message(error)); setOverviewError('账户与订阅读取失败，请重试。'); }
    } finally { setOverviewLoading(false); }
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

  const authenticationPending = useRef(false);
  const authenticate = async (event: FormEvent) => {
    event.preventDefault();
    if (authenticationPending.current) return;
    authenticationPending.current = true; setStatus(''); setIsAuthenticating(true);
    try {
      const result = loginMode === 'login' ? await bank.login(phone, password) : await bank.register(name, phone, password);
      localStorage.setItem('token', result.access_token);
      localStorage.setItem('bank-user', JSON.stringify(result.user));
      setUser(result.user);
    } catch (error) { setStatus(`${loginMode === 'login' ? '登录' : '注册'}失败：${message(error)}`); }
    finally { authenticationPending.current = false; setIsAuthenticating(false); }
  };

  const submitTransfer = async (event: FormEvent) => {
    event.preventDefault(); setStatus('');
    if (!fromAccount) return setStatus('请选择付款账户');
    if (transferMode === 'scheduled') {
      if (!scheduledAt) return setStatus('请选择定时执行时间');
      if (new Date(fromBeijingInput(scheduledAt)).getTime() <= Date.now()) return setStatus('定时执行时间必须晚于当前时间');
    }
    setIsActing(true);
    try {
      const choice = await bank.resolveTransferRecipient(recipientName.trim(), recipientMode,
        recipientNumbers[recipientMode].trim(), recipientAccountIds[recipientMode]);
      const task = await bank.createTransferTask({
        request_id: newId(), operation_id: newId(), from_account: fromAccount,
        to_account: choice.account_id,
        recipient_phone: recipientMode === 'phone' && recipientNumbers.phone.trim()
          ? recipientNumbers.phone.trim() : undefined,
        recipient_card_number: recipientMode === 'card' && recipientNumbers.card.trim()
          ? recipientNumbers.card.trim() : undefined,
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
    finally { setIsActing(false); }
  };

  const chooseTransferRecipient = (choice: TransferChoice) => {
    setRecipientName(choice.name);
    setRecipientMode(choice.cardNumber ? 'card' : 'phone');
    setRecipientNumbers({ card: choice.cardNumber || '', phone: choice.phoneNumber || '' });
    setRecipientAccountIds({ card: choice.cardAccountId || choice.accountId,
      phone: choice.phoneAccountId || choice.accountId });
    setRecipientPanel(null); setStatus('');
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

  const reviewBillSubscription = async (id: string) => {
    if (isActing) return;
    setIsActing(true); setStatus('');
    try {
      const subscription = subscriptions.find(item => item.subscription_id === id) || (await bank.listSubscriptions()).find(item => item.subscription_id === id);
      if (!subscription) throw new Error('订阅已变化，请刷新后重试');
      const preview = await bank.previewSubscriptionCancellation(subscription);
      openSection('subscriptions');
      setSelectedSubscriptionId(id);
      setPendingChargeSubscription(null); setPendingSubscription(preview.subscription);
      setSubscriptionCancellationToken(preview.confirmation_token);
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
      await load();
    } catch (error) { setStatus(message(error)); }
    finally { setIsActing(false); }
  };

  const restoreSubscriptionForTesting = async (subscription: bank.Subscription) => {
    if (isActing) return;
    setIsActing(true); setStatus('');
    try {
      const updated = await bank.restoreSubscriptionForTesting(subscription);
      setSubscriptions(items => items.map(item => item.subscription_id === updated.subscription_id ? updated : item));
      setStatus(`测试专用：已恢复 ${updated.merchant_name} 订阅，未扣款，计划扣费日期保持不变`);
      await load();
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
    manualDrafts.clear();
    localStorage.removeItem('token'); localStorage.removeItem('bank-user');
    setUser(null); setAccounts([]); setRecipients([]); setTransferHistoryRecipients([]); setTransactions([]); setPaymentRequests([]); setSubscriptions([]); setPendingSubscription(null); setSubscriptionCancellationToken(''); setPendingChargeSubscription(null); setSubscriptionChargeId('');
    setChat([]); setActiveTask(null); setConversation(null); setRetryChat(null); setStatus('');
    setFromAccount(''); setRecipientName(''); setRequestPayerIds([]); setRequestAccountId(''); setScheduledAt('');
    setRecipientMode('card'); setRecipientNumbers({ card: '', phone: '' }); setRecipientAccountIds({ card: '', phone: '' }); setRecipientPanel(null);
    setAmount(''); setNote(''); setEditingNote(null); setEditNoteCleared(false); setTransferMode('instant');
    setRequestAmount(''); setRequestNote(''); setSplitEqually(false); setChatInput('');
    setTotpEnabled(false); setMfaOpen(false); setMfaCode(''); setEnrollOpen(false); setEnrollData(null); setEnrollCode('');
    setSelectedCardId(undefined); setSelectedSubscriptionId(undefined);
    setBillPageContext({ version: 1, page: 'insights' });
    setThirdPartyPageContext({ version: 1, page: 'thirdParty' });
    setAppView('banking'); setBankingSection('home'); setWealthTestMode(false); setAgentOpen(window.matchMedia('(min-width: 1101px)').matches); setAgentExpanded(true); setHighlightedPaymentRequest('');
  };

  useEffect(()=>{
    const expired=()=>{logout();setStatus(authenticationExpiredMessage);};
    window.addEventListener(AUTHENTICATION_EXPIRED_EVENT,expired);
    return()=>window.removeEventListener(AUTHENTICATION_EXPIRED_EVENT,expired);
  },[]);

  const goToBankPayment = async (paymentRequestId?: string) => {
    setHighlightedPaymentRequest(paymentRequestId || '');
    setAppView('banking');
    setBankingSection('transfer'); setAgentExpanded(false);
    await load();
    window.setTimeout(() => document.getElementById('payment-requests')?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50);
  };

  const requestAmountNumber = Number(requestAmount) || 0;
  const requestRecipients = [
    ...recipients.map(item => ({ user_id: item.user_id, name: item.name, label: item.nickname || item.name, number: item.phone ? `手机号 ${item.phone}` : item.card_masked ? `银行卡尾号 ${item.card_masked.slice(-4)}` : '', saved: true })),
    ...transferHistoryRecipients.filter(item => !recipients.some(saved => saved.user_id === item.user_id))
      .map(item => ({ user_id: item.user_id, name: item.name, label: item.name, number: item.phone_masked ? `手机号 ${item.phone_masked}` : item.card_number ? `银行卡尾号 ${item.card_number.slice(-4)}` : '', saved: false })),
  ];
  const splitIsExact = requestPayerIds.length > 0
    && Math.round(requestAmountNumber * 100) % requestPayerIds.length === 0;
  const perPersonAmount = splitEqually && requestPayerIds.length > 0
    ? requestAmountNumber / requestPayerIds.length
    : requestAmountNumber;
  const requestTotal = splitEqually
    ? requestAmountNumber
    : requestAmountNumber * requestPayerIds.length;

  const openSection = (section: BankingSection) => {
    if (bankingSection !== section || appView !== 'banking') {
      setSelectedCardId(undefined); setSelectedSubscriptionId(undefined);
      if (section === 'insights') setBillPageContext({ version: 1, page: 'insights' });
    }
    setAppView('banking'); setBankingSection(section); setAgentExpanded(false);
    bankShellRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };
  const switchAgentMode = () => {
    if (!window.matchMedia('(min-width: 1101px)').matches) { setAgentOpen(value => !value); return; }
    if (modeTransition.current) return;
    const change = () => flushSync(() => { setAgentOpen(true); setAgentExpanded(!agentOpen || !agentExpanded); });
    const transitionDocument = document as Document & {startViewTransition?: (update: () => void) => {finished: Promise<void>}};
    if (transitionDocument.startViewTransition && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      modeTransition.current = true;
      void transitionDocument.startViewTransition(change).finished.catch(() => {}).finally(() => { modeTransition.current = false; });
    } else change();
  };
  const reviewCardOperation = (operationId: string) => { openSection('cards'); setPendingCardOperationId(operationId); };

  if (!user) return <LoginPage mode={loginMode} phone={phone} password={password} name={name}
    busy={isAuthenticating} status={status} onPhone={setPhone} onPassword={setPassword} onName={setName}
    onSubmit={authenticate} onMode={() => { setLoginMode(loginMode === 'login' ? 'register' : 'login'); setStatus(''); }} />;

  if (['U007', 'U008'].includes(user.user_id)) return <><BeijingClock /><button className="secondary" onClick={() => setBankingSection(section => section === 'audit' ? 'home' : 'audit')}>{bankingSection === 'audit' ? '返回商户工作台' : '操作记录'}</button><Suspense fallback={<main className="auth"><section className="card"><p>正在加载商户工作台…</p></section></main>}>
    {bankingSection === 'audit' ? <Audit key={user.user_id} /> : <MerchantDashboard user={user} accounts={accounts} transactions={transactions} revision={bankRevision} onRefresh={load} onLogout={logout} />}
  </Suspense></>;

  const confirmationCard = activeTask?.confirmation_card;
  const payerCardTail = confirmationCard?.payer_card_masked?.slice(-4)
    || accounts.find(account => account.account_id === confirmationCard?.from_account)?.transfer_card_number?.slice(-4);

  return <main className={`app prototype-app home-workspace${agentOpen ? ' nova-open' : ''}${agentOpen && agentExpanded ? ' nova-expanded' : ' nova-docked'}`}>
    <div className="bank-shell" ref={bankShellRef}>
      <aside className="bank-sidebar">
        <button className="brand" onClick={() => openSection('home')} aria-label="返回首页"><span className="brand-mark">N</span><span>NOVA<small>AI BANKING</small></span></button>
        <nav aria-label="主要功能">
          {([['home','⌂','概览'],['transfer','↗','转账与收款'],['insights','◫','账单洞察'],['cards','▣','卡片'],['subscriptions','↻','订阅'],['wealth','◇','理财']] as [BankingSection,string,string][]).map(([key,icon,label]) =>
            <button key={key} data-destination={key} className={appView === 'banking' && bankingSection === key ? 'active' : ''} onClick={() => openSection(key)}><span>{icon}</span>{label}</button>)}
          <button data-destination="services" className={appView === 'thirdParty' ? 'active' : ''} onClick={() => { if (appView !== 'thirdParty') setThirdPartyPageContext({ version: 1, page: 'thirdParty' }); setAppView('thirdParty'); setAgentExpanded(false); }}><span>＋</span>生活服务</button>
        </nav>
        <div className="sidebar-bottom">
          <button className={`sidebar-audit-button ${appView === 'banking' && bankingSection === 'audit' ? 'active' : ''}`} data-destination="audit" onClick={() => openSection('audit')}><span>☷</span>操作记录</button>
          <div className="sidebar-foot"><span className="security-dot" />模拟银行演示<small>所有资金与交易均为虚构</small></div>
        </div>
      </aside>
      <div className="bank-workspace">
        <header className="bank-topbar"><div><BeijingClock /><span className="workspace-label">{desktopLayout && agentOpen && agentExpanded ? 'NOVA 银行智能体' : appView === 'thirdParty' ? '生活服务' : ({home:'财务概览',transfer:'转账与收款',insights:'账单洞察',cards:'卡片管理',subscriptions:'订阅管理',wealth:'理财规划'} as Record<BankingSection,string>)[bankingSection]}</span>{desktopLayout&&agentOpen&&agentExpanded&&(bankingSection!=='home'||appView==='thirdParty')&&<small className="nova-reference-context">参考工作台：{appView==='thirdParty'?'生活服务':({transfer:'转账与收款',insights:'账单洞察',cards:'卡片管理',subscriptions:'订阅管理',wealth:'理财规划'} as Partial<Record<BankingSection,string>>)[bankingSection]}</small>}</div><div className="top-actions"><div className="nova-header-slot" ref={setNovaControls} /><button id="nova-toggle" className="agent-button" aria-expanded={desktopLayout ? agentExpanded && agentOpen : agentOpen} aria-controls="nova-panel" title={desktopLayout ? (agentExpanded && agentOpen ? '切换为侧边对话，查看财务概览' : '展开 NOVA 对话') : (agentOpen ? '收起 NOVA 对话' : '打开 NOVA 对话')} onClick={switchAgentMode}>{(desktopLayout ? agentExpanded && agentOpen : agentOpen) ? '收起 NOVA' : '展开 NOVA'}</button><button className="icon-button auth-enroll-button" disabled={isActing} onClick={openEnroll} aria-label={totpEnabled ? '已绑定验证器' : '绑定验证器'} title={totpEnabled ? '已绑定验证器' : '绑定验证器'}>⌾</button><button className="profile-button" onClick={logout} title="退出登录">{user.name.slice(0,1)}</button></div></header>
        <SecurityNotice userId={user.user_id} />
        {status && <p className="notice global-notice">{status}</p>}
        <div className="workspace-content"><ManualTaskWorkspace destination={appView==='thirdParty'?(thirdPartyPageContext.calendar?'calendar':'thirdParty'):bankingSection}/>
        {appView==='banking'&&bankingSection==='wealth'&&import.meta.env.DEV&&import.meta.env.VITE_B6_BROWSER_TEST==='true'&&<p className="notice" role="status">B6 隔离验收：固定契约方案＋独立模拟账户。不是模型建议，与日常演示账户无关；测试业务时钟与现实时间分开。</p>}
        {appView === 'thirdParty' ? <Suspense fallback={<section className="card"><p>正在加载第三方服务…</p></section>}>
          <ThirdPartyServices requestedTab={manualTab} onContextChange={updateThirdPartyPageContext} revision={bankRevision} onBack={() => openSection('home')} onGoBank={goToBankPayment} />
        </Suspense> : <>
        {bankingSection === 'home' && <FinancialOverview userId={user.user_id} revision={bankRevision} accounts={accounts} subscriptions={subscriptions} loading={overviewLoading} error={overviewError} onRetry={() => void load()} assetSummary={assetSummary} onNavigate={openSection} />}
        {bankingSection === 'wealth' && <AssetSummaryCard {...assetSummary} onRefresh={assetSummary.refresh} />}
        {bankingSection === 'wealth' && showWealthPreview && PortfolioConsumerPreview && <Suspense fallback={<section className="card">正在加载开发预览…</section>}><PortfolioConsumerPreview onReturn={()=>setShowWealthPreview(false)} accounts={accounts} revision={bankRevision} onActivity={load} userId={user.user_id} /></Suspense>}
        {bankingSection === 'wealth' && !showWealthPreview && <>{wealthConsumerPreviewEnabled&&<button className="secondary" onClick={()=>setShowWealthPreview(true)}>返回理财开发预览（保留输入）</button>}<div className="page-intro wealth-intro"><div><p className="eyebrow">WEALTH</p><h1>理财规划与购买</h1><p>选择整体规划或目标产品，核对方案后确认并完成验证。</p></div><button className={wealthTestMode ? 'secondary' : 'test-mode-button'} onClick={() => setWealthTestMode(value => !value)}>{wealthTestMode ? '退出测试模式' : '进入测试模式'}</button></div>{wealthTestMode && <><p className="notice wealth-test-notice">测试模式已开启：下方保留团队原有的理财工作台与模拟测试工具。重置资金或删除旧轮次仍需单独确认。</p><Suspense fallback={null}><DemoTools key={user.user_id} userId={user.user_id} revision={bankRevision} changed={async()=>{setWealthWorkspaceEpoch(value=>value+1);await load();}} /></Suspense></>}<Suspense fallback={<section className="card"><p>正在加载理财模拟…</p></section>}>{workspaceReady===user.user_id?<Wealth key={`${user.user_id}:${wealthWorkspaceEpoch}`} userId={user.user_id} accounts={accounts} revision={bankRevision} onActivity={load} />:<section className="card">{workspaceError?<><p role="alert">{workspaceError}</p><button onClick={()=>{workspaceBoot.current=null;setWorkspaceRetry(n=>n+1);}}>重新开启操作轮次</button></>:<p role="status">正在开启新的理财操作轮次…</p>}</section>}</Suspense></>}
        {bankingSection === 'audit' && <Suspense fallback={<section className="card"><p>正在加载操作记录…</p></section>}><Audit key={user.user_id} /></Suspense>}
        {bankingSection === 'insights' && <><div className="page-intro bills-page-intro"><p className="eyebrow">INSIGHTS</p><h1>查看支出与变化</h1><p>查看消费与周期收支，按自己的标签整理账单。</p></div><Suspense fallback={<section className="card"><p>正在加载账单分析…</p></section>}><BillAnalysis key={user.user_id} accounts={accounts} revision={bankRevision} onViewCashflows={() => openSection('transfer')} onContextChange={updateBillPageContext} onManageSubscription={reviewBillSubscription} onOpenNova={() => {setAgentOpen(true);setAgentExpanded(false);}} onAskNova={(text, context) => { setNovaPrompts(items => [...items,{id:crypto.randomUUID(),text,context,userId:user.user_id}]); setAgentOpen(true); setAgentExpanded(false); }} /></Suspense></>}

        {bankingSection === 'transfer' && <div className="transfers-page"><div className="page-intro transfers-page-intro"><p className="eyebrow">MOVE MONEY</p><h1>转账与收款</h1><p>先生成确认信息；高风险操作仍需要本人验证。</p></div><section className="grid">
      <div className="card transfer-accounts"><h2>我的账户</h2>{accounts.map(a => <div className="account" key={a.account_id}><span>{a.account_type === 'savings' ? '活期账户' : a.account_type} · {a.card_number}</span>{a.transfer_card_number && <small>模拟收款卡号：{a.transfer_card_number}</small>}<strong>¥{a.balance.toFixed(2)}</strong>{a.available_fen != null && <small>可用 ¥{(a.available_fen / 100).toFixed(2)} · 占款 ¥{((a.held_fen || 0) / 100).toFixed(2)}</small>}<small>今日已转 ¥{a.daily_transfer_used.toFixed(2)} / ¥{a.daily_transfer_limit.toFixed(2)}</small></div>)}</div>
      <div className="card transfer-compose"><div className="transfer-heading"><h2>创建转账任务</h2><div className="transfer-heading-actions"><button type="button" className="secondary" aria-label="转账人" title="转账人" aria-expanded={recipientPanel === 'people'} onClick={() => setRecipientPanel(current => current === 'people' ? null : 'people')}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="8" r="4" /><path d="M4 21v-2a8 8 0 0 1 16 0v2" /></svg></button><button type="button" className="secondary" aria-label="转账人设置" title="转账人设置" aria-expanded={recipientPanel === 'settings'} onClick={() => setRecipientPanel(current => current === 'settings' ? null : 'settings')}>⚙</button></div></div>
      {recipientPanel && <TransferContacts key={user.user_id} userId={user.user_id} mode={recipientPanel} onChoose={chooseTransferRecipient} onChanged={load} />}
      <form onSubmit={submitTransfer}>
        <label>付款账户<select value={fromAccount} onChange={e => setFromAccount(e.target.value)}>{accounts.map(a => <option value={a.account_id} key={a.account_id}>{a.card_number}（¥{a.balance.toFixed(2)}）</option>)}</select></label>
        <label>收款人户名或备注名<input required maxLength={64} value={recipientName} onChange={e => { setRecipientName(e.target.value); setRecipientAccountIds({ card: '', phone: '' }); }} placeholder="输入真实户名或已保存的备注名" /></label>
        <label>收款账户<select value={recipientMode} onChange={e => setRecipientMode(e.target.value as 'card' | 'phone')}><option value="card">银行卡号</option><option value="phone">手机号</option></select></label>
        <label>{recipientMode === 'card' ? '银行卡号（使用已保存备注名时可留空）' : '手机号'}<input inputMode="numeric" autoComplete="off" value={recipientNumbers[recipientMode]} onChange={e => { setRecipientNumbers(current => ({ ...current, [recipientMode]: e.target.value })); setRecipientAccountIds(current => ({ ...current, [recipientMode]: '' })); }} placeholder={recipientMode === 'card' ? '请输入完整模拟银行卡号' : '请输入完整手机号'} /></label>
        <label>金额（元）<input required min="0.01" step="0.01" type="number" value={amount} onChange={e => setAmount(e.target.value)} /></label>
        <label>备注（可选）<input maxLength={256} value={note} onChange={e => setNote(e.target.value)} /></label>
        <label>执行方式<select value={transferMode} onChange={e => { const mode = e.target.value as 'instant' | 'scheduled'; setTransferMode(mode); if (mode === 'scheduled' && !scheduledAt) setScheduledAt(suggestedScheduleTime()); }}><option value="instant">立即转账</option><option value="scheduled">定时转账</option></select></label>
        {transferMode === 'scheduled' && <><label>执行时间<input required min={localDateTimeInput(new Date())} type="datetime-local" value={scheduledAt} onChange={e => setScheduledAt(e.target.value)} /><small>点击右侧日历图标选择日期和时间，例如：2026/09/26 21:31。系统按北京时间执行。</small></label><button type="button" className="secondary" onClick={() => setScheduledAt(suggestedScheduleTime())}>快速设置为约 2 分钟后</button></>}
        <button disabled={!fromAccount || isActing}>生成确认信息</button>
      </form></div>
    </section>
    <section className="grid">
      <div className="card transfer-request-create"><h2>发起收款申请</h2><details className="add-contact-details"><summary>＋ 添加联系人</summary><AddContactForm onAdded={load} /></details><form onSubmit={submitPaymentRequest}>
        <label>向谁收款（可多选）<details className="multi-select" ref={payerSelectorRef}><summary>{requestPayerIds.length ? `已选择 ${requestPayerIds.length} 人：${requestRecipients.filter(r => requestPayerIds.includes(r.user_id)).map(r => r.label).join('、')}` : '请选择联系人'}</summary><div>{requestRecipients.length === 0 && <p className="hint">暂无联系人或历史转账人</p>}{requestRecipients.map(r => <label className="check-option" key={r.user_id}><input type="checkbox" checked={requestPayerIds.includes(r.user_id)} onChange={e => setRequestPayerIds(ids => e.target.checked ? [...ids, r.user_id] : ids.filter(id => id !== r.user_id))} />{r.label}{r.label !== r.name ? `（${r.name}）` : ''}{r.number && ` · ${r.number}`}{!r.saved && ' · 历史转账人'}</label>)}</div></details></label>
        <label>收款账户<select value={requestAccountId} onChange={e => setRequestAccountId(e.target.value)}>{accounts.map(a => <option value={a.account_id} key={a.account_id}>{a.card_number}</option>)}</select></label>
        <label>{splitEqually ? 'AA 总金额（元）' : '每人收款金额（元）'}<input required min="0.01" step="0.01" type="number" value={requestAmount} onChange={e => setRequestAmount(e.target.value)} /></label>
        <button type="button" className={splitEqually ? '' : 'secondary'} onClick={() => setSplitEqually(value => !value)}>AA 均分：{splitEqually ? '已开启' : '未开启'}</button>
        {requestPayerIds.length > 0 && requestAmountNumber > 0 && <p className={splitEqually && !splitIsExact ? 'error' : 'notice'}>{splitEqually && !splitIsExact ? '该金额无法精确均分到分，请调整金额' : `将向每人收款 ¥${perPersonAmount.toFixed(2)}，共 ${requestPayerIds.length} 人，总申请金额 ¥${requestTotal.toFixed(2)}`}</p>}
        <label>备注（可选）<input maxLength={256} value={requestNote} onChange={e => setRequestNote(e.target.value)} /></label>
        <button disabled={requestPayerIds.length === 0 || !requestAccountId || (splitEqually && !splitIsExact) || isActing}>发出申请</button>
      </form></div>
      <div className="card transfer-request-list" id="payment-requests"><h2>收款申请</h2>{paymentRequests.some(request => ['PENDING', 'ACCEPTED'].includes(request.status) && request.payer_user_id === user.user_id) && <label>付款账户<select value={fromAccount} onChange={e => setFromAccount(e.target.value)}>{accounts.map(a => <option value={a.account_id} key={a.account_id}>{a.card_number}（¥{a.balance.toFixed(2)}）</option>)}</select></label>}{paymentRequests.length === 0 ? <p className="hint">暂无收款申请</p> : <ul className="history">{paymentRequests.map(request => {
        const incoming = request.payer_user_id === user.user_id;
        const canPay = incoming && ['PENDING', 'ACCEPTED'].includes(request.status);
        return <li className={highlightedPaymentRequest === request.payment_request_id ? 'payment-request-highlight' : ''} key={request.payment_request_id}><span>{incoming ? `${request.requester_name} 向你收款` : `向 ${request.payer_name} 收款`} {request.note && `· ${request.note}`}<small>{request.status} · {beijingTime(request.created_at)}</small></span><strong>¥{request.amount.toFixed(2)}</strong>{(canPay || request.status === 'PENDING') && <div className="confirm">{canPay ? <><button disabled={isActing} onClick={() => actOnPaymentRequest(request, 'accept')}>{request.status === 'ACCEPTED' ? '确认付款' : '接受申请'}</button>{request.status === 'PENDING' && <button className="secondary" disabled={isActing} onClick={() => actOnPaymentRequest(request, 'reject')}>拒绝</button>}</> : <button className="secondary" disabled={isActing} onClick={() => actOnPaymentRequest(request, 'cancel')}>取消</button>}</div>}</li>;
      })}</ul>}<p className="hint">接受申请后先核对转账任务，再确认付款；需要强验证的任务会停留在身份验证阶段。</p></div>
    </section>
    {activeTask && <section className="card task-focus transfer-task">
      <h2>转账任务</h2><p>{activeTask.message}</p>
      {confirmationCard && <p>从{payerCardTail ? `尾号 ${payerCardTail} 的银行卡` : '付款银行卡'}向 {confirmationCard.recipient_name}（{confirmationCard.recipient_phone_masked
        ? `手机号 ${confirmationCard.recipient_phone_masked}`
        : confirmationCard.recipient_card_masked
          ? `银行卡尾号 ${confirmationCard.recipient_card_masked.slice(-4)}`
          : '收款账户'}）转账 <strong>¥{confirmationCard.amount.toFixed(2)}</strong> · 备注：{confirmationCard.note ?? '无'} · 风险级别 {confirmationCard.risk_level}</p>}
      {activeTask.confirmation_card && activeTask.allowed_actions.includes('modify') && conversation?.task?.task_id !== activeTask.task_id && <>
        {editingNote === null
          ? <button className="secondary" onClick={() => { setEditingNote(activeTask.confirmation_card?.note || ''); setEditNoteCleared(activeTask.confirmation_card?.note_source === 'cleared'); }}>修改或清空备注</button>
          : <div className="row"><input maxLength={256} disabled={editNoteCleared} value={editingNote} onChange={e => setEditingNote(e.target.value)} /><label><input type="checkbox" checked={editNoteCleared} onChange={e => setEditNoteCleared(e.target.checked)} />清空</label><button disabled={isActing || (!editNoteCleared && !editingNote.trim())} onClick={saveEditedNote}>保存并重新确认</button><button className="secondary" onClick={() => setEditingNote(null)}>返回</button></div>}
      </>}
      {activeTask.scheduled_at && <p className="notice">计划执行时间：{beijingTime(activeTask.scheduled_at)}</p>}
      <p className="hint">{activeTask.phase === 'FINISHED' ? '银行处理已结束' : '银行处理中或待本人确认'} · 最终备注：{activeTask.operation.note ?? '无'}</p>
      <div className="confirm">
        {activeTask.allowed_actions.includes('cancel') && <button className="secondary" disabled={isActing || isChatting || (conversation?.task?.task_id === activeTask.task_id && conversation.kind === 'clarification')} onClick={() => actOnTask('cancel')}>取消任务</button>}
        {activeTask.allowed_actions.includes('confirm') && <button disabled={isActing || isChatting || (conversation?.task?.task_id === activeTask.task_id && conversation.kind === 'clarification')} onClick={() => actOnTask('confirm')}>{activeTask.scheduled_at ? '确认定时转账' : '确认并提交'}</button>}
        {activeTask.allowed_actions.includes('verify') && <button disabled={isActing || isChatting || (conversation?.task?.task_id === activeTask.task_id && conversation.kind === 'clarification')} onClick={() => { setMfaCode(''); setMfaFactor({ method: 'totp' }); setMfaOpen(true); }}>身份验证</button>}
        {activeTask.allowed_actions.includes('reconcile') && <button disabled={isActing || isChatting || (conversation?.task?.task_id === activeTask.task_id && conversation.kind === 'clarification')} onClick={() => actOnTask('reconcile')}>核对银行结果</button>}
      </div>
    </section>}
    <section className="grid"><div className="card transfer-dialogue"><h2>即时转账对话</h2><p className="hint">例如：给手机号13800000002转300元，备注房租；改成500元；清空备注。当前模式：{conversation?.mode === 'model' ? '模型理解' : '离线规则（有限表达）'}。</p><button className="secondary" disabled={isChatting || !!retryChat} onClick={newConversation}>新建转账会话</button><div className="chat">{chat.map((item,index)=><p key={index}>{item}</p>)}</div>{conversation?.candidates.map(candidate=><button key={candidate.candidate_id} disabled={isChatting || !!retryChat} onClick={()=>sendChat(candidate.candidate_id)}>{candidate.label}</button>)}<div className="row"><input value={chatInput} disabled={isChatting || !!retryChat} onChange={e=>setChatInput(e.target.value)} onKeyDown={e=>e.key==='Enter'&&!retryChat&&sendChat()} placeholder="给手机号13800000002转300元，备注聚餐"/><button disabled={isChatting || !!retryChat} onClick={()=>sendChat()}>发送</button></div>{retryChat&&!isChatting&&<button onClick={()=>sendChat(undefined,true)}>重试原消息（避免重复建单）</button>}</div>
      <div className="card transfer-ledger"><h2>最近流水</h2>{transactions.length === 0 ? <p className="hint">暂无流水</p> : <ul className="history">{transactions.slice(0, 8).map(tx => {
        const outgoing = ['transfer_out', 'purchase', 'subscription', 'investment_purchase'].includes(tx.type);
        const label = tx.type === 'investment_purchase' ? '理财申购' : tx.type === 'investment_redemption' ? '理财赎回到账' : tx.type === 'transfer_out' ? '转出' : tx.type === 'transfer_in' ? '转入' : tx.type === 'subscription' ? '订阅扣费' : tx.type === 'purchase' ? '消费' : tx.type === 'refund' ? '退款' : tx.type === 'salary' ? '工资' : tx.type;
        const statusLabel = ({ completed: '已完成', pending: '处理中', failed: '失败', cancelled: '已取消' } as Record<string, string>)[tx.status] || tx.status;
        const counterparty = counterpartyLabel(tx.counterparty);
        return <li key={tx.tx_id}><span>{label}{counterparty && ` · ${counterparty}`} {tx.note && `· ${tx.note}`}<small>{beijingTime(tx.timestamp)} · {statusLabel}</small></span><strong className={tx.status !== 'completed' ? 'hint' : outgoing ? 'out' : 'in'}>{outgoing ? '-' : '+'}¥{tx.amount.toFixed(2)}</strong></li>;
      })}</ul>}</div></section></div>}
    {bankingSection === 'cards' && <><div className="page-intro cards-page-intro"><p className="eyebrow">CARDS</p><h1>管理卡片</h1><p>查看卡片状态与限额；变更需确认，部分操作还需验证码。</p></div><Suspense fallback={<section className="card"><p>正在加载卡片管理…</p></section>}><CardManagement key={user.user_id} accounts={accounts} revision={bankRevision} onActivity={load} selectedCardId={selectedCardId} onSelectCard={setSelectedCardId} pendingOperationId={pendingCardOperationId} onPendingHandled={() => setPendingCardOperationId(undefined)} /></Suspense></>}
    {bankingSection === 'subscriptions' && <><div className="page-intro subscription-page-intro"><p className="eyebrow">SUBSCRIPTIONS</p><h1>管理订阅</h1><p>取消前展示影响，历史流水不会被删除。</p></div><section className="card subscription-card">
      <h2>订阅管理</h2><p className="hint">选择一项生效中的订阅后，可向 NOVA 询问“这个订阅什么时候扣费？”</p>
      {activeSubscriptions.length === 0 ? <p className="hint">暂无生效中的订阅</p> : <ul className="history">{activeSubscriptions.map(subscription => {
        const active = subscription.status === 'ACTIVE';
        return <li key={subscription.subscription_id} className={selectedSubscriptionId === subscription.subscription_id ? 'nova-selected-row' : undefined}><span><strong>{subscription.merchant_name}</strong><small>{subscription.debit_account_label} · 每月 ¥{subscription.amount.toFixed(2)}</small><small>{active ? `计划扣费：${beijingTime(subscription.next_charge_at)}` : `已于 ${subscription.cancelled_at ? beijingTime(subscription.cancelled_at) : '-'} 取消`}</small>{subscription.source_transaction_id && <small>识别依据：已有历史扣费</small>}</span><div className="subscription-action"><button className="secondary" aria-pressed={selectedSubscriptionId === subscription.subscription_id} onClick={() => setSelectedSubscriptionId(selectedSubscriptionId === subscription.subscription_id ? undefined : subscription.subscription_id)}>{selectedSubscriptionId === subscription.subscription_id ? '已选中 · 再点取消选择' : '选择这项订阅问 NOVA'}</button><strong className={active ? 'in' : 'hint'}>{active ? '生效中' : '已取消'}</strong>{active ? <><button disabled={isActing} onClick={() => { setPendingSubscription(null); setPendingChargeSubscription(subscription); setSubscriptionChargeId(newId()); }}>模拟扣费</button><button className="secondary" disabled={isActing} onClick={async () => {
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
      {cancelledSubscriptions.length > 0 && <div className="cancelled-subscriptions">
        <label className="hint"><input type="checkbox" checked={showCancelledSubscriptions} onChange={event => setShowCancelledSubscriptions(event.target.checked)} aria-controls="cancelled-subscription-list" aria-expanded={showCancelledSubscriptions} /> 显示已取消订阅（{cancelledSubscriptions.length}）</label>
        {showCancelledSubscriptions && <div id="cancelled-subscription-list">
          <p className="hint">测试专用：恢复订阅用于重复调试取消操作，不接入 NOVA。恢复不会扣款或修改原计划扣费日期，历史流水保留。</p>
          <ul className="history">{cancelledSubscriptions.map(subscription => <li key={subscription.subscription_id}>
            <span><strong>{subscription.merchant_name}</strong><small>{subscription.debit_account_label} · 每月 ¥{subscription.amount.toFixed(2)}</small><small>已于 {subscription.cancelled_at ? beijingTime(subscription.cancelled_at) : '-'} 取消</small>{subscription.source_transaction_id && <small>识别依据：已有历史扣费</small>}</span>
            <div className="subscription-action"><strong className="hint">已取消</strong><small>不会继续扣费</small><button className="secondary" disabled={isActing} onClick={() => restoreSubscriptionForTesting(subscription)}>恢复订阅（测试专用）</button></div>
          </li>)}</ul>
        </div>}
      </div>}
      {pendingSubscription && <div className="subscription-confirm"><p><strong>确认取消 {pendingSubscription.merchant_name}？</strong></p><p>扣费账户：{pendingSubscription.debit_account_label} · 每月 ¥{pendingSubscription.amount.toFixed(2)}<br />计划扣费：{beijingTime(pendingSubscription.next_charge_at)}</p><p className="hint">确认后将停止后续模拟自动扣费，历史扣费流水不会删除。</p><div className="confirm"><button className="secondary" disabled={isActing} onClick={() => { setPendingSubscription(null); setSubscriptionCancellationToken(''); }}>返回</button><button disabled={isActing} onClick={confirmSubscriptionCancellation}>{isActing ? '正在取消…' : '确认取消'}</button></div></div>}
      {pendingChargeSubscription && <div className="subscription-confirm"><p><strong>模拟扣除 {pendingChargeSubscription.merchant_name} ¥{pendingChargeSubscription.amount.toFixed(2)}？</strong></p><p className="hint">确认后将真实减少演示账户余额、生成订阅流水，并把下次扣费日推进一个月。</p><div className="confirm"><button className="secondary" disabled={isActing} onClick={() => { setPendingChargeSubscription(null); setSubscriptionChargeId(''); }}>返回</button><button disabled={isActing} onClick={confirmSubscriptionCharge}>{isActing ? '正在扣费…' : '确认模拟扣费'}</button></div></div>}
    </section></>}
    </>}
        </div>
      </div>
    </div>
    <NovaChat promptRequest={novaPrompts[0]} onPromptHandled={id => setNovaPrompts(items => items.filter(item => item.id !== id))} headerControls={desktopLayout?novaControls:null} pageContext={appView === 'thirdParty' ? thirdPartyPageContext : bankingSection === 'insights' ? billPageContext : bankingSection === 'cards' ? { version: 1, page: 'cards', cards: { selected_card_id: selectedCardId } } : bankingSection === 'subscriptions' ? { version: 1, page: 'subscriptions', subscriptions: { selected_subscription_id: activeSubscriptions.some(s => s.subscription_id === selectedSubscriptionId) ? selectedSubscriptionId : undefined } } : { version: 1, page: bankingSection === 'audit' ? 'home' : bankingSection }} key={user.user_id} name={user.name} userId={user.user_id} open={agentOpen} onClose={() => { if (window.matchMedia('(min-width: 1101px)').matches) setAgentExpanded(false); else setAgentOpen(false); }} onNavigate={section => openSection(section)} onReviewCardOperation={reviewCardOperation} onTransferActivity={() => { void load(); }} />
    {mfaOpen && <div className="transfers-mfa" style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }} onClick={() => { if (!isActing) { setMfaOpen(false); setMfaCode(''); } }}>
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
