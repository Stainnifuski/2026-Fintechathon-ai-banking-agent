import './Wealth/novaWealth.css';
import NovaVoiceInput from './NovaVoiceInput';
import {appendVoiceText} from '../utils/voiceInput';
import {flushNovaWealthView,downloadNovaDebug,getNovaDebugTrace,listNovaDebugTraces,type NovaDebugTrace} from '../services/nova';
import {getNovaTaskSwitch,type NovaTaskSwitch} from '../services/nova';
import {getNovaExecutionQueue,advanceNovaExecutionQueue,stopNovaExecutionQueue,type NovaExecutionQueue} from '../services/nova';
import {createPortal} from 'react-dom';
import {getNovaWealthCards,deleteNovaConversation,type NovaWealthCard as WealthCard} from '../services/nova';
import { FormEvent, useEffect, useRef, useState, lazy, Suspense } from 'react';
import NovaMarkdown from './NovaMarkdown';
import NovaTransferCard from './NovaTransferCard';
import NovaCollectionCard from './NovaCollectionCard';
import NovaCardOperationCard from './NovaCardOperationCard';
import NovaSubscriptionCancelCard from './NovaSubscriptionCancelCard';
import NovaCalendarCard from './NovaCalendarCard';
import NovaShopCard from './NovaShopCard';
import * as bank from '../services/bank';
import { streamNova, getNovaTaskSession, listNovaConversations, createNovaConversation, getNovaConversation, NovaConversation, NovaMessage, NovaReply, NovaPageContext, NovaStreamEvent, NovaTransferTask, NovaTaskRequest, NovaTaskSession, NovaCollectionCard as CollectionCard, NovaChatCardTask, NovaChatSubscriptionTask, NovaChatCalendarTask, NovaChatShopTask } from '../services/nova';

const NovaWealthCard=lazy(()=>import('./NovaWealthCard'));

type Entry = NovaMessage & { details?: NovaReply; streaming?: boolean; status?: string };
const TOOL_LABELS: Record<string, string> = {
  describe_bank_capabilities: '业务能力介绍',
  list_cards: '卡片列表', get_card: '卡片详情', list_subscriptions: '订阅列表与预计月费', get_subscription: '订阅详情',
  get_current_time: '时间查询', get_financial_overview: '账户概览', get_bill_analysis: '账单分析', list_transactions: '流水列表',
  get_transaction: '流水详情', list_calendar_events: '日程查询', search_calendar_events: '活动搜索', get_calendar_event: '活动详情',
  list_shop_products: '店铺商品', get_shop_product: '商品详情与报价', list_shop_orders: '本人店铺订单', get_shop_order: '订单付款状态',
  preview_card_operation: '卡片操作预览', request_subscription_cancellation: '取消订阅任务卡', manage_subscription_task: '订阅操作任务卡', manipulate_calender_event: '日程操作任务卡', prepare_shop_purchase: '店铺下单', list_card_transactions: '用卡记录', project_cashflow: '只读现金流情景', get_subscription_insights: '订阅扣费核对',
  manage_transfer_task: '转账任务规划', manage_collection_request: '收款草稿', manage_wealth_request:'理财任务',
};
const money = (value: unknown) => typeof value === 'number' ? (value / 100).toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' }) : '暂无';
function queryRecord(call: NovaReply['tool_calls'][number]): string {
  const result = call.result;
  if (result.error) return String(result.message || result.error);
  if (call.name === 'describe_bank_capabilities') {
    const catalog = result as { businesses?: unknown[] };
    return `当前已接入 ${catalog.businesses?.length ?? 0} 类业务`;
  }
  if (call.name === 'get_current_time') return `${result.local_time} · ${result.timezone}`;
  const data = result.data as { totals?: Record<string, unknown>; query?: { date?: string }; total?: number; page?: number; has_more?: boolean; tx_id?: string; counterparty?: string; amount_fen?: number } | undefined;
  if (call.name.includes('shop')) {
    const shop = result.data as { total?: number; order_id?: string; status?: string; requested_quantity?: number; estimated_total_fen?: number; product?: { name?: string; price_fen?: number } } | undefined;
    const labels: Record<string, string> = { PENDING_PAYMENT: '等待付款', COMPLETED: '交易完成', PAYMENT_CLOSED: '付款已关闭', REFUNDED: '已退款' };
    const fact = call.name === 'get_shop_product' ? `${shop?.product?.name} · 单价 ${money(shop?.product?.price_fen)} · ${shop?.requested_quantity}件报价 ${money(shop?.estimated_total_fen)}`
      : call.name === 'get_shop_order' ? `订单 · ${labels[shop?.status || ''] || '状态待核对'}` : `共 ${shop?.total ?? 0} 项匹配结果`;
    return `模拟店铺 · ${result.shop_name} · ${fact}`;
  }
  if (call.name.includes('calendar')) {
    const calendar = result.data as { total?: number; title?: string; keyword?: string; query?: { start?: string; end_exclusive?: string } } | undefined;
    const dateRange = calendar?.query ? `\n范围：${calendar.query.start} 至 ${calendar.query.end_exclusive}（不含终点）` : '';
    return `本人日历 · ${calendar?.title || calendar?.keyword || '日程查询'} · 共 ${calendar?.total ?? 0} 项${dateRange}\n时区：${result.timezone}`;
  }
  if (call.name === 'preview_card_operation') {
    const preview = result.data as { action_label?: string; card_number?: string | null; permission_level?: string; changes?: { label: string }[] } | undefined;
    return `模拟银行 · ${preview?.action_label}${preview?.card_number ? ` · ${preview.card_number}` : ''} · ${preview?.permission_level === 'red' ? '需确认 + 验证码' : '需本人确认'} · 尚未执行\n变更：${(preview?.changes ?? []).map(change => change.label).join('、')}`;
  }
  if (call.name === 'manage_transfer_task') {
    const transfer = result as { tasks?: NovaTransferTask[] };
    return `模拟银行 · 已规划 ${transfer.tasks?.length ?? 0} 笔转账任务 · 尚未执行`;
  }
  if (call.name === 'manage_collection_request') {
    const collection = result as { collection_cards?: CollectionCard[]; reply?: string; message?: string; error?: string };
    return `模拟银行 · ${collection.reply || collection.message || '请核对收款卡片'}${collection.error ? ' · 未修改草稿' : ''}`;
  }
  let text = '模拟银行数据';
  if (['list_cards', 'get_card', 'list_subscriptions', 'get_subscription'].includes(call.name)) {
    const item = result.data as { total?: number; card_number?: string; merchant_name?: string; status?: string; summary?: { monthly_commitment_fen?: number } };
    text += call.name.startsWith('list_') ? ` · 共 ${item.total ?? 0} 项匹配结果` : ` · ${item.card_number || item.merchant_name} · ${item.status}`;
    if (item.summary) text += ` · 预计月费 ${money(item.summary.monthly_commitment_fen)}（非已扣款）`;
  }
  if (call.name === 'get_financial_overview') text += ` · 储蓄账户可用 ${money(data?.totals?.available_fen)} · 占款 ${money(data?.totals?.held_fen)}`;
  if (call.name === 'get_bill_analysis') text += ` · ${data?.query?.date} · 消费原额 ${money(data?.totals?.consumption_fen)}`;
  if (call.name === 'list_transactions') text += ` · ${data?.query?.date} · 共 ${data?.total} 笔 · 第 ${data?.page} 页${data?.has_more ? '，还有下一页' : ''}`;
  if (call.name === 'project_cashflow') {
    const projection = result.data as { days?: number; baseline_end_fen?: number; scenario_end_fen?: number };
    text += ` · 未来${projection.days}天 · 基准估计 ${money(projection.baseline_end_fen)} · 假设情景 ${money(projection.scenario_end_fen)}`;
  }
  if (call.name === 'get_subscription_insights') text += ` · ${data?.total ?? 0}组关联记录，需本人核对`;
  if (call.name === 'get_transaction') text += ` · ${data?.counterparty || '交易对方'} · ${money(data?.amount_fen)}`;
  if (typeof result.as_of === 'string') text += `\n查询截止：${new Date(result.as_of).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}（北京时间）`;
  return text;
}

