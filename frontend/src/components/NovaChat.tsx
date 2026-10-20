import { FormEvent, useEffect, useRef, useState } from 'react';
import NovaMarkdown from './NovaMarkdown';
import { streamNova, getNovaTaskSession, listNovaConversations, createNovaConversation, getNovaConversation, NovaConversation, NovaMessage, NovaReply, NovaPageContext, NovaStreamEvent, NovaTransferTask, NovaTaskRequest } from '../services/nova';

type Entry = NovaMessage & { details?: NovaReply; streaming?: boolean; status?: string };
const TOOL_LABELS: Record<string, string> = {
  list_cards: '卡片列表', get_card: '卡片详情', list_subscriptions: '订阅列表与预计月费', get_subscription: '订阅详情',
  get_current_time: '时间查询', get_financial_overview: '账户概览', get_bill_analysis: '账单分析', list_transactions: '流水列表',
  get_transaction: '流水详情', list_calendar_events: '日程查询', search_calendar_events: '活动搜索', get_calendar_event: '活动详情',
  list_shop_products: '店铺商品', get_shop_product: '商品详情与报价', list_shop_orders: '本人店铺订单', get_shop_order: '订单付款状态',
  preview_card_operation: '卡片操作预览', project_cashflow: '只读现金流情景', get_subscription_insights: '订阅扣费核对',
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
      : call.name === 'get_shop_order' ? `${shop?.order_id} · ${labels[shop?.status || ''] || shop?.status}` : `共 ${shop?.total ?? 0} 项匹配结果`;
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
  if (call.name === 'get_transaction') text += ` · ${data?.tx_id} · ${data?.counterparty} · ${money(data?.amount_fen)}`;
  if (typeof result.as_of === 'string') text += `\n查询截止：${new Date(result.as_of).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}（北京时间）`;
  return text;
}

