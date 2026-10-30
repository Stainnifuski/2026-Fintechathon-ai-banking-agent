import Modal from './components/Modal';
import type {BillResultTarget} from './services/nova';
import {useManualPrefill} from './services/manualPrefill';
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
import { formatFen, formatYuan, maskCard } from './utils/money';
import { isOutgoing, transactionStatus, transactionTypeLabel } from './utils/transactionLabels';

const Audit = lazy(() => import('./components/Audit'));
const BillAnalysis = lazy(() => import('./components/BillAnalysis'));
const Wealth = lazy(() => import('./components/Wealth'));
const PortfolioConsumerPreview = import.meta.env.DEV ? lazy(() => import('./components/Wealth/PortfolioConsumerPreview')) : null;
const wealthConsumerPreviewEnabled = import.meta.env.DEV && new URLSearchParams(window.location.search).get('wealth-dev') === '1';
const CardManagement = lazy(() => import('./components/CardManagement'));
const ThirdPartyServices = lazy(() => import('./components/ThirdPartyServices'));
const MerchantDashboard = lazy(() => import('./components/MerchantDashboard'));
const ScheduledTasks = lazy(() => import('./components/ScheduledTasks'));

const newId = () => crypto.randomUUID?.() ?? `id-${Date.now()}-${Math.random()}`;
const localDateTimeInput = beijingInput;
type BankingSection = 'home' | 'transfer' | 'insights' | 'cards' | 'subscriptions' | 'scheduled' | 'wealth' | 'audit';
// 侧栏导航图标：只是彩色圆角方块里的白色线条，方块颜色仍由 data-destination 决定。
const NAV_ICONS: Record<BankingSection | 'services', string> = {
  home: 'M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  transfer: 'M4 8h13l-4-4M20 16H7l4 4',
  insights: 'M5 20V10M12 20V4M19 20v-7',
  cards: 'M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM3 10h18',
  subscriptions: 'M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5',
  scheduled: 'M12 2a10 10 0 1 0 0 20a10 10 0 0 0 0-20zM12 6v6l4 2',
  wealth: 'M12 3l8 9-8 9-8-9z',
  services: 'M5 8h14l-1 12H6zM9 8a3 3 0 0 1 6 0',
  audit: 'M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01',
};
const NavIcon = ({ d }: { d: string }) => <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d={d} /></svg>;

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
  const [recipientMode, setRecipientMode] = useState<'card' | 'phone'>('phone');
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
  const [transferError, setTransferError] = useState('');
  const [requestError, setRequestError] = useState('');
  const [ledgerPage, setLedgerPage] = useState(0);
  const [status, setStatus] = useState('');
  const [chatInput, setChatInput] = useState('');
  const [chat, setChat] = useState<string[]>([]);
  const [activeTask, setActiveTask] = useState<bank.TaskSnapshot | null>(null);
  const [conversation, setConversation] = useState<bank.ChatReply | null>(null);
  const [isChatting, setIsChatting] = useState(false);
  const [retryChat, setRetryChat] = useState<bank.ChatRequest | null>(null);
  const [appView, setAppView] = useState<'banking' | 'thirdParty'>('banking');
  const [bankingSection, setBankingSection] = useState<BankingSection>(wealthConsumerPreviewEnabled ? 'wealth' : 'home');
  const [agentOpen, setAgentOpen] = useState(() => window.matchMedia('(min-width: 1101px)').matches);
  const [agentExpanded, setAgentExpanded] = useState(!wealthConsumerPreviewEnabled);
  const [agentModeChanging, setAgentModeChanging] = useState(false);
  const modeTransition = useRef(false);
  const modeTimers = useRef<number[]>([]);
  useEffect(() => () => modeTimers.current.forEach(window.clearTimeout), []);
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
  useEffect(() => { setShowCancelledSubscriptions(false);setBillNavigation(undefined); }, [user?.user_id]);
  const [billNavigation,setBillNavigation]=useState<{id:string;target:BillResultTarget}>();
  const [billPageContext, setBillPageContext] = useState<NovaPageContext>({ version: 1, page: 'insights' });
  const [thirdPartyPageContext, setThirdPartyPageContext] = useState<NovaPageContext>({ version: 1, page: 'thirdParty' });
  const updateThirdPartyPageContext = useCallback((context: NovaPageContext) => setThirdPartyPageContext(context), []);
  const updateBillPageContext = useCallback((context: NovaPageContext) => setBillPageContext(context), []);
  const [highlightedPaymentRequest, setHighlightedPaymentRequest] = useState('');
  // 人工接管：把聊天任务卡已填写的内容填入本页原表单，由本人在原表单继续。
  useManualPrefill(['transfer','collection','payment_request','subscription'],({key,draft,task,handoff={}})=>{
    const text=(value:unknown)=>typeof value==='string'?value:'';
    const known=(id:unknown)=>typeof id==='string'&&accounts.some(account=>account.account_id===id);
    if(key.startsWith('transfer:')){
      // 银行已生成待确认的转账：在本页“转账任务”中继续确认，不再重填表单，避免同一笔转账提交两次。
      const snapshot=task.task as bank.TaskSnapshot|null|undefined;
      if(snapshot&&snapshot.phase!=='FINISHED'){
        setActiveTask(snapshot);setStatus('已转人工办理：这笔转账已生成确认信息，请在下方“转账任务”中核对并确认。');
        if(user)localStorage.setItem(`active-task-${user.user_id}`,snapshot.task_id);
        setTimeout(()=>document.querySelector('.transfer-task')?.scrollIntoView({behavior:'smooth',block:'center'}),0);
        return;
      }
      // 卡片只显示脱敏号码；完整号码与收款账户由服务端在接管时交回。
      const usable=(value:string)=>!!value&&!/[*•]/.test(value);
      const phone=usable(text(draft.phone))?text(draft.phone):text(handoff.phone);
      const card=usable(text(draft.card))?text(draft.card):text(handoff.card);
      // 只有收款人未在卡片上改动时，才沿用服务端核实过的收款账户。
      const original=(task.form as {recipient?:string}|undefined)?.recipient;
      const target=text(draft.recipient)===original?text(handoff.to_account):'';
      setRecipientName(text(draft.recipient));
      setRecipientMode(card&&!phone?'card':'phone');
      setRecipientNumbers({card,phone});
      setRecipientAccountIds({card:card?target:'',phone:phone?target:''});
      setAmount(text(draft.amount));setNote(text(draft.note));
      if(known(draft.payer))setFromAccount(draft.payer as string);
      setTransferMode(draft.executionMode==='scheduled'?'scheduled':'instant');setScheduledAt(text(draft.scheduledAt));
      setTransferError('');
    }else if(key.startsWith('collection:')){
      if(Array.isArray(draft.selected))setRequestPayerIds(draft.selected.filter((id):id is string=>typeof id==='string'));
      if(known(draft.account))setRequestAccountId(draft.account as string);
      setRequestAmount(text(draft.amount));setSplitEqually(!!draft.split);setRequestNote(text(draft.note));setRequestError('');
    }else if(key.startsWith('payment_request:')){
      const requests=(task.requests as bank.PaymentRequest[]|undefined)??[];
      const target=requests.find(request=>['PENDING','ACCEPTED'].includes(request.status))??requests[0];
      if(target)setHighlightedPaymentRequest(target.payment_request_id);
    }else if(key.startsWith('subscription:')){
      const id=(task.subscription_id as string|null|undefined)??(task.subscription as bank.Subscription|null|undefined)?.subscription_id;
      if(id)setSelectedSubscriptionId(id);
    }
  },accounts.length>0);

  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [isActing, setIsActing] = useState(false);
  const [totpEnabled, setTotpEnabled] = useState(false);
  const [mfaOpen, setMfaOpen] = useState(false);
  const [mfaCode, setMfaCode] = useState('');
  const [mfaFactor, setMfaFactor] = useState<bank.SecondFactor | null>({ method: 'totp' });
  const [enrollOpen, setEnrollOpen] = useState(false);
  const [enrollData, setEnrollData] = useState<bank.TotpEnroll | null>(null);
  const [enrollCode, setEnrollCode] = useState('');
  const [rebindConfirmOpen, setRebindConfirmOpen] = useState(false);
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
      document.documentElement.dataset.novaAuth='moving';
      flushSync(()=>{setAgentOpen(true);setAgentExpanded(true);setUser(result.user);});
      // The persistent orb reveals the workspace when its 500 ms move finishes.
      // Merchant workspaces have no NOVA destination.
      await new Promise<void>(resolve=>{
        const finish=()=>{
          window.removeEventListener('nova-auth-arrived',finish);clearTimeout(fallback);
          document.documentElement.dataset.novaAuth='revealing';
          window.setTimeout(()=>{
            document.documentElement.removeAttribute('data-nova-auth');
            document.querySelectorAll<HTMLElement>('#root > :not(.nova-global-orb)').forEach(element=>{element.inert=false;});
            resolve();
          },500);
        };
        const fallback=window.setTimeout(finish,550);
        window.addEventListener('nova-auth-arrived',finish);
        document.querySelectorAll<HTMLElement>('#root > :not(.nova-global-orb)').forEach(element=>{element.inert=true;});
      });
    } catch (error) { setStatus(`${loginMode === 'login' ? '登录' : '注册'}失败：${message(error)}`); }
    finally { authenticationPending.current = false; setIsAuthenticating(false); }
  };

  const submitTransfer = async (event: FormEvent) => {
    event.preventDefault(); setStatus(''); setTransferError('');
    if (!fromAccount) return setTransferError('请选择付款账户');
    if (!recipientName.trim()) return setTransferError('请填写收款人户名或备注名');
    if (!/^\d+(\.\d{1,2})?$/.test(amount.trim()) || Number(amount) <= 0) return setTransferError('请输入大于 0 的金额，最多两位小数');
    if (transferMode === 'scheduled') {
      if (!scheduledAt) return setTransferError('请选择定时执行时间');
      if (new Date(fromBeijingInput(scheduledAt)).getTime() <= Date.now()) return setTransferError('定时执行时间必须晚于当前时间');
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
    } catch (error) { setTransferError(message(error)); }
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
    event.preventDefault(); setStatus(''); setRequestError('');
    if (requestPayerIds.length === 0 || !requestAccountId) return setRequestError('请至少选择一位付款人和收款账户');
    if (!/^\d+(\.\d{1,2})?$/.test(requestAmount.trim()) || Number(requestAmount) <= 0) return setRequestError('请输入大于 0 的金额，最多两位小数');
    const amountFen = Math.round(Number(requestAmount) * 100);
    if (splitEqually && amountFen % requestPayerIds.length !== 0) {
      return setRequestError('总金额无法精确均分到分，请调整金额');
    }
    setIsActing(true);
    try {
      await bank.createPaymentRequestBatch({
        payer_user_ids: requestPayerIds, receive_account_id: requestAccountId,
        amount: Number(requestAmount), split_equally: splitEqually,
        note: requestNote || undefined, idempotency_key: newId(),
      });
      setRequestAmount(''); setRequestNote(''); setStatus(`已向 ${requestPayerIds.length} 人发出收款申请`); await load();
    } catch (error) { setRequestError(message(error)); }
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
      if (errorCode(error) === 'TOTP_NOT_ENROLLED') { setMfaOpen(false); setMfaCode(''); void startEnroll(); }
    } finally { setIsActing(false); }
  };

  // 打开绑定弹窗：向后端申请新密钥与二维码。
  // 后端每次 enroll 都会重置密钥并作废已绑定的验证器，所以已绑定时先请本人确认，确认后才申请新密钥。
  const openEnroll = () => {
    if (isActing) return;
    setStatus('');
    if (totpEnabled) { setRebindConfirmOpen(true); return; }
    void startEnroll();
  };
  const startEnroll = async () => {
    setRebindConfirmOpen(false);
    try {
      const data = await bank.totpEnroll();
      setTotpEnabled(false); // 新密钥确认前不生效，旧验证器已作废
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
    setRecipientMode('phone'); setRecipientNumbers({ card: '', phone: '' }); setRecipientAccountIds({ card: '', phone: '' }); setRecipientPanel(null);
    setAmount(''); setNote(''); setEditingNote(null); setEditNoteCleared(false); setTransferMode('instant');
    setRequestAmount(''); setRequestNote(''); setSplitEqually(false); setChatInput('');
    setTotpEnabled(false); setMfaOpen(false); setMfaCode(''); setEnrollOpen(false); setEnrollData(null); setEnrollCode(''); setRebindConfirmOpen(false);
    setSelectedCardId(undefined); setSelectedSubscriptionId(undefined);
    setBillPageContext({ version: 1, page: 'insights' });
    setThirdPartyPageContext({ version: 1, page: 'thirdParty' });
    setAppView('banking'); setBankingSection('home'); setAgentOpen(window.matchMedia('(min-width: 1101px)').matches); setAgentExpanded(true); setHighlightedPaymentRequest('');
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

  const requestFen = Math.round((Number(requestAmount) || 0) * 100);
  const requestRecipients = [
    ...recipients.map(item => ({ user_id: item.user_id, name: item.name, label: item.nickname || item.name, number: item.phone ? `手机号 ${item.phone}` : item.card_masked ? `银行卡尾号 ${item.card_masked.slice(-4)}` : '', saved: true })),
    ...transferHistoryRecipients.filter(item => !recipients.some(saved => saved.user_id === item.user_id))
      .map(item => ({ user_id: item.user_id, name: item.name, label: item.name, number: item.phone_masked ? `手机号 ${item.phone_masked}` : item.card_number ? `银行卡尾号 ${item.card_number.slice(-4)}` : '', saved: false })),
  ];
  const splitIsExact = requestPayerIds.length > 0 && requestFen % requestPayerIds.length === 0;
  const perPersonFen = splitEqually && requestPayerIds.length > 0 ? requestFen / requestPayerIds.length : requestFen;
  const requestTotalFen = splitEqually ? requestFen : requestFen * requestPayerIds.length;
  // 最近流水每页 5 笔；流水变少时页码自动回到有效范围
  const LEDGER_PAGE_SIZE = 5;
  const ledgerPages = Math.max(1, Math.ceil(transactions.length / LEDGER_PAGE_SIZE));
  const ledgerPageIndex = Math.min(ledgerPage, ledgerPages - 1);
  const ledgerRows = transactions.slice(ledgerPageIndex * LEDGER_PAGE_SIZE, (ledgerPageIndex + 1) * LEDGER_PAGE_SIZE);
  const accountName = (type: string) => type === 'savings' ? '活期账户' : type === 'credit' ? '信用账户' : '银行账户';
  const payerOption = (a: bank.Account) => `${accountName(a.account_type)} ${maskCard(a.card_number)} · 可用 ${formatFen(a.available_fen ?? Math.round(a.balance * 100))}`;

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
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { change(); return; }
    // Fade only the moving titles first. Fixed controls never join the fade.
    modeTransition.current = true;
    flushSync(() => setAgentModeChanging(true));
    modeTimers.current = [window.setTimeout(() => {
      change();
      modeTimers.current.push(window.setTimeout(() => {
        setAgentModeChanging(false);
        modeTransition.current = false;
        modeTimers.current = [];
      }, 400)); // 380ms shared panel/mascot motion plus one display frame.
    }, 110)];
  };
  const openServices = () => {
    if (appView !== 'thirdParty') setThirdPartyPageContext({ version: 1, page: 'thirdParty' });
    setAppView('thirdParty'); setAgentExpanded(false);
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

  return <main className={`app prototype-app home-workspace${agentOpen ? ' nova-open' : ''}${agentOpen && agentExpanded ? ' nova-expanded' : ' nova-docked'}${agentModeChanging ? ' nova-mode-changing' : ''}`}>
    <div className="bank-shell" ref={bankShellRef}>
      <aside className="bank-sidebar">
        <button className="brand" onClick={() => openSection('home')} aria-label="返回首页"><span className="brand-mark">N</span><span>NOVA<small>AI BANKING</small></span></button>
        <nav aria-label="主要功能">
          {([['home','概览'],['transfer','转账与收款'],['insights','账单洞察'],['cards','卡片'],['subscriptions','订阅'],['scheduled','定时任务'],['wealth','理财']] as [BankingSection,string][]).map(([key,label]) =>
            <button key={key} data-destination={key} className={appView === 'banking' && bankingSection === key ? 'active' : ''} onClick={() => openSection(key)}><span><NavIcon d={NAV_ICONS[key]} /></span>{label}</button>)}
          <button data-destination="services" className={appView === 'thirdParty' ? 'active' : ''} onClick={openServices}><span><NavIcon d={NAV_ICONS.services} /></span>生活服务</button>
        </nav>
        <div className="sidebar-bottom">
          <button className={`sidebar-audit-button ${appView === 'banking' && bankingSection === 'audit' ? 'active' : ''}`} data-destination="audit" onClick={() => openSection('audit')}><span><NavIcon d={NAV_ICONS.audit} /></span>操作记录</button>
          <div className="sidebar-foot"><span className="security-dot" />模拟银行演示<small>所有资金与交易均为虚构</small></div>
        </div>
      </aside>
      <div className="bank-workspace">
        <header className="bank-topbar"><div><BeijingClock /><span className="workspace-label">{desktopLayout && agentOpen && agentExpanded ? 'NOVA 银行智能体' : appView === 'thirdParty' ? '生活服务' : ({home:'财务概览',transfer:'转账与收款',insights:'账单洞察',cards:'卡片管理',subscriptions:'订阅管理',scheduled:'定时任务',wealth:'理财规划',audit:'操作记录'} as Record<BankingSection,string>)[bankingSection]}</span>{desktopLayout&&agentOpen&&agentExpanded&&(bankingSection!=='home'||appView==='thirdParty')&&<small className="nova-reference-context">参考工作台：{appView==='thirdParty'?'生活服务':({transfer:'转账与收款',insights:'账单洞察',cards:'卡片管理',subscriptions:'订阅管理',scheduled:'定时任务',wealth:'理财规划'} as Partial<Record<BankingSection,string>>)[bankingSection]}</small>}</div><div className="top-actions"><div className="nova-header-slot" ref={setNovaControls} /><button id="nova-toggle" className="agent-button" aria-expanded={desktopLayout ? agentExpanded && agentOpen : agentOpen} aria-controls="nova-panel" title={desktopLayout ? (agentExpanded && agentOpen ? '切换为侧边对话，查看财务概览' : '展开 NOVA 对话') : (agentOpen ? '收起 NOVA 对话' : '打开 NOVA 对话')} onClick={switchAgentMode}>{desktopLayout ? (agentExpanded && agentOpen ? '收起 NOVA' : '展开 NOVA') : (agentOpen ? '收起 NOVA' : '展开 NOVA')}</button><button className="icon-button auth-enroll-button" disabled={isActing} onClick={openEnroll} aria-label={totpEnabled ? '已绑定验证器' : '绑定验证器'} title={totpEnabled ? '已绑定验证器' : '绑定验证器'}>⌾</button><button className="profile-button" onClick={logout} title="退出登录">{user.name.slice(0,1)}</button></div></header>
        <SecurityNotice userId={user.user_id} />
        {status && <p className="notice global-notice">{status}</p>}
        <div className="workspace-content">
        {appView==='banking'&&bankingSection==='wealth'&&import.meta.env.DEV&&import.meta.env.VITE_B6_BROWSER_TEST==='true'&&<p className="notice" role="status">B6 隔离验收：固定契约方案＋独立模拟账户。不是模型建议，与日常演示账户无关；测试业务时钟与现实时间分开。</p>}
        {appView === 'thirdParty' ? <Suspense fallback={<section className="card"><p>正在加载第三方服务…</p></section>}>
          <ThirdPartyServices requestedTab={manualTab} onContextChange={updateThirdPartyPageContext} revision={bankRevision} onBack={() => openSection('home')} onGoBank={goToBankPayment} />
        </Suspense> : <>
        {bankingSection === 'home' && <FinancialOverview userId={user.user_id} revision={bankRevision} accounts={accounts} subscriptions={subscriptions} loading={overviewLoading} error={overviewError} onRetry={() => void load()} assetSummary={assetSummary} onNavigate={openSection} />}
        {bankingSection === 'scheduled' && <Suspense fallback={<section className="card"><p>正在加载定时任务…</p></section>}><ScheduledTasks accounts={accounts} onFundsChanged={()=>{void load();}} onGoTransfer={()=>openSection('transfer')} onDispatch={task=>{if(!task.dispatch_request_id)return;setNovaPrompts(items=>[...items,{id:task.dispatch_request_id!,text:task.description,context:{version:1,page:'home'},userId:user.user_id}]);setAgentOpen(true);setAgentExpanded(false);}} /></Suspense>}
        {bankingSection === 'wealth' && <AssetSummaryCard {...assetSummary} onRefresh={assetSummary.refresh} />}
        {bankingSection === 'wealth' && showWealthPreview && PortfolioConsumerPreview && <Suspense fallback={<section className="card">正在加载开发预览…</section>}><PortfolioConsumerPreview onReturn={()=>setShowWealthPreview(false)} accounts={accounts} revision={bankRevision} onActivity={load} userId={user.user_id} /></Suspense>}
        {bankingSection === 'wealth' && !showWealthPreview && <>{wealthConsumerPreviewEnabled&&<button className="secondary" onClick={()=>setShowWealthPreview(true)}>返回理财开发预览（保留输入）</button>}<div className="page-intro wealth-intro"><div><p className="eyebrow">WEALTH</p><h1>理财规划与购买</h1><p>选择整体规划或目标产品，核对方案后确认并完成验证。</p></div></div><Suspense fallback={<section className="card"><p>正在加载理财模拟…</p></section>}>{workspaceReady===user.user_id?<Wealth key={user.user_id} userId={user.user_id} accounts={accounts} revision={bankRevision} onActivity={load} />:<section className="card">{workspaceError?<><p role="alert">{workspaceError}</p><button onClick={()=>{workspaceBoot.current=null;setWorkspaceRetry(n=>n+1);}}>重新开启操作轮次</button></>:<p role="status">正在开启新的理财操作轮次…</p>}</section>}</Suspense></>}
        {bankingSection === 'audit' && <Suspense fallback={<section className="card"><p>正在加载操作记录…</p></section>}><Audit key={user.user_id} /></Suspense>}
        {bankingSection === 'insights' && <><div className="page-intro bills-page-intro"><p className="eyebrow">INSIGHTS</p><h1>查看支出与变化</h1><p>查看消费与周期收支，按自己的标签整理账单。</p></div><Suspense fallback={<section className="card"><p>正在加载账单分析…</p></section>}><BillAnalysis navigation={billNavigation} key={user.user_id} accounts={accounts} revision={bankRevision} onViewCashflows={() => openSection('transfer')} onContextChange={updateBillPageContext} onManageSubscription={reviewBillSubscription} onAskNova={(text, context) => { setNovaPrompts(items => [...items,{id:crypto.randomUUID(),text,context,userId:user.user_id}]); setAgentOpen(true); setAgentExpanded(false); }} /></Suspense></>}

        {bankingSection === 'transfer' && <div className="transfers-page"><div className="page-intro transfers-page-intro"><p className="eyebrow">MOVE MONEY</p><h1>转账与收款</h1><p>先生成确认信息；高风险操作仍需要本人验证。</p></div><section className="transfer-board">
      <div className="transfer-col">
      <div className="card transfer-accounts"><h2>我的账户</h2>{accounts.map(a => <div className="account" key={a.account_id}><span>{accountName(a.account_type)} · {a.card_number}</span>{a.transfer_card_number && <small>模拟收款卡号：{a.transfer_card_number}</small>}<strong>{formatYuan(a.balance)}</strong>{a.available_fen != null && <small>可用 {formatYuan((a.available_fen / 100))} · 占款 {formatYuan(((a.held_fen || 0) / 100))}</small>}<small>今日已转 {formatYuan(a.daily_transfer_used)} / {formatYuan(a.daily_transfer_limit)}</small></div>)}</div>
      <div className="card transfer-request-create"><h2>发起收款</h2><form className="transfer-form" onSubmit={submitPaymentRequest} noValidate>
        <div className="span-2 transfer-field"><span className="transfer-field-label">向谁收款（可多选）</span>
          {requestRecipients.length === 0 ? <p className="hint">暂无联系人或历史转账人</p> : <div className="payer-list" role="group" aria-label="付款人">{requestRecipients.map(r => <label className="payer-option" key={r.user_id}><input type="checkbox" checked={requestPayerIds.includes(r.user_id)} onChange={e => setRequestPayerIds(ids => e.target.checked ? [...ids, r.user_id] : ids.filter(id => id !== r.user_id))} /><span><strong>{r.label}{r.label !== r.name ? `（${r.name}）` : ''}</strong><small>{[r.number, !r.saved && '历史转账人'].filter(Boolean).join(' · ')}</small></span></label>)}</div>}
          <details className="add-contact-details"><summary>＋ 添加联系人</summary><AddContactForm onAdded={load} /></details>
        </div>
        <label>收款账户<select value={requestAccountId} onChange={e => setRequestAccountId(e.target.value)}>{accounts.map(a => <option value={a.account_id} key={a.account_id}>{accountName(a.account_type)} {maskCard(a.card_number)}</option>)}</select></label>
        <label>{splitEqually ? 'AA 总金额（元）' : '每人金额（元）'}<input inputMode="decimal" value={requestAmount} placeholder="0.00" onChange={e => setRequestAmount(e.target.value)} /></label>
        <label className="span-2"><input type="checkbox" checked={splitEqually} onChange={e => setSplitEqually(e.target.checked)} />AA 均分：按人数平分总金额</label>
        <label className="span-2">备注（可选）<input maxLength={256} value={requestNote} onChange={e => setRequestNote(e.target.value)} /></label>
        {requestPayerIds.length > 0 && requestFen > 0 && <p className={`span-2 ${splitEqually && !splitIsExact ? 'error' : 'notice'}`}>{splitEqually && !splitIsExact ? '这个金额无法精确均分到分，请调整金额。' : `向 ${requestPayerIds.length} 人各收 ${formatFen(perPersonFen)}，合计 ${formatFen(requestTotalFen)}。`}</p>}
        {requestError && <p className="error span-2" role="alert">{requestError}</p>}
        <button className="span-2 transfer-wide-button" disabled={requestPayerIds.length === 0 || !requestAccountId || (splitEqually && !splitIsExact) || isActing}>{isActing ? '正在发出…' : '发出收款申请'}</button>
      </form></div>
      </div>
      <div className="transfer-col">
      <div className="card transfer-compose"><div className="transfer-heading"><h2>新建转账</h2><div className="transfer-heading-actions transfer-text-actions"><button type="button" className="secondary" aria-expanded={recipientPanel === 'people'} onClick={() => setRecipientPanel(current => current === 'people' ? null : 'people')}>选择收款人</button><button type="button" className="secondary" aria-expanded={recipientPanel === 'settings'} onClick={() => setRecipientPanel(current => current === 'settings' ? null : 'settings')}>管理</button></div></div>
      {recipientPanel && <TransferContacts key={user.user_id} userId={user.user_id} mode={recipientPanel} onChoose={chooseTransferRecipient} onChanged={load} />}
      <form className="transfer-form" onSubmit={submitTransfer} noValidate>
        <label className="span-2">付款账户<select value={fromAccount} onChange={e => setFromAccount(e.target.value)}>{accounts.map(a => <option value={a.account_id} key={a.account_id}>{payerOption(a)}</option>)}</select></label>
        <label className="span-2">收款人户名或备注名<input maxLength={64} value={recipientName} onChange={e => { setRecipientName(e.target.value); setRecipientAccountIds({ card: '', phone: '' }); }} placeholder="输入真实户名或已保存的备注名" /></label>
        <div className="span-2 transfer-field">
          <div className="transfer-field-head"><span>收款账户</span><div className="transfer-segmented" role="group" aria-label="收款账户类型">{(['phone', 'card'] as const).map(mode => <button type="button" key={mode} aria-pressed={recipientMode === mode} onClick={() => setRecipientMode(mode)}>{mode === 'phone' ? '手机号' : '银行卡号'}</button>)}</div></div>
          <input aria-label={recipientMode === 'card' ? '银行卡号' : '手机号'} inputMode="numeric" autoComplete="off" value={recipientNumbers[recipientMode]} onChange={e => { setRecipientNumbers(current => ({ ...current, [recipientMode]: e.target.value })); setRecipientAccountIds(current => ({ ...current, [recipientMode]: '' })); }} placeholder={recipientMode === 'card' ? '完整的模拟银行卡号；用已保存的备注名时可留空' : '完整手机号'} />
        </div>
        <label>金额（元）<input inputMode="decimal" value={amount} placeholder="0.00" onChange={e => setAmount(e.target.value)} /></label>
        <label>备注（可选）<input maxLength={256} value={note} onChange={e => setNote(e.target.value)} /></label>
        <div className="span-2 transfer-field">
          <div className="transfer-field-head"><span>执行时间</span><div className="transfer-segmented" role="group" aria-label="执行时间">{(['instant', 'scheduled'] as const).map(mode => <button type="button" key={mode} aria-pressed={transferMode === mode} onClick={() => setTransferMode(mode)}>{mode === 'instant' ? '立即' : '定时'}</button>)}</div></div>
          {transferMode === 'scheduled' && <div className="transfer-schedule"><input aria-label="执行时间（北京时间）" min={localDateTimeInput(new Date())} type="datetime-local" value={scheduledAt} onChange={e => setScheduledAt(e.target.value)} /><small>按北京时间执行。</small></div>}
        </div>
        {transferError && <p className="error span-2" role="alert">{transferError}</p>}
        <div className="span-2 transfer-submit"><button disabled={!fromAccount || isActing}>{isActing ? '正在生成…' : '生成确认信息'}</button><small>生成后在下方任务卡上确认，确认前不会扣款。</small></div>
      </form></div>
      <div className="card transfer-request-list" id="payment-requests"><h2>收款申请</h2>{paymentRequests.some(request => ['PENDING', 'ACCEPTED'].includes(request.status) && request.payer_user_id === user.user_id) && <label>付款账户<select value={fromAccount} onChange={e => setFromAccount(e.target.value)}>{accounts.map(a => <option value={a.account_id} key={a.account_id}>{a.card_number}（{formatYuan(a.balance)}）</option>)}</select></label>}{paymentRequests.length === 0 ? <p className="hint">暂无收款申请</p> : <ul className="history">{paymentRequests.map(request => {
        const incoming = request.payer_user_id === user.user_id;
        const canPay = incoming && ['PENDING', 'ACCEPTED'].includes(request.status);
        return <li className={highlightedPaymentRequest === request.payment_request_id ? 'payment-request-highlight' : ''} key={request.payment_request_id}><span>{incoming ? `${request.requester_name} 向你收款` : `向 ${request.payer_name} 收款`} {request.note && `· ${request.note}`}<small>{request.status} · {beijingTime(request.created_at)}</small></span><strong>{formatYuan(request.amount)}</strong>{(canPay || request.status === 'PENDING') && <div className="confirm">{canPay ? <><button disabled={isActing} onClick={() => actOnPaymentRequest(request, 'accept')}>{request.status === 'ACCEPTED' ? '确认付款' : '接受申请'}</button>{request.status === 'PENDING' && <button className="secondary" disabled={isActing} onClick={() => actOnPaymentRequest(request, 'reject')}>拒绝</button>}</> : <button className="secondary" disabled={isActing} onClick={() => actOnPaymentRequest(request, 'cancel')}>取消</button>}</div>}</li>;
      })}</ul>}<p className="hint">接受申请后先核对转账任务，再确认付款；需要强验证的任务会停留在身份验证阶段。</p></div>
      <div className="card transfer-dialogue"><h2>即时转账对话</h2><p className="hint">例如：给手机号13800000002转300元，备注房租；改成500元；清空备注。当前模式：{conversation?.mode === 'model' ? '模型理解' : '离线规则（有限表达）'}。</p><button className="secondary" disabled={isChatting || !!retryChat} onClick={newConversation}>新建转账会话</button><div className="chat">{chat.map((item,index)=><p key={index}>{item}</p>)}</div>{conversation?.candidates.map(candidate=><button key={candidate.candidate_id} disabled={isChatting || !!retryChat} onClick={()=>sendChat(candidate.candidate_id)}>{candidate.label}</button>)}<div className="row"><input value={chatInput} disabled={isChatting || !!retryChat} onChange={e=>setChatInput(e.target.value)} onKeyDown={e=>e.key==='Enter'&&!retryChat&&sendChat()} placeholder="给手机号13800000002转300元，备注聚餐"/><button disabled={isChatting || !!retryChat} onClick={()=>sendChat()}>发送</button></div>{retryChat&&!isChatting&&<button onClick={()=>sendChat(undefined,true)}>重试原消息（避免重复建单）</button>}</div>
      </div>
    </section>
    {activeTask && <section className="card task-focus transfer-task">
      <h2>转账任务</h2><p>{activeTask.message}</p>
      {confirmationCard && <p>从{payerCardTail ? `尾号 ${payerCardTail} 的银行卡` : '付款银行卡'}向 {confirmationCard.recipient_name}（{confirmationCard.recipient_phone_masked
        ? `手机号 ${confirmationCard.recipient_phone_masked}`
        : confirmationCard.recipient_card_masked
          ? `银行卡尾号 ${confirmationCard.recipient_card_masked.slice(-4)}`
          : '收款账户'}）转账 <strong>{formatYuan(confirmationCard.amount)}</strong> · 备注：{confirmationCard.note ?? '无'} · 风险级别 {confirmationCard.risk_level}</p>}
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
    <section className="card transfer-ledger"><div className="transfer-heading"><h2>最近流水</h2>{transactions.length > 0 && <small>共 {transactions.length} 笔</small>}</div>
      {transactions.length === 0 ? <p className="hint">暂无流水</p> : <>
        <div className="ledger-table-wrap"><table className="ledger-table"><thead><tr><th>时间</th><th>类型</th><th>对方 · 备注</th><th className="r">金额</th><th>状态</th></tr></thead>
          <tbody>{ledgerRows.map(tx => {
            const outgoing = isOutgoing(tx.type);
            const [statusLabel, tone] = transactionStatus(tx.status);
            return <tr key={tx.tx_id}>
              <td className="ledger-time">{beijingTime(tx.timestamp).slice(0, 16)}</td>
              <td>{transactionTypeLabel(tx.type)}</td>
              <td>{counterpartyLabel(tx.counterparty)}{tx.note && <small> · {tx.note}</small>}</td>
              <td className={`r ledger-amount ${tx.status !== 'completed' ? 'pending' : outgoing ? 'out' : 'in'}`}>{outgoing ? '' : '+'}{formatYuan(outgoing ? -tx.amount : tx.amount)}</td>
              <td><span className={`ledger-status ledger-status-${tone}`}>{statusLabel}</span></td>
            </tr>;
          })}</tbody></table></div>
        {ledgerPages > 1 && <nav className="ledger-pager" aria-label="流水翻页"><button type="button" className="secondary" disabled={ledgerPageIndex === 0} onClick={() => setLedgerPage(ledgerPageIndex - 1)}>上一页</button><span>第 {ledgerPageIndex + 1} / {ledgerPages} 页</span><button type="button" className="secondary" disabled={ledgerPageIndex >= ledgerPages - 1} onClick={() => setLedgerPage(ledgerPageIndex + 1)}>下一页</button></nav>}
      </>}
    </section></div>}
    {bankingSection === 'cards' && <><div className="page-intro cards-page-intro"><p className="eyebrow">CARDS</p><h1>管理卡片</h1><p>查看卡片状态与限额；变更需确认，部分操作还需验证码。</p></div><Suspense fallback={<section className="card"><p>正在加载卡片管理…</p></section>}><CardManagement key={user.user_id} accounts={accounts} revision={bankRevision} onActivity={load} selectedCardId={selectedCardId} onSelectCard={setSelectedCardId} pendingOperationId={pendingCardOperationId} onPendingHandled={() => setPendingCardOperationId(undefined)} /></Suspense></>}
    {bankingSection === 'subscriptions' && <><div className="page-intro subscription-page-intro"><p className="eyebrow">SUBSCRIPTIONS</p><h1>管理订阅</h1><p>取消前展示影响，历史流水不会被删除。</p></div><section className="card subscription-card">
      <h2>订阅管理</h2><p className="hint">选择一项生效中的订阅后，可向 NOVA 询问“这个订阅什么时候扣费？”</p>
      {activeSubscriptions.length === 0 ? <p className="hint">暂无生效中的订阅</p> : <ul className="history">{activeSubscriptions.map(subscription => {
        const active = subscription.status === 'ACTIVE';
        return <li key={subscription.subscription_id} className={selectedSubscriptionId === subscription.subscription_id ? 'nova-selected-row' : undefined}><span><strong>{subscription.merchant_name}</strong><small>{subscription.debit_account_label} · 每月 {formatYuan(subscription.amount)}</small><small>{active ? `计划扣费：${beijingTime(subscription.next_charge_at)}` : `已于 ${subscription.cancelled_at ? beijingTime(subscription.cancelled_at) : '-'} 取消`}</small>{subscription.source_transaction_id && <small>识别依据：已有历史扣费</small>}</span><div className="subscription-action"><button className="secondary" aria-pressed={selectedSubscriptionId === subscription.subscription_id} onClick={() => setSelectedSubscriptionId(selectedSubscriptionId === subscription.subscription_id ? undefined : subscription.subscription_id)}>{selectedSubscriptionId === subscription.subscription_id ? '已选中 · 再点取消选择' : '选择这项订阅问 NOVA'}</button><strong className={active ? 'in' : 'hint'}>{active ? '生效中' : '已取消'}</strong>{active ? <><button disabled={isActing} onClick={() => { setPendingSubscription(null); setPendingChargeSubscription(subscription); setSubscriptionChargeId(newId()); }}>模拟扣费</button><button className="secondary" disabled={isActing} onClick={async () => {
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
            <span><strong>{subscription.merchant_name}</strong><small>{subscription.debit_account_label} · 每月 {formatYuan(subscription.amount)}</small><small>已于 {subscription.cancelled_at ? beijingTime(subscription.cancelled_at) : '-'} 取消</small>{subscription.source_transaction_id && <small>识别依据：已有历史扣费</small>}</span>
            <div className="subscription-action"><strong className="hint">已取消</strong><small>不会继续扣费</small><button className="secondary" disabled={isActing} onClick={() => restoreSubscriptionForTesting(subscription)}>恢复订阅（测试专用）</button></div>
          </li>)}</ul>
        </div>}
      </div>}
      {pendingSubscription && <div className="subscription-confirm"><p><strong>确认取消 {pendingSubscription.merchant_name}？</strong></p><p>扣费账户：{pendingSubscription.debit_account_label} · 每月 {formatYuan(pendingSubscription.amount)}<br />计划扣费：{beijingTime(pendingSubscription.next_charge_at)}</p><p className="hint">确认后将停止后续模拟自动扣费，历史扣费流水不会删除。</p><div className="confirm"><button className="secondary" disabled={isActing} onClick={() => { setPendingSubscription(null); setSubscriptionCancellationToken(''); }}>返回</button><button disabled={isActing} onClick={confirmSubscriptionCancellation}>{isActing ? '正在取消…' : '确认取消'}</button></div></div>}
      {pendingChargeSubscription && <div className="subscription-confirm"><p><strong>模拟扣除 {pendingChargeSubscription.merchant_name} {formatYuan(pendingChargeSubscription.amount)}？</strong></p><p className="hint">确认后将真实减少演示账户余额、生成订阅流水，并把下次扣费日推进一个月。</p><div className="confirm"><button className="secondary" disabled={isActing} onClick={() => { setPendingChargeSubscription(null); setSubscriptionChargeId(''); }}>返回</button><button disabled={isActing} onClick={confirmSubscriptionCharge}>{isActing ? '正在扣费…' : '确认模拟扣费'}</button></div></div>}
    </section></>}
    </>}
        </div>
      </div>
    </div>
    <NovaChat onBillNavigate={target=>{setBillNavigation({id:crypto.randomUUID(),target});openSection('insights');setAgentExpanded(false);if(!window.matchMedia('(min-width: 1101px)').matches)setAgentOpen(false);}} promptRequest={novaPrompts[0]} onPromptHandled={id => setNovaPrompts(items => items.filter(item => item.id !== id))} headerControls={desktopLayout?novaControls:null} pageContext={appView === 'thirdParty' ? thirdPartyPageContext : bankingSection === 'insights' ? billPageContext : bankingSection === 'cards' ? { version: 1, page: 'cards', cards: { selected_card_id: selectedCardId } } : bankingSection === 'subscriptions' ? { version: 1, page: 'subscriptions', subscriptions: { selected_subscription_id: activeSubscriptions.some(s => s.subscription_id === selectedSubscriptionId) ? selectedSubscriptionId : undefined } } : { version: 1, page: bankingSection === 'audit'||bankingSection === 'scheduled' ? 'home' : bankingSection }} key={user.user_id} name={user.name} userId={user.user_id} open={agentOpen} onClose={() => { if (window.matchMedia('(min-width: 1101px)').matches) setAgentExpanded(false); else setAgentOpen(false); }} onNavigate={section => section === 'services' ? openServices() : openSection(section)} onReviewCardOperation={reviewCardOperation} onTransferActivity={() => { void load(); }} />
    {mfaOpen && <Modal className="transfers-mfa" role="dialog" aria-modal="true" aria-label="身份验证" onClick={() => { if (!isActing) { setMfaOpen(false); setMfaCode(''); } }}>
      <div className="card" style={{ width: 360, maxWidth: '90vw' }} onClick={e => e.stopPropagation()}>
        <h2>🔐 身份验证</h2>
        {activeTask && <SecondFactor purpose="transfer" referenceId={activeTask.task_id} factor={mfaFactor} onChange={setMfaFactor} disabled={isActing} />}
        <input inputMode="numeric" maxLength={6} value={mfaCode} placeholder="6 位验证码" onChange={e => setMfaCode(e.target.value.replace(/\D/g, '').slice(0, 6))} onKeyDown={e => e.key === 'Enter' && submitMfa()} />
        <div className="confirm">
          <button className="secondary" disabled={isActing} onClick={() => { setMfaOpen(false); setMfaCode(''); }}>取消</button>
          <button disabled={isActing || !mfaFactor || mfaCode.length !== 6} onClick={submitMfa}>{isActing ? '验证中…' : '提交验证'}</button>
        </div>
      </div>
    </Modal>}
    {rebindConfirmOpen && <Modal onClick={() => setRebindConfirmOpen(false)}>
      <div className="card" role="dialog" aria-modal="true" aria-labelledby="rebind-title" style={{ width: 400, maxWidth: '90vw' }} onClick={e => e.stopPropagation()}>
        <h2 id="rebind-title">🔐 重新绑定验证器</h2>
        <p className="hint">你已经绑定过验证器。重新绑定会立即作废当前验证器里的 NOVA 条目，完成新绑定之前，大额转账、调高卡片额度等需要验证器的操作都无法进行。</p>
        <div className="confirm">
          <button className="secondary" onClick={() => setRebindConfirmOpen(false)}>保留当前验证器</button>
          <button onClick={() => void startEnroll()}>作废并重新绑定</button>
        </div>
      </div>
    </Modal>}
    {enrollOpen && enrollData && <Modal role="dialog" aria-modal="true" aria-label="绑定验证器" onClick={() => { if (!isActing) { setEnrollOpen(false); setEnrollData(null); setEnrollCode(''); } }}>
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
    </Modal>}
  </main>;
}

export default App;