type Prompt = { label: string; text: string };
const HOME_PROMPTS: Prompt[] = [
  { label: '🧾 最近的钱花在哪里', text: '帮我看看最近的支出' },
  { label: '🔁 帮我发起一笔转账', text: '我想发起一笔转账' },
  { label: '💳 查看我的账户与卡片', text: '查看我的账户与卡片' },
  { label: '↻ 查看即将扣款的订阅', text: '有哪些订阅即将扣款' },
];
const COMMON_PROMPTS: Prompt[] = [
  { label: '可用资金', text: '我现在有多少钱可以用？' },
  { label: '明天的安排', text: '明天有什么日程安排？' },
  { label: '北京时间', text: '现在北京时间几点？' },
  { label: '发起转账', text: '我想发起一笔转账' },
];

function promptsFor(context: NovaPageContext): Prompt[] {
  const prompts: Prompt[] = [];
  if (context.page === 'cards') {
    if (context.cards?.selected_card_id) {

      prompts.push({ label: '这张卡的限额', text: '这张卡的状态和限额是多少？' },
        { label: '锁卡预览', text: '帮我把这张卡临时锁上' });
    }
    prompts.push({ label: '我的卡片', text: '查看我的卡片状态和限额' });
  }
  if (context.page === 'subscriptions') {
    if (context.subscriptions?.selected_subscription_id) {

      prompts.push({ label: '下次扣费时间', text: '这个订阅什么时候扣费？' });
    }
    prompts.push({ label: '预计订阅月费', text: '我的订阅每月预计要花多少钱？' });
  }
  if (context.page === 'insights') {

    if (context.selected) {

      prompts.push({ label: '解释这笔流水', text: '这笔流水是什么？' });
    }
    prompts.push({ label: '解释当前账单', text: '解释当前筛选的账单' });
  }
  if (context.shop) {

    if (context.shop.selected?.type === 'product') {

      prompts.push({ label: '所选商品报价', text: '这个商品多少钱，我选的数量总共多少钱？' });
    }
    if (context.shop.selected?.type === 'order') {

      prompts.push({ label: '这笔订单的付款状态', text: '这个订单付款了吗？' });
    }
    prompts.push({ label: '店铺商品', text: '这家店有哪些商品？' },
      { label: '我的店铺订单', text: '查看这家店我的订单和付款状态' });
  }
  if (context.calendar) {

    if (context.calendar.selected_event) {

      prompts.push({ label: '所选活动的时间地点', text: '这个活动什么时候举行，在哪里？' });
    }
    prompts.push({ label: '这一天的安排', text: '这一天有什么规划？' });
  }
  if(context.page==='wealth')prompts.push({label:'理财规划',text:'帮我做理财规划'},{label:'查债券基金',text:'推荐债券基金'},{label:'我的持仓',text:'查看我的理财持仓'});
  return prompts;
}

