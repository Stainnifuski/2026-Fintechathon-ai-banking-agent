import './Wealth/novaWealth.css';
const NovaWealthCard=lazy(()=>import('./NovaWealthCard'));
import {getNovaWealthCards,deleteNovaConversation,type NovaWealthCard as WealthCard} from '../services/nova';
import { FormEvent, useEffect, useRef, useState, lazy, Suspense } from 'react';
import NovaMarkdown from './NovaMarkdown';
import NovaTransferCard from './NovaTransferCard';
import * as bank from '../services/bank';
import { streamNova, getNovaTaskSession, listNovaConversations, createNovaConversation, getNovaConversation, NovaConversation, NovaMessage, NovaReply, NovaPageContext, NovaStreamEvent, NovaTransferTask, NovaTaskRequest } from '../services/nova';

type Entry = NovaMessage & { details?: NovaReply; streaming?: boolean; status?: string };
const TOOL_LABELS: Record<string, string> = {
  list_cards: '卡片列表', get_card: '卡片详情', list_subscriptions: '订阅列表与预计月费', get_subscription: '订阅详情',
  get_current_time: '时间查询', get_financial_overview: '账户概览', get_bill_analysis: '账单分析', list_transactions: '流水列表',
  get_transaction: '流水详情', list_calendar_events: '日程查询', search_calendar_events: '活动搜索', get_calendar_event: '活动详情',
  list_shop_products: '店铺商品', get_shop_product: '商品详情与报价', list_shop_orders: '本人店铺订单', get_shop_order: '订单付款状态',
  preview_card_operation: '卡片操作预览', project_cashflow: '只读现金流情景', get_subscription_insights: '订阅扣费核对',
  manage_transfer_request: '转账任务规划', manage_wealth_request:'理财任务',
};
const money = (value: unknown) => typeof value === 'number' ? (value / 100).toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' }) : '暂无';
function queryRecord(call: NovaReply['tool_calls'][number]): string {
  const result = call.result;
  if (result.error) return String(result.message || result.error);
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
  if (call.name === 'manage_transfer_request') {
    const transfer = result as { tasks?: NovaTransferTask[] };
    return `模拟银行 · 已规划 ${transfer.tasks?.length ?? 0} 笔转账任务 · 尚未执行`;
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
const COMMON_PROMPTS: Prompt[] = [
  { label: '可用资金', text: '我现在有多少钱可以用？' },
  { label: '明天的安排', text: '明天有什么日程安排？' },
  { label: '北京时间', text: '现在北京时间几点？' },
  { label: '转账需求示例', text: '给张三转100元' },
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

export default function NovaChat({ name, userId, open, onClose, onNavigate, pageContext, onReviewCardOperation, onTransferActivity }: {
  name: string; userId: string; open: boolean; onClose: () => void; pageContext: NovaPageContext;
  onNavigate: (section: 'transfer' | 'insights' | 'wealth') => void;
  onReviewCardOperation?: (operationId: string) => void;
  onTransferActivity?: () => void;
}) {
  const [deleteTarget,setDeleteTarget]=useState<NovaConversation|null>(null);
  const [wealthFocus,setWealthFocus]=useState<{taskId:string;stage:string;label:string}|null>(null);
  const [wealthCards,setWealthCards]=useState<WealthCard[]>([]);
  const [messages, setMessages] = useState<Entry[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [showHelp, setShowHelp] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  useEffect(() => { if (!open) setShowHistory(false); }, [open]);
  useEffect(() => { setShowHistory(false); }, [userId]);
  const prompts = [...promptsFor(pageContext), ...COMMON_PROMPTS];
  const contextKey = JSON.stringify({ ...pageContext, observed_as_of: undefined });
  useEffect(() => { setShowHelp(false); }, [contextKey]);
  const [conversations, setConversations] = useState<NovaConversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  useEffect(()=>{setWealthFocus(null);},[activeConversationId]);
  const wealthSession=useRef<string|null>(null);
  useEffect(()=>{wealthSession.current=activeConversationId;setWealthCards([]);if(!activeConversationId)return;let live=true;void getNovaWealthCards(activeConversationId).then(cards=>{if(live)setWealthCards(cards);}).catch(()=>{if(live)setError('理财任务读取失败，请重新打开对话核对原任务。');});return()=>{live=false;};},[activeConversationId]);

  useEffect(()=>{if(wealthFocus&&!wealthCards.some(c=>c.task_id===wealthFocus.taskId&&!c.closed))setWealthFocus(null);},[wealthCards]);
  const [loadingConversation, setLoadingConversation] = useState(true);
  const [taskSession, setTaskSession] = useState<{ session_id: string; session_version: number; tasks: NovaTransferTask[]; focus_task_id?: string | null } | null>(null);
  const [payerAccounts, setPayerAccounts] = useState<bank.Account[]>([]);
  const [focusTaskId, setFocusTaskId] = useState<string | undefined>();
  const request = useRef<AbortController | null>(null);
  const uncertainRetry = useRef<{ content: string; request: NovaTaskRequest } | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
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
        setFocusTaskId(session.focus_task_id || undefined);
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
      setFocusTaskId(session.focus_task_id || undefined); setInput(''); uncertainRetry.current = null; setShowHistory(false);
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

  async function send(text: string, candidateId?: string, selectedTaskId?: string) {
    const content = text.trim();
    if (!content || busy || loadingConversation || request.current) return;
    if (uncertainRetry.current?.content !== content) uncertainRetry.current = null;
    const controller = new AbortController();
    request.current = controller;
    const newRequest: NovaTaskRequest = {
      request_id: crypto.randomUUID(), session_id: activeConversationId || undefined,
      session_version: taskSession?.session_version || 0,
      focus_task_id: selectedTaskId || focusTaskId,
      candidate_id: candidateId,
      wealth_task_id:wealthFocus?.taskId,wealth_stage:wealthFocus?.stage,
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
      const reply = await streamNova(content, controller.signal, pageContext, onEvent, taskRequest);
      if (!controller.signal.aborted) {
        uncertainRetry.current = null;
        if(reply.wealth_cards)setWealthCards(reply.wealth_cards);
        updateDraft(() => ({ role: 'assistant', content: reply.reply, request_id: taskRequest.request_id, details: reply }));
        if (reply.session_id && reply.session_version !== undefined && reply.tasks) {
          setTaskSession({ session_id: reply.session_id, session_version: reply.session_version,
            tasks: reply.tasks, focus_task_id: reply.focus_task_id });
          setFocusTaskId(reply.focus_task_id || undefined);
        }
        void listNovaConversations().then(setConversations).catch(() => {});
      }
    } catch (failure: unknown) {
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
  return <aside id="nova-panel" className="agent-panel" aria-label="NOVA 对话">
      <div className="agent-head"><span className="agent-orb">✦</span><div><div className="nova-title-row"><strong>NOVA</strong><button type="button" className="nova-history-toggle" title="展开新建对话和历史记录" aria-expanded={showHistory} aria-controls="nova-history" onClick={() => { setShowHistory(value => !value); setShowHelp(false); }}>＋ 新建对话 <svg className="nova-history-chevron" aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg></button></div><small>银行与生活服务助手</small></div><button className="icon-button" aria-label="收起 NOVA" onClick={onClose}>×</button></div>
      {showHistory && <nav id="nova-history" className="nova-conversation-nav" aria-label="NOVA 对话历史">
        <button type="button" className="secondary" disabled={busy || loadingConversation} onClick={() => void newConversation()}>＋ 新建对话</button>
        <div className="nova-conversation-list">
          {conversations.map(conversation => <div className="nova-history-item" key={conversation.session_id}><button type="button"
            className={conversation.session_id === activeConversationId ? 'active' : ''}
            disabled={busy || loadingConversation} onClick={() => { if (conversation.session_id === activeConversationId) setShowHistory(false); else void selectConversation(conversation.session_id); }}
            title={conversation.title}>{conversation.title}</button><button className="nova-history-remove" type="button" aria-label={`删除对话：${conversation.title}`} disabled={busy||loadingConversation} onClick={()=>setDeleteTarget(conversation)}>×</button></div>)}
        </div>
      </nav>}
      <div className="agent-body" ref={body} onScroll={updatePinnedTasks}>
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
        {messages.length === 0 && !error && <div className="nova-empty"><p>{name}，想了解什么？</p><small>直接输入问题，或从下方示例开始。</small></div>}
        <div className="nova-messages" aria-live="polite">
          {messages.map((entry, index) => <div key={`${entry.request_id || index}-${entry.role}-${index}`} className="nova-turn-entry">
            <div className={`nova-message nova-${entry.role}`}>
            <small>{entry.role === 'user' ? '你' : 'NOVA'}</small>
            {entry.role === 'assistant' ? (entry.content ? <NovaMarkdown content={entry.content} /> : null) : <p>{entry.content}</p>}
            {entry.streaming && <p className="hint" role="status">{entry.status ? `🔍 ${entry.status}…` : entry.content ? '▍' : '正在处理你的问题…'}</p>}
            {entry.details && <small>{entry.details.mode === 'offline' ? '离线规则' : '模型回答'}{entry.details.tool_calls.length > 0 && ` · 处理记录 ${entry.details.tool_calls.length} 项`}</small>}
            {entry.details?.tool_calls.map((call, callIndex) => <details key={callIndex}><summary>{TOOL_LABELS[call.name] || call.name}{call.result.error ? '（失败）' : ''}</summary><p>{queryRecord(call)}</p></details>)}
            {entry.details?.tool_calls.filter(call => call.name === 'preview_card_operation' && !call.result.error).map((call, callIndex) => {
              const preview = call.result.data as { operation_id: string; action_label: string };
              return <button key={`review-${callIndex}`} className="agent-prompt" onClick={() => onReviewCardOperation?.(preview.operation_id)}>去卡片页核对并确认：{preview.action_label} <span>→</span></button>;
            })}
            </div>
            {entry.role==='assistant'&&wealthCards.filter(card=>card.request_id===entry.request_id||(!messages.some(m=>m.role==='assistant'&&m.request_id===card.request_id)&&index===messages.length-1)).map(card=><Suspense fallback={<p>正在读取理财任务…</p>} key={`${card.task_id}:${card.request_id}:${!!card.paused}:${card.draft_epoch??0}`}><NovaWealthCard key={`${card.task_id}:${card.request_id}:${!!card.paused}:${card.draft_epoch??0}`} card={card} userId={userId} focusedStage={wealthFocus?.taskId===card.task_id?wealthFocus.stage:undefined} onFocus={(stage,label)=>{setFocusTaskId(undefined);setWealthFocus({taskId:card.task_id,stage,label});}} disabled={busy||loadingConversation} onUpdated={cards=>{if(wealthSession.current===card.nova_session_id)setWealthCards(cards);}} onActivity={()=>onTransferActivity?.()}/></Suspense>)}
            {taskGroups.get(index)?.map(task => <div key={task.chat_task_id} className="nova-task-anchor"
              ref={element => { taskAnchors.current[task.chat_task_id] = element; }}>
              <NovaTransferCard task={task} session={taskSession!} accounts={payerAccounts}
                focused={focusTaskId === task.chat_task_id} disabled={busy || loadingConversation}
                onUpdated={updated => { setTaskSession(updated); setFocusTaskId(updated.focus_task_id || undefined); }}
                onFocus={() => setFocusTaskId(task.chat_task_id)}
                onSelectCandidate={candidateId => void send('选择收款账户', candidateId, task.chat_task_id)}
                onTransferActivity={onTransferActivity} />
            </div>)}
          </div>)}
          {error && <p className="nova-error" role="alert">{error}</p>}
          <div ref={bottom} />
        </div>
      </div>
      <div className="agent-controls">
        <div className="nova-guide-row">
          <button type="button" className="nova-help-toggle" aria-expanded={showHelp} aria-controls="nova-help" onClick={() => { setShowHelp(value => !value); setShowHistory(false); }}>{showHelp ? '收起示例' : '更多示例'} <span aria-hidden="true">{showHelp ? '−' : '+'}</span></button>
        </div>
        {!showHelp && messages.length === 0 && <div className="nova-quick-prompts" aria-label="快捷提问">
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
      <form className="agent-compose" onSubmit={submit}><input aria-label="向 NOVA 提问" maxLength={2000} value={input} disabled={busy || loadingConversation} onChange={event => setInput(event.target.value)} placeholder="输入你的问题或需求"/><button type="submit" aria-label="发送" disabled={busy || loadingConversation || !input.trim()}>↑</button></form>
  </aside>;
}
