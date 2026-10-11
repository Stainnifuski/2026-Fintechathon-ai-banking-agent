import { FormEvent, useEffect, useRef, useState } from 'react';
import NovaMarkdown from './NovaMarkdown';
import { streamNova, NovaMessage, NovaReply, NovaPageContext, NovaStreamEvent } from '../services/nova';

type Entry = NovaMessage & { details?: NovaReply; streaming?: boolean; status?: string };
const TOOL_LABELS: Record<string, string> = {
  list_cards: '卡片列表', get_card: '卡片详情', list_subscriptions: '订阅列表与预计月费', get_subscription: '订阅详情',
  get_current_time: '时间查询', get_financial_overview: '账户概览', get_bill_analysis: '账单分析', list_transactions: '流水列表',
  get_transaction: '流水详情', list_calendar_events: '日程查询', search_calendar_events: '活动搜索', get_calendar_event: '活动详情',
  list_shop_products: '店铺商品', get_shop_product: '商品详情与报价', list_shop_orders: '本人店铺订单', get_shop_order: '订单付款状态',
  preview_card_operation: '卡片操作预览',
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
  if (call.name === 'get_transaction') text += ` · ${data?.tx_id} · ${data?.counterparty} · ${money(data?.amount_fen)}`;
  if (typeof result.as_of === 'string') text += `\n查询截止：${new Date(result.as_of).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}（北京时间）`;
  return text;
}

export default function NovaChat({ name, open, onClose, onNavigate, pageContext, onReviewCardOperation }: {
  name: string; open: boolean; onClose: () => void; pageContext: NovaPageContext;
  onNavigate: (section: 'transfer' | 'insights' | 'wealth') => void;
  onReviewCardOperation?: (operationId: string) => void;
}) {
  const [messages, setMessages] = useState<Entry[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const request = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => () => { request.current?.abort(); }, []);
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'nearest' }); }, [messages, busy, open]);

  async function send(text: string) {
    const content = text.trim();
    if (!content || busy || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    const history = messages.slice(-12).map(({ role, content }) => ({ role, content: content.slice(0, 4000) }));
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
      const reply = await streamNova(content, history, controller.signal, pageContext, onEvent);
      if (!controller.signal.aborted) updateDraft(() => ({ role: 'assistant', content: reply.reply, details: reply }));
    } catch (failure: unknown) {
      if (!controller.signal.aborted) {
        const detail = (failure as { response?: { data?: { detail?: { message?: string; code?: string } } } }).response?.data?.detail;
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
      <div className="agent-body">
        <p className="agent-greeting">你好，{name}。可以问我账户资金、账单、日历、店铺商品和订单。</p>
        <p className="hint">我会结合当前页面筛选查询本人数据。卡片操作我只生成待确认的变更，需你在卡片页本人确认；转账及其他操作请进入对应工作台。</p>
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
          <div ref={bottom} />
        </div>
      </div>
      <form className="agent-compose" onSubmit={submit}><input aria-label="向 NOVA 提问" maxLength={2000} value={input} disabled={busy} onChange={event => setInput(event.target.value)} placeholder="可用资金有多少？这笔流水是什么？"/><button type="submit" aria-label="发送" disabled={busy || !input.trim()}>↑</button></form>
  </aside>;
}