export default function NovaChat({ name, userId, open, onClose, onNavigate, pageContext, onReviewCardOperation, onReviewTransfer }: {
  name: string; userId: string; open: boolean; onClose: () => void; pageContext: NovaPageContext;
  onNavigate: (section: 'transfer' | 'insights' | 'wealth') => void;
  onReviewCardOperation?: (operationId: string) => void;
  onReviewTransfer?: (taskId: string) => void;
}) {
  const [messages, setMessages] = useState<Entry[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [conversations, setConversations] = useState<NovaConversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [loadingConversation, setLoadingConversation] = useState(true);
  const [taskSession, setTaskSession] = useState<{ session_id: string; session_version: number; tasks: NovaTransferTask[]; focus_task_id?: string | null } | null>(null);
  const [focusTaskId, setFocusTaskId] = useState<string | undefined>();
  const request = useRef<AbortController | null>(null);
  const uncertainRetry = useRef<{ content: string; request: NovaTaskRequest } | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => () => { request.current?.abort(); }, []);
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
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'nearest' }); }, [messages, busy, open, taskSession, focusTaskId]);

  async function selectConversation(sessionId: string) {
    if (busy || loadingConversation || sessionId === activeConversationId) return;
    setLoadingConversation(true); setError('');
    try {
      const [conversation, session] = await Promise.all([getNovaConversation(sessionId), getNovaTaskSession(sessionId)]);
      setActiveConversationId(sessionId); setMessages(conversation.messages); setTaskSession(session);
      setFocusTaskId(session.focus_task_id || undefined); setInput(''); uncertainRetry.current = null;
    } catch { setError('读取历史对话失败，请稍后重试。'); }
    finally { setLoadingConversation(false); }
  }

  async function newConversation() {
    if (busy || loadingConversation) return;
    setLoadingConversation(true); setError('');
    try {
      const created = await createNovaConversation();
      setConversations(previous => [created, ...previous]); setActiveConversationId(created.session_id);
      setMessages([]); setTaskSession(null); setFocusTaskId(undefined); setInput(''); uncertainRetry.current = null;
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
    };
    const taskRequest = uncertainRetry.current?.content === content ? uncertainRetry.current.request : newRequest;
    // 先放一条草稿回答，流式片段逐步写入；完成后替换为带查询记录的正式回答。
    setMessages(previous => [...previous, { role: 'user', content }, { role: 'assistant', content: '', streaming: true }]);
    setInput(''); setError(''); setBusy(true);
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
        updateDraft(() => ({ role: 'assistant', content: reply.reply, details: reply }));
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

  function submit(event: FormEvent) { event.preventDefault(); void send(input); }
  if (!open) return null;
  return <aside id="nova-panel" className="agent-panel" aria-label="NOVA 对话">
      <div className="agent-head"><span className="agent-orb">✦</span><div><strong>NOVA</strong><small>银行与生活服务助手</small></div><button className="icon-button" aria-label="收起 NOVA" onClick={onClose}>×</button></div>
      <nav className="nova-conversation-nav" aria-label="NOVA 对话历史">
        <button type="button" className="secondary" disabled={busy || loadingConversation} onClick={() => void newConversation()}>＋ 新建对话</button>
        <div className="nova-conversation-list">
          {conversations.map(conversation => <button key={conversation.session_id} type="button"
            className={conversation.session_id === activeConversationId ? 'active' : ''}
            disabled={busy || loadingConversation} onClick={() => void selectConversation(conversation.session_id)}
            title={conversation.title}>{conversation.title}</button>)}
        </div>
      </nav>
      <div className="agent-body">
        <p className="agent-greeting">你好，{name}。可以问我账户资金、账单、日历、店铺商品和订单，也可以告诉我转账需求。</p>
        <p className="hint">转账信息齐全后会生成待确认任务；请核对任务卡并由本人完成确认及必要验证。</p>
        <button className="agent-prompt" disabled={busy || loadingConversation} onClick={() => void send('给张三转100元')}>发起转账示例 <span>→</span></button>
        <button className="agent-prompt" disabled={busy} onClick={() => void send('现在北京时间几点？')}>现在北京时间几点？ <span>→</span></button>
        <button className="agent-prompt" disabled={busy} onClick={() => void send('我现在有多少钱可以用？')}>查看可用资金 <span>→</span></button>
        <button className="agent-prompt" disabled={busy} onClick={() => void send('明天有什么日程安排？')}>明天有什么安排？ <span>→</span></button>
        {pageContext.page === 'cards' && <>
          <button className="agent-prompt" disabled={busy} onClick={() => void send('查看我的卡片状态和限额')}>查看我的卡片 <span>→</span></button>
          {pageContext.cards?.selected_card_id && <button className="agent-prompt" disabled={busy} onClick={() => void send('这张卡的状态和限额是多少？')}>查询选中卡片 <span>→</span></button>}
          {pageContext.cards?.selected_card_id && <button className="agent-prompt" disabled={busy} onClick={() => void send('帮我把这张卡临时锁上')}>锁定选中卡片（生成预览） <span>→</span></button>}
        </>}
        {pageContext.page === 'subscriptions' && <>
          <button className="agent-prompt" disabled={busy} onClick={() => void send('我的订阅每月预计要花多少钱？')}>查看订阅与预计月费 <span>→</span></button>
          {pageContext.subscriptions?.selected_subscription_id && <button className="agent-prompt" disabled={busy} onClick={() => void send('这个订阅什么时候扣费？')}>查询选中订阅 <span>→</span></button>}
        </>}
        {pageContext.shop && <>
          <button className="agent-prompt" disabled={busy} onClick={() => void send('这家店有哪些商品？')}>查看当前店铺商品 <span>→</span></button>
          <button className="agent-prompt" disabled={busy} onClick={() => void send('查看这家店我的订单和付款状态')}>查看我的店铺订单 <span>→</span></button>
          {pageContext.shop.selected?.type === 'product' && <button className="agent-prompt" disabled={busy} onClick={() => void send('这个商品多少钱，我选的数量总共多少钱？')}>查询选中商品价格 <span>→</span></button>}
          {pageContext.shop.selected?.type === 'order' && <button className="agent-prompt" disabled={busy} onClick={() => void send('这个订单付款了吗？')}>查询选中订单状态 <span>→</span></button>}
        </>}
        {pageContext.calendar && <>
          <button className="agent-prompt" disabled={busy} onClick={() => void send('这一天有什么规划？')}>查看日历选中日期 <span>→</span></button>
          {pageContext.calendar.selected_event && <button className="agent-prompt" disabled={busy} onClick={() => void send('这个活动什么时候举行，在哪里？')}>解释选中活动 <span>→</span></button>}
        </>}
        {pageContext.page === 'insights' && <>
          <button className="agent-prompt" disabled={busy} onClick={() => void send('解释当前筛选的账单')}>解释当前账单 <span>→</span></button>
          {pageContext.selected && <button className="agent-prompt" disabled={busy} onClick={() => void send('这笔流水是什么？')}>解释选中流水 <span>→</span></button>}
        </>}
        {(['transfer', 'insights', 'wealth'] as const).map((section, index) => <button key={section} className="agent-prompt" onClick={() => onNavigate(section)}>{['转账与收款', '账单洞察', '理财规划'][index]} <span>→</span></button>)}
        <div className="nova-messages" aria-live="polite">
          {messages.map((entry, index) => <div key={index} className={`nova-message nova-${entry.role}`}>
            <small>{entry.role === 'user' ? '你' : 'NOVA'}</small>
            {entry.role === 'assistant' ? (entry.content ? <NovaMarkdown content={entry.content} /> : null) : <p>{entry.content}</p>}
            {entry.streaming && <p className="hint" role="status">{entry.status ? `🔍 ${entry.status}…` : entry.content ? '▍' : 'NOVA 正在思考…'}</p>}
            {entry.details && <small>{entry.details.mode === 'offline' ? '离线规则' : '模型回答'}{entry.details.tool_calls.length > 0 && ` · 工具调用 ${entry.details.tool_calls.length} 次`}</small>}
            {entry.details?.tool_calls.map((call, callIndex) => <details key={callIndex}><summary>{TOOL_LABELS[call.name] || call.name}{call.result.error ? '（失败）' : ''}</summary><p>{queryRecord(call)}</p></details>)}
            {entry.details?.tool_calls.filter(call => call.name === 'preview_card_operation' && !call.result.error).map((call, callIndex) => {
              const preview = call.result.data as { operation_id: string; action_label: string };
              return <button key={`review-${callIndex}`} className="agent-prompt" onClick={() => onReviewCardOperation?.(preview.operation_id)}>去卡片页核对并确认：{preview.action_label} <span>→</span></button>;
            })}
          </div>)}
          {error && <p className="nova-error" role="alert">{error}</p>}
          {!!taskSession?.tasks.length && <section className="nova-transfer-tasks" aria-label="本次聊天的转账任务">
            <h3>转账任务（{taskSession.tasks.length}）</h3>
            {taskSession.tasks.slice().sort((a, b) => Number(a.chat_task_id === focusTaskId) - Number(b.chat_task_id === focusTaskId)).map(task => <div key={task.chat_task_id} className="card">
              <strong>任务 {task.chat_task_id.slice(0, 8)} · {task.recipient || '待提供收款人'}</strong>
              <p>{task.amount_fen ? money(task.amount_fen) : '待提供金额'} · {task.transfer_task_id ? task.status : '草稿'}</p>
              {!!task.missing_fields.length && <p>请补充：{task.missing_fields.join('、')}</p>}
              {task.task?.confirmation_card && <p>收款人：{task.task.confirmation_card.recipient_name} · 金额：{task.task.confirmation_card.amount.toFixed(2)} 元<br />
                收款账户：{task.task.confirmation_card.recipient_card_masked || task.task.confirmation_card.recipient_phone_masked || '请在确认页核对'} · 备注：{task.task.confirmation_card.note || '无'}</p>}
              <button className="secondary" disabled={busy} onClick={() => setFocusTaskId(task.chat_task_id)}>{focusTaskId === task.chat_task_id ? '当前补充对象' : '补充或修改这笔'}</button>
              {task.candidates.map(candidate => <button key={candidate.candidate_id} className="secondary" disabled={busy}
                onClick={() => void send(`选择${candidate.label}`, candidate.candidate_id, task.chat_task_id)}>{candidate.label}</button>)}
              {task.transfer_task_id && <button className="agent-prompt" onClick={() => onReviewTransfer?.(task.transfer_task_id!)}>前往转账页核对并确认 <span>→</span></button>}
            </div>)}
          </section>}
          <div ref={bottom} />
        </div>
      </div>
      <form className="agent-compose" onSubmit={submit}><input aria-label="向 NOVA 提问" maxLength={2000} value={input} disabled={busy || loadingConversation} onChange={event => setInput(event.target.value)} placeholder="给张三转100元；或者查询可用资金"/><button type="submit" aria-label="发送" disabled={busy || loadingConversation || !input.trim()}>↑</button></form>
  </aside>;
}