export default function NovaChat({ headerControls, name, userId, open, onClose, onNavigate, pageContext, onTransferActivity }: {
  headerControls?:HTMLElement|null; name: string; userId: string; open: boolean; onClose: () => void; pageContext: NovaPageContext;
  onNavigate: (section: 'transfer' | 'insights' | 'wealth') => void;
  onReviewCardOperation?: (operationId: string) => void;
  onTransferActivity?: () => void;
}) {
  const [deleteTarget,setDeleteTarget]=useState<NovaConversation|null>(null);
  const [wealthFocus,setWealthFocus]=useState<{taskId:string;stage:string;label:string}|null>(null);
  const [wealthCards,setWealthCards]=useState<WealthCard[]>([]);
  const [messages, setMessages] = useState<Entry[]>([]);
  const [input, setInput] = useState('');
  const [voiceBusy,setVoiceBusy]=useState(false);
  const [debugEnabled,setDebugEnabled]=useState(false);
  const [lastDebugRequest,setLastDebugRequest]=useState<string|null>(null);
  const [debugTrace,setDebugTrace]=useState<NovaDebugTrace|null>(null);
  const [debugError,setDebugError]=useState('');
  const [conversations, setConversations] = useState<NovaConversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [showHelp, setShowHelp] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  useEffect(() => { if (!open) setShowHistory(false); }, [open]);
  useEffect(() => { setShowHistory(false); setDebugEnabled(false); setLastDebugRequest(null); setDebugTrace(null); setDebugError(''); }, [userId]);
  useEffect(() => { setLastDebugRequest(null); setDebugTrace(null); }, [activeConversationId]);
  useEffect(() => {
    if (!debugEnabled || !activeConversationId) return;
    let live = true;
    void listNovaDebugTraces(activeConversationId).then(traces => {
      if (live) setLastDebugRequest(current => current ?? traces[0]?.request_id ?? null);
    }).catch(() => { if (live) setDebugError('读取会话调试记录列表失败。'); });
    return () => { live = false; };
  }, [debugEnabled, activeConversationId]);
  useEffect(() => {
    if (!debugEnabled || !lastDebugRequest) { setDebugTrace(null); return; }
    let live = true;
    setDebugError('');
    void getNovaDebugTrace(lastDebugRequest).then(trace => {
      if (live) setDebugTrace(trace);
    }).catch(() => { if (live) setDebugError('读取完整调试记录失败，请稍后重试。'); });
    return () => { live = false; };
  }, [debugEnabled, lastDebugRequest]);
  const prompts = [...promptsFor(pageContext), ...COMMON_PROMPTS];
  const contextKey = JSON.stringify({ ...pageContext, observed_as_of: undefined });
  useEffect(() => { setShowHelp(false); }, [contextKey]);
  const [taskSwitch,setTaskSwitch]=useState<NovaTaskSwitch|null>(null);
  useEffect(()=>{setTaskSwitch(null);if(!activeConversationId)return;let live=true;
    void getNovaTaskSwitch(activeConversationId).then(p=>{if(live)setTaskSwitch(p);}).catch(()=>{if(live)setError('任务切换状态读取失败，请重新打开对话核对。');});
    return()=>{live=false;};},[activeConversationId]);
  const [loadingConversation, setLoadingConversation] = useState(true);
  const [executionQueue,setExecutionQueue]=useState<NovaExecutionQueue|null>(null);
  const [queueBusy,setQueueBusy]=useState(false);
  const queueLock=useRef(false);
  const queueSession=useRef<string|null>(null);
  queueSession.current=activeConversationId;
  useEffect(()=>{setExecutionQueue(null);if(!activeConversationId)return;let live=true;
    void getNovaExecutionQueue(activeConversationId).then(q=>{if(live)setExecutionQueue(q);}).catch(()=>{if(live)setError('多操作办理进度读取失败，请重新打开对话核对。');});
    return()=>{live=false;};},[activeConversationId]);
  useEffect(()=>{
    if(!open||busy||loadingConversation||taskSwitch||!activeConversationId||!executionQueue||['completed','stopped','blocked'].includes(executionQueue.status))return;
    const session=activeConversationId;
    const timer=window.setTimeout(async()=>{
      if(queueLock.current)return;
      queueLock.current=true;setQueueBusy(true);
      const debugRequestId=debugEnabled?crypto.randomUUID():undefined;
      try{
        const latest=await getNovaExecutionQueue(session);
        if(!latest||queueSession.current!==session)return;
        const result=await advanceNovaExecutionQueue(session,latest.version,debugEnabled,debugRequestId);
        if(debugRequestId)setLastDebugRequest(debugRequestId);
        if(queueSession.current!==session)return;
        setExecutionQueue(result.execution_queue??null);
        if(result.wealth_cards)setWealthCards(result.wealth_cards);
        if(result.tasks&&result.session_version!==undefined)setTaskSession(previous=>({session_id:session,session_version:result.session_version!,tasks:result.tasks!,collection_cards:result.collection_cards??previous?.collection_cards??[],card_operations:previous?.card_operations,subscription_tasks:previous?.subscription_tasks,calendar_tasks:previous?.calendar_tasks,shop_tasks:previous?.shop_tasks,focus_task_id:result.focus_task_id}));
        if(result.execution_queue?.steps.some(step=>step.review_operation_id))void getNovaTaskSession(session).then(fresh=>{if(queueSession.current===session)setTaskSession(fresh);}).catch(()=>{});
      }catch{if(queueSession.current===session){
        if(debugRequestId)setLastDebugRequest(debugRequestId);
        try{setExecutionQueue(await getNovaExecutionQueue(session));}catch{setError('办理进度读取失败，已暂停自动接续，请重新打开对话核对原任务。');}
      }}finally{queueLock.current=false;setQueueBusy(false);}
    },4000);
    return()=>window.clearTimeout(timer);
  },[activeConversationId,executionQueue,open,busy,loadingConversation,taskSwitch,debugEnabled]);
  useEffect(()=>{setWealthFocus(null);},[activeConversationId]);
  const wealthSession=useRef<string|null>(null);
  useEffect(()=>{wealthSession.current=activeConversationId;setWealthCards([]);if(!activeConversationId)return;let live=true;void getNovaWealthCards(activeConversationId).then(cards=>{if(live)setWealthCards(cards);}).catch(()=>{if(live)setError('理财任务读取失败，请重新打开对话核对原任务。');});return()=>{live=false;};},[activeConversationId]);

  useEffect(()=>{if(wealthFocus&&!wealthCards.some(c=>c.task_id===wealthFocus.taskId&&!c.closed))setWealthFocus(null);},[wealthCards]);
  const [taskSession, setTaskSession] = useState<NovaTaskSession | null>(null);
  const [payerAccounts, setPayerAccounts] = useState<bank.Account[]>([]);
  const [focusTaskId, setFocusTaskId] = useState<string | undefined>();
  const request = useRef<AbortController | null>(null);
  const uncertainRetry = useRef<{ content: string; request: NovaTaskRequest } | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const composerInput = useRef<HTMLInputElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const taskAnchors = useRef<Record<string, HTMLDivElement | null>>({});
  const [pinnedTaskIds, setPinnedTaskIds] = useState<string[]>([]);
  useEffect(() => () => { request.current?.abort(); }, []);
  useEffect(() => {
    let cancelled = false;
    bank.accounts(userId).then(accounts => { if (!cancelled) setPayerAccounts(accounts); }).catch(() => { if (!cancelled) setPayerAccounts([]); });
    return () => { cancelled = true; };
  }, [userId]);
  useEffect(() => {
    let cancelled = false;
    setLoadingConversation(true);
    setMessages([]); setTaskSession(null); setActiveConversationId(null); setConversations([]);
    listNovaConversations().then(async items => {
      if (cancelled) return;
      setConversations(items);
      if (!items.length) return;
      const [conversation, session] = await Promise.all([
        getNovaConversation(items[0].session_id), getNovaTaskSession(items[0].session_id),
      ]);
      if (!cancelled) {
        setActiveConversationId(items[0].session_id);
        setMessages(conversation.messages);
        setTaskSession(session);
        setFocusTaskId(undefined);
      }
    }).catch(() => { if (!cancelled) setError('读取历史对话失败，请稍后重试。'); })
      .finally(() => { if (!cancelled) setLoadingConversation(false); });
    return () => { cancelled = true; };
  }, [userId]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'nearest' }); }, [messages, busy, open]);

  const visibleTasks = taskSession?.tasks.filter(task => task.status !== 'CANCELLED') || [];
  const taskGroups = new Map<number, NovaTransferTask[]>();
  for (const task of visibleTasks) {
    let position = task.origin_request_id ? messages.findIndex(entry =>
      entry.role === 'assistant' && entry.request_id === task.origin_request_id) : -1;
    if (position < 0) position = messages.findIndex(entry =>
      entry.role === 'assistant' && entry.content.includes(`任务 ${task.chat_task_id.slice(0, 8)}`));
    if (position < 0) position = messages.length - 1;
    taskGroups.set(position, [...(taskGroups.get(position) || []), task]);
  }
  const collectionGroups = new Map<number, CollectionCard[]>();
  for (const card of taskSession?.collection_cards || []) {
    let position = messages.findIndex(entry => entry.role === 'assistant' && entry.request_id === card.origin_request_id);
    if (position < 0) position = messages.length - 1;
    collectionGroups.set(position, [...(collectionGroups.get(position) || []), card]);
  }

  const shopGroups = new Map<number, NovaChatShopTask[]>();
  for (const task of taskSession?.shop_tasks || []) {
    let position = messages.findIndex(entry => entry.role === 'assistant' && entry.request_id === task.origin_request_id);
    if (position < 0) position = messages.length - 1;
    shopGroups.set(position, [...(shopGroups.get(position) || []), task]);
  }
  const calendarGroups = new Map<number, NovaChatCalendarTask[]>();
  for (const task of taskSession?.calendar_tasks || []) {
    let position = messages.findIndex(entry => entry.role === 'assistant' && entry.request_id === task.origin_request_id);
    if (position < 0) position = messages.length - 1;
    calendarGroups.set(position, [...(calendarGroups.get(position) || []), task]);
  }
  const subscriptionGroups = new Map<number, NovaChatSubscriptionTask[]>();
  for (const task of taskSession?.subscription_tasks || []) {
    let position = messages.findIndex(entry => entry.role === 'assistant' && entry.request_id === task.origin_request_id);
    if (position < 0) position = messages.length - 1;
    subscriptionGroups.set(position, [...(subscriptionGroups.get(position) || []), task]);
  }
  const cardOperationGroups = new Map<number, NovaChatCardTask[]>();
  for (const task of taskSession?.card_operations || []) {
    let position = messages.findIndex(entry => entry.role === 'assistant' && entry.request_id === task.origin_request_id);
    if (position < 0) position = messages.length - 1;
    cardOperationGroups.set(position, [...(cardOperationGroups.get(position) || []), task]);
  }

  function updatePinnedTasks() {
    const viewport = body.current?.getBoundingClientRect();
    if (!viewport) return;
    const next = visibleTasks.filter(task => task.missing_fields.length > 0 &&
      ['COLLECTING', 'NEEDS_SELECTION'].includes(task.status) &&
      (taskAnchors.current[task.chat_task_id]?.getBoundingClientRect().top ?? Infinity) < viewport.top + 8)
      .map(task => task.chat_task_id);
    setPinnedTaskIds(previous => previous.length === next.length && previous.every((id, index) => id === next[index])
      ? previous : next);
  }

  useEffect(() => {
    const frame = requestAnimationFrame(updatePinnedTasks);
    return () => cancelAnimationFrame(frame);
  }, [messages, taskSession, activeConversationId, open]);

  async function selectConversation(sessionId: string) {
    if (busy || loadingConversation || sessionId === activeConversationId) return;
    setLoadingConversation(true); setError(''); setShowHelp(false);
    try {
      const [conversation, session] = await Promise.all([getNovaConversation(sessionId), getNovaTaskSession(sessionId)]);
      setActiveConversationId(sessionId); setMessages(conversation.messages); setTaskSession(session);
      setFocusTaskId(undefined); setInput(''); uncertainRetry.current = null; setShowHistory(false);
    } catch { setError('读取历史对话失败，请稍后重试。'); }
    finally { setLoadingConversation(false); }
  }

  async function newConversation() {
    if (busy || loadingConversation) return;
    setLoadingConversation(true); setError(''); setShowHelp(false);
    try {
      const created = await createNovaConversation();
      setConversations(previous => [created, ...previous]); setActiveConversationId(created.session_id);
      setMessages([]); setTaskSession(null); setFocusTaskId(undefined); setInput(''); uncertainRetry.current = null; setShowHistory(false);
    } catch { setError('新建对话失败，请稍后重试。'); }
    finally { setLoadingConversation(false); }
  }

  async function send(text: string, candidateId?: string, selectedTaskId?: string, switchDecision?:{token:string;accept:boolean}) {
    const content = text.trim();
    if (!content || busy || queueLock.current || loadingConversation || request.current) return;
    if (uncertainRetry.current?.content !== content) uncertainRetry.current = null;
    const controller = new AbortController();
    request.current = controller;
    const newRequest: NovaTaskRequest = {
      request_id: crypto.randomUUID(), session_id: activeConversationId || undefined,
      session_version: taskSession?.session_version || 0,
      focus_task_id: selectedTaskId || focusTaskId,
      candidate_id: candidateId,
      wealth_task_id:wealthFocus?.taskId,wealth_stage:wealthFocus?.stage,
      task_switch_token:switchDecision?.token,accept_task_switch:switchDecision?.accept,
      debug:debugEnabled,
    };
    const taskRequest = uncertainRetry.current?.content === content ? uncertainRetry.current.request : newRequest;
    // 先放一条草稿回答，流式片段逐步写入；完成后替换为带查询记录的正式回答。
    setMessages(previous => [...previous, { role: 'user', content, request_id: taskRequest.request_id },
      { role: 'assistant', content: '', request_id: taskRequest.request_id, streaming: true }]);
    setInput(''); setError(''); setBusy(true); setShowHelp(false);
    const updateDraft = (change: (draft: Entry) => Entry) => setMessages(previous => {
      const last = previous[previous.length - 1];
      return last?.streaming ? [...previous.slice(0, -1), change(last)] : previous;
    });
    const onEvent = (event: NovaStreamEvent) => {
      if (event.type === 'delta') updateDraft(draft => ({ ...draft, content: draft.content + event.text, status: undefined }));
      else if (event.type === 'reset') updateDraft(draft => ({ ...draft, content: '' }));
      else updateDraft(draft => ({ ...draft, status: `正在查询：${TOOL_LABELS[event.name] || event.name}` }));
    };
    try {
      if (!taskRequest.session_id) {
        const created = await createNovaConversation();
        taskRequest.session_id = created.session_id;
        setActiveConversationId(created.session_id);
        setConversations(previous => [created, ...previous]);
      }
      await flushNovaWealthView(taskRequest.wealth_task_id);
      const reply = await streamNova(content, controller.signal, pageContext, onEvent, taskRequest);
      if(taskRequest.debug)setLastDebugRequest(taskRequest.request_id);
      if (!controller.signal.aborted) {
        uncertainRetry.current = null;
        setTaskSwitch(reply.task_switch??null);
        setWealthFocus(null);setFocusTaskId(undefined);
        if(reply.wealth_cards)setWealthCards(reply.wealth_cards);
        if(reply.execution_queue!==undefined)setExecutionQueue(reply.execution_queue);
        updateDraft(() => ({ role: 'assistant', content: reply.reply, request_id: taskRequest.request_id, details: reply }));
        if (reply.session_id && reply.session_version !== undefined && (reply.tasks || reply.collection_cards)) {
          setTaskSession(previous => ({ session_id: reply.session_id!, session_version: reply.session_version!,
            tasks: reply.tasks || [], collection_cards: reply.collection_cards || [],
            card_operations: previous?.session_id === reply.session_id ? previous?.card_operations : undefined,
            subscription_tasks: previous?.session_id === reply.session_id ? previous?.subscription_tasks : undefined,
            calendar_tasks: previous?.session_id === reply.session_id ? previous?.calendar_tasks : undefined,
            shop_tasks: previous?.session_id === reply.session_id ? previous?.shop_tasks : undefined,
            focus_task_id: reply.focus_task_id }));
          setFocusTaskId(undefined);
        }
        const sessionId = taskRequest.session_id;
        if (sessionId && reply.tool_calls.some(call => ['preview_card_operation', 'request_subscription_cancellation', 'manage_subscription_task', 'manipulate_calender_event', 'prepare_shop_purchase'].includes(call.name) && !call.result.error)) {
          // 卡片任务卡状态以服务端授权单为准，随会话一起读取。
          void getNovaTaskSession(sessionId).then(setTaskSession).catch(() => setError('卡片任务卡读取失败，请重新打开对话。'));
        }
        void listNovaConversations().then(setConversations).catch(() => {});
      }
    } catch (failure: unknown) {
      if(taskRequest.debug)setLastDebugRequest(taskRequest.request_id);
      if (!controller.signal.aborted) {
        const detail = (failure as { response?: { data?: { detail?: { message?: string; code?: string } } } }).response?.data?.detail;
        const status = (failure as { response?: { status?: number } }).response?.status;
        uncertainRetry.current = !status || status >= 500 ? { content, request: taskRequest } : null;
        setError(`${detail?.message || '查询失败，请重新发送。'}${detail?.code ? `（${detail.code}）` : ''}`);
        setInput(content);
        // 失败请求及其半截回答不进入下一轮上下文。
        setMessages(previous => previous[previous.length - 1]?.streaming ? previous.slice(0, -2) : previous.slice(0, -1));
      }
    } finally {
      if (!controller.signal.aborted) { setBusy(false); request.current = null; }
    }
  }

  async function removeConversation(){
    if(!deleteTarget||busy||loadingConversation)return;
    setLoadingConversation(true);setError('');
    try{
      await deleteNovaConversation(deleteTarget.session_id);
      const remaining=conversations.filter(c=>c.session_id!==deleteTarget.session_id);setConversations(remaining);
      if(activeConversationId===deleteTarget.session_id){
        request.current?.abort();uncertainRetry.current=null;setMessages([]);setTaskSession(null);setWealthCards([]);setWealthFocus(null);setActiveConversationId(null);setInput('');
        if(remaining.length){const [conversation,session]=await Promise.all([getNovaConversation(remaining[0].session_id),getNovaTaskSession(remaining[0].session_id)]);setMessages(conversation.messages);setTaskSession(session);setActiveConversationId(remaining[0].session_id);}
      }
      setDeleteTarget(null);
    }catch{setError('移除对话失败，请稍后重试。');}finally{setLoadingConversation(false);}
  }
  function submit(event: FormEvent) { event.preventDefault(); void send(input); }
  if (!open) return null;
  const historyButton=<button type="button" className="nova-history-toggle" title="展开新建对话和历史记录" aria-expanded={showHistory} aria-controls="nova-history" onClick={() => { setShowHistory(value => !value); setShowHelp(false); }}>＋ 新建/历史对话 <svg className="nova-history-chevron" aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg></button>;
  const historyMenu=showHistory && <nav id="nova-history" className="nova-conversation-nav" aria-label="NOVA 对话历史">
        <button type="button" className="secondary" disabled={busy || loadingConversation} onClick={() => void newConversation()}>＋ 新建对话</button>
        <div className="nova-conversation-list">
          {conversations.map(conversation => <div className="nova-history-item" key={conversation.session_id}><button type="button"
            className={conversation.session_id === activeConversationId ? 'active' : ''}
            disabled={busy || loadingConversation} onClick={() => { if (conversation.session_id === activeConversationId) setShowHistory(false); else void selectConversation(conversation.session_id); }}
            title={conversation.title}>{conversation.title}</button><button className="nova-history-remove" type="button" aria-label={`删除对话：${conversation.title}`} disabled={busy||loadingConversation} onClick={()=>setDeleteTarget(conversation)}>×</button></div>)}
        </div>
      </nav>;
  return <aside id="nova-panel" className="agent-panel" aria-label="NOVA 对话">
      <div className="agent-head"><span className="agent-orb">✦</span><div><div className="nova-title-row"><strong>NOVA</strong>{!headerControls&&historyButton}</div><small>你的银行智能体</small></div><button className="icon-button" aria-label="收起 NOVA" onClick={onClose}>×</button></div>
      {!headerControls&&historyMenu}
      {headerControls&&createPortal(<>{historyButton}{historyMenu}</>,headerControls)}
      <div className="agent-body" ref={body} onScroll={updatePinnedTasks}>
        {executionQueue&&<section className="nova-execution-queue" aria-label="多操作办理顺序">
          <strong>按以下顺序办理</strong>
          <ol>{executionQueue.steps.map(step=><li key={step.goal_id} aria-current={step.status==='waiting'?'step':undefined}>
            {step.objective} · {{pending:'待办理',running:'正在核对',waiting:'等待本人操作',completed:'已完成',blocked:'已暂停',stopped:'已停止'}[step.status]||step.status}
            {step.reply&&<details><summary>查看此步结果</summary><NovaMarkdown content={step.reply}/></details>}
            {step.status==='waiting'&&step.review_operation_id&&<small>请在对话中的卡片表单核对并本人确认</small>}
          </li>)}</ol>
          <p>{executionQueue.message}</p>
          {!['completed','stopped'].includes(executionQueue.status)&&<button type="button" disabled={busy||queueBusy} onClick={async()=>{
            if(!activeConversationId)return;
            try{setExecutionQueue(await stopNovaExecutionQueue(activeConversationId,executionQueue.version));}
            catch{setError('队列正在更新，请稍候再停止；已成交操作保留。');}
          }}>停止后续办理</button>}
        </section>}
        {pinnedTaskIds.length > 0 && <div className="nova-pinned-stack" aria-label="待补全的转账任务">
          <div className="nova-pinned-stack-content">
            {pinnedTaskIds.map(id => {
              const task = visibleTasks.find(item => item.chat_task_id === id);
              if (!task) return null;
              return <button type="button" key={id} className="nova-pinned-task" onClick={() =>
                taskAnchors.current[id]?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>
                <strong>{task.recipient || '待填写收款人'} · {task.form.amount_yuan ? `¥${task.form.amount_yuan}` : '待填写金额'}</strong>
                <small>还需：{task.missing_fields.join('、')} · 点击查看任务卡</small>
              </button>;
            })}
          </div>
        </div>}
        {messages.length === 0 && !error && <div className="nova-empty" aria-label={`${name}的银行助手`}><div className="eyebrow">YOUR AI BANKING AGENT</div><h1>今天想办理什么？</h1><p>查账、转账、管理财务，直接告诉 NOVA。</p><div className="nova-topic-ribbon"><span>💳 账户与卡片</span><span>🧾 账单洞察</span><span>📈 理财</span><span>🔁 转账</span></div></div>}
        <div className="nova-messages" aria-live="polite">
          {messages.map((entry, index) => <div key={`${entry.request_id || index}-${entry.role}-${index}`} className="nova-turn-entry">
            <div className={`nova-message nova-${entry.role}`}>
            <small>{entry.role === 'user' ? '你' : 'NOVA'}</small>
            {entry.role === 'assistant' ? (entry.content ? <NovaMarkdown content={entry.content} onConversation={id=>{if(id===activeConversationId)body.current?.scrollTo({top:0,behavior:'smooth'});else void selectConversation(id);}} /> : null) : <p>{entry.content}</p>}
            {entry.streaming && <p className="hint" role="status">{entry.status ? `🔍 ${entry.status}…` : entry.content ? '▍' : '正在处理你的问题…'}</p>}
            {entry.details && <small>{entry.details.mode === 'offline' ? '离线规则' : '模型回答'}{entry.details.tool_calls.length > 0 && ` · 处理记录 ${entry.details.tool_calls.length} 项`}</small>}
            {entry.details?.grounding && <small className="hint">{({
              grounded: '依据后端结果生成', partial: '部分结果未核实，请查看处理记录',
              abstained: '尚无可核验的业务结果', blocked: '本次请求已停止处理',
            })[entry.details.grounding.status]}</small>}
            {entry.details?.tool_calls.map((call, callIndex) => <details key={callIndex}><summary>{TOOL_LABELS[call.name] || call.name}{call.result.error ? '（失败）' : ''}</summary><p>{queryRecord(call)}</p></details>)}
            </div>
            {entry.role==='assistant'&&wealthCards.filter(card=>card.request_id===entry.request_id||(!messages.some(m=>m.role==='assistant'&&m.request_id===card.request_id)&&index===messages.length-1)).map(card=><Suspense fallback={<p>正在读取理财任务…</p>} key={`${card.task_id}:${card.request_id}:${!!card.paused}:${card.draft_epoch??0}`}><NovaWealthCard key={`${card.task_id}:${card.request_id}:${!!card.paused}:${card.draft_epoch??0}`} card={card} userId={userId} focusedStage={wealthFocus?.taskId===card.task_id?wealthFocus.stage:undefined} onFocus={(stage,label)=>{setFocusTaskId(undefined);setWealthFocus({taskId:card.task_id,stage,label});}} disabled={busy||loadingConversation} onUpdated={cards=>{if(wealthSession.current===card.nova_session_id)setWealthCards(cards);}} onActivity={()=>onTransferActivity?.()}/></Suspense>)}
            {taskGroups.get(index)?.map(task => <div key={task.chat_task_id} className="nova-task-anchor"
              ref={element => { taskAnchors.current[task.chat_task_id] = element; }}>
              <NovaTransferCard task={task} session={taskSession!} accounts={payerAccounts}
                focused={focusTaskId === task.chat_task_id} disabled={busy || loadingConversation}
                onUpdated={updated => { setTaskSession(updated); setFocusTaskId(updated.focus_task_id || undefined); }}
                onFocus={() => {setWealthFocus(null);setFocusTaskId(task.chat_task_id);}}
                onSelectCandidate={candidateId => void send('选择收款账户', candidateId, task.chat_task_id)}
                onTransferActivity={onTransferActivity} />
            </div>)}
            {collectionGroups.get(index)?.map(card => <NovaCollectionCard key={card.collection_id}
              card={card} session={taskSession!} disabled={busy || loadingConversation}
              onUpdated={updated => setTaskSession(previous => previous ? { ...previous, ...updated } : null)}
              onActivity={onTransferActivity} />)}
            {cardOperationGroups.get(index)?.map(task => <NovaCardOperationCard key={task.entry_id}
              task={task} sessionId={taskSession!.session_id} accounts={payerAccounts}
              disabled={busy || loadingConversation}
              onUpdated={tasks => setTaskSession(previous => previous ? { ...previous, card_operations: tasks } : previous)}
              onActivity={onTransferActivity} />)}
            {shopGroups.get(index)?.map(task => <NovaShopCard key={task.entry_id}
              task={task} sessionId={taskSession!.session_id} accounts={payerAccounts} disabled={busy || loadingConversation}
              onUpdated={tasks => setTaskSession(previous => previous ? { ...previous, shop_tasks: tasks } : previous)}
              onActivity={onTransferActivity} />)}
            {calendarGroups.get(index)?.map(task => <NovaCalendarCard key={task.entry_id}
              task={task} sessionId={taskSession!.session_id} disabled={busy || loadingConversation}
              onUpdated={tasks => setTaskSession(previous => previous ? { ...previous, calendar_tasks: tasks } : previous)} />)}
            {subscriptionGroups.get(index)?.map(task => <NovaSubscriptionCancelCard key={task.entry_id}
              task={task} sessionId={taskSession!.session_id} disabled={busy || loadingConversation}
              onUpdated={tasks => setTaskSession(previous => previous ? { ...previous, subscription_tasks: tasks } : previous)}
              onActivity={onTransferActivity} />)}
          </div>)}
          {error && <p className="nova-error" role="alert">{error}</p>}
          {taskSwitch&&<section className="wealth-step-card" role="dialog" aria-label="确认切换操作"><h3>切换操作？</h3><p>当前：{taskSwitch.current_label}<br/>转向：{taskSwitch.target_label}</p><p>确认后结束当前未提交任务，再开始目标操作。已成交结果保留；不能回退已到账或待核实的操作。</p><button disabled={busy||loadingConversation} onClick={()=>void send('继续当前任务',undefined,undefined,{token:taskSwitch.token,accept:false})}>继续当前任务</button><button disabled={busy||loadingConversation} onClick={()=>void send('确认切换操作',undefined,undefined,{token:taskSwitch.token,accept:true})}>确认切换</button></section>}
          <div ref={bottom} />
        </div>
      </div>
      <div className="agent-controls">
        <div className="nova-debug-row"><label><input type="checkbox" checked={debugEnabled} disabled={busy} onChange={event=>setDebugEnabled(event.target.checked)}/> 调试模式</label>
          {debugEnabled&&lastDebugRequest&&<button type="button" onClick={()=>void downloadNovaDebug(lastDebugRequest).catch(()=>setDebugError('调试记录下载失败，请稍后重试。'))}>下载副本</button>}
          {debugEnabled&&<small>后续请求会自动保存完整执行记录，并在下方直接显示；敏感凭据会隐藏。</small>}
          {debugEnabled&&debugError&&<small role="alert">{debugError}</small>}
          {debugEnabled&&debugTrace&&<details className="nova-debug-trace" open>
            <summary>调试记录 · {debugTrace.events.length} 个步骤 · {debugTrace.request_id}</summary>
            {debugTrace.events.map((event,index)=><div className="nova-debug-event" key={`${event.at}-${index}`}>
              <strong>{index+1}. {event.stage}</strong><time>{event.at}</time>
              <pre>{JSON.stringify(event.content,null,2)}</pre>
            </div>)}
          </details>}
        </div>
        <div className="nova-guide-row">
          <button type="button" className="nova-help-toggle" aria-expanded={showHelp} aria-controls="nova-help" onClick={() => { setShowHelp(value => !value); setShowHistory(false); }}>{showHelp ? '收起示例' : '更多示例'} <span aria-hidden="true">{showHelp ? '−' : '+'}</span></button>
        </div>
        {!showHelp && messages.length === 0 && pageContext.page !== 'home' && <div className="nova-quick-prompts" aria-label="快捷提问">
          {prompts.slice(0, 2).map(prompt => <button type="button" key={prompt.text} className="nova-chip" disabled={busy || loadingConversation} onClick={() => void send(prompt.text)}>问：{prompt.label}</button>)}
        </div>}
        {showHelp && <div id="nova-help" className="nova-help">
          <p>点击示例直接提问，会结合当前页面的筛选与选择。</p>
          <div className="nova-example-list" aria-label="示例提问">
            {prompts.map(prompt => <button type="button" key={prompt.text} className="nova-chip" disabled={busy || loadingConversation} onClick={() => void send(prompt.text)}>问：{prompt.label}</button>)}
          </div>
          <p>打开工作台，自行办理：</p>
          <div className="nova-workspace-links" aria-label="打开工作台">
            {(['transfer', 'insights', 'wealth'] as const).map((section, index) => <button type="button" key={section} onClick={() => { onNavigate(section); setShowHelp(false); }}>打开{['转账与收款', '账单洞察', '理财规划'][index]} <span aria-hidden="true">→</span></button>)}
          </div>
          <p>账户资金与交易均为模拟。卡片变更会先生成预览，需在卡片页核对、确认；转账与理财需求会生成待确认任务；理财问卷由本人填写，申购和赎回各自独立确认及MFA。</p>
        </div>}
      </div>
      {deleteTarget&&<div className="nova-delete-backdrop"><section role="dialog" aria-modal="true" aria-labelledby="nova-delete-title"><h3 id="nova-delete-title">永久移除这段对话？</h3><p>将删除“{deleteTarget.title}”的聊天记录和相关草稿缓存，无法恢复。已成交的银行记录、资金和审计保持。</p><button className="secondary" disabled={loadingConversation} onClick={()=>setDeleteTarget(null)}>取消</button><button disabled={loadingConversation} onClick={()=>void removeConversation()}>确认永久移除</button></section></div>}
      {wealthFocus&&<div className="nova-focus-hint">正在补充：{wealthFocus.label}<button type="button" onClick={()=>setWealthFocus(null)} aria-label="取消理财补充对象">×</button></div>}
      <form className="agent-compose" onSubmit={event=>{if(voiceBusy)event.preventDefault();else submit(event);}}><input ref={composerInput} aria-label="向 NOVA 提问" maxLength={2000} value={input} disabled={busy || loadingConversation} onChange={event => setInput(event.target.value)} placeholder="输入你的问题和需求"/><NovaVoiceInput key={`${userId}:${activeConversationId??'new'}`} disabled={busy||loadingConversation} onBusy={setVoiceBusy} onText={text=>{const next=appendVoiceText(input,text);if(next===null)return false;setInput(next);composerInput.current?.focus();return true;}}/><button type="submit" aria-label="发送" disabled={busy || loadingConversation || voiceBusy || !input.trim()}>↑</button></form>
      {!showHelp&&messages.length===0&&pageContext.page==='home'&&<div className="nova-home-suggestions" aria-label="开始对话">
        {HOME_PROMPTS.map(prompt=><button type="button" key={prompt.text} disabled={busy||loadingConversation} title="填入输入框，编辑后发送" onClick={()=>{setInput(prompt.text);composerInput.current?.focus();}}>{prompt.label}<span aria-hidden="true">↗</span></button>)}
      </div>}
  </aside>;
}
