import { FormEvent, useEffect, useRef, useState } from 'react';
import NovaMarkdown from './NovaMarkdown';
import { streamNova, NovaMessage, NovaReply, NovaPageContext, NovaStreamEvent } from '../services/nova';

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

type Prompt = { label: string; text: string };
const COMMON_PROMPTS: Prompt[] = [
  { label: '可用资金', text: '我现在有多少钱可以用？' },
  { label: '明天的安排', text: '明天有什么日程安排？' },
  { label: '北京时间', text: '现在北京时间几点？' },
];

function guideFor(context: NovaPageContext): { label: string; prompts: Prompt[] } {
  const prompts: Prompt[] = [];
  const pages = { home: '财务概览', transfer: '转账与收款', insights: '账单洞察', cards: '卡片管理',
    subscriptions: '订阅管理', wealth: '理财规划', thirdParty: '生活服务' };
  let label: string = pages[context.page];
  if (context.page === 'cards') {
    if (context.cards?.selected_card_id) {
      label += ' · 已选卡片';
      prompts.push({ label: '这张卡的限额', text: '这张卡的状态和限额是多少？' },
        { label: '锁卡预览', text: '帮我把这张卡临时锁上' });
    }
    prompts.push({ label: '我的卡片', text: '查看我的卡片状态和限额' });
  }
  if (context.page === 'subscriptions') {
    if (context.subscriptions?.selected_subscription_id) {
      label += ' · 已选订阅';
      prompts.push({ label: '下次扣费时间', text: '这个订阅什么时候扣费？' });
    }
    prompts.push({ label: '预计订阅月费', text: '我的订阅每月预计要花多少钱？' });
  }
  if (context.page === 'insights') {
    label += context.filters?.date ? ' · ' + context.filters.date : '';
    if (context.selected) {
      label += ' · 已选流水';
      prompts.push({ label: '解释这笔流水', text: '这笔流水是什么？' });
    }
    prompts.push({ label: '解释当前账单', text: '解释当前筛选的账单' });
  }
  if (context.shop) {
    label = context.shop.shop === 'flower' ? '鲜花店' : '蛋糕店';
    if (context.shop.selected?.type === 'product') {
      label += ' · 已选商品';
      prompts.push({ label: '所选商品报价', text: '这个商品多少钱，我选的数量总共多少钱？' });
    }
    if (context.shop.selected?.type === 'order') {
      label += ' · 已选订单';
      prompts.push({ label: '这笔订单的付款状态', text: '这个订单付款了吗？' });
    }
    prompts.push({ label: '店铺商品', text: '这家店有哪些商品？' },
      { label: '我的店铺订单', text: '查看这家店我的订单和付款状态' });
  }
  if (context.calendar) {
    label = '日历 · ' + context.calendar.selected_date;
    if (context.calendar.selected_event) {
      label += ' · 已选活动';
      prompts.push({ label: '所选活动的时间地点', text: '这个活动什么时候举行，在哪里？' });
    }
    prompts.push({ label: '这一天的安排', text: '这一天有什么规划？' });
  }
  return { label, prompts };
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
  const [showHelp, setShowHelp] = useState(false);
  const guide = guideFor(pageContext);
  const prompts = [...guide.prompts, ...COMMON_PROMPTS];
  const contextKey = JSON.stringify({ ...pageContext, observed_as_of: undefined });
  useEffect(() => { setShowHelp(false); }, [contextKey]);
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
        {messages.length === 0 && !error && <div className="nova-empty"><p>{name}，想了解什么？</p><small>直接输入问题，或从下方示例开始。</small></div>}
        <div className="nova-messages" aria-live="polite">
          {messages.map((entry, index) => <div key={index} className={`nova-message nova-${entry.role}`}>
            <small>{entry.role === 'user' ? '你' : 'NOVA'}</small>
            {entry.role === 'assistant' ? (entry.content ? <NovaMarkdown content={entry.content} /> : null) : <p>{entry.content}</p>}
            {entry.streaming && <p className="hint" role="status">{entry.status ? `🔍 ${entry.status}…` : entry.content ? '▍' : '正在处理你的问题…'}</p>}
            {entry.details && <small>{entry.details.mode === 'offline' ? '离线规则' : '模型回答'}{entry.details.tool_calls.length > 0 && ` · 处理记录 ${entry.details.tool_calls.length} 项`}</small>}
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
      <div className="agent-controls">
        <div className="nova-guide-row">
          <span className="nova-context" title={guide.label}>{guide.label}</span>
          <button type="button" className="nova-help-toggle" aria-expanded={showHelp} aria-controls="nova-help" onClick={() => setShowHelp(value => !value)}>{showHelp ? '收起示例' : '更多示例'} <span aria-hidden="true">{showHelp ? '−' : '+'}</span></button>
        </div>
        {!showHelp && messages.length === 0 && <div className="nova-quick-prompts" aria-label="快捷提问">
          {prompts.slice(0, 2).map(prompt => <button type="button" key={prompt.text} className="nova-chip" disabled={busy} onClick={() => void send(prompt.text)}>问：{prompt.label}</button>)}
        </div>}
        {showHelp && <div id="nova-help" className="nova-help">
          <p>点击示例直接提问，会结合当前页面的筛选与选择。</p>
          <div className="nova-example-list" aria-label="示例提问">
            {prompts.map(prompt => <button type="button" key={prompt.text} className="nova-chip" disabled={busy} onClick={() => void send(prompt.text)}>问：{prompt.label}</button>)}
          </div>
          <p>打开工作台，自行办理：</p>
          <div className="nova-workspace-links" aria-label="打开工作台">
            {(['transfer', 'insights', 'wealth'] as const).map((section, index) => <button type="button" key={section} onClick={() => { onNavigate(section); setShowHelp(false); }}>打开{['转账与收款', '账单洞察', '理财规划'][index]} <span aria-hidden="true">→</span></button>)}
          </div>
          <p>账户资金与交易均为模拟。卡片变更会先生成预览，需在卡片页核对、确认；其他业务请到相应工作台办理。</p>
        </div>}
        <small className="nova-confirm-note">变更需本人确认，必要时还需验证码。</small>
      </div>
      <form className="agent-compose" onSubmit={submit}><input aria-label="向 NOVA 提问" maxLength={2000} value={input} disabled={busy} onChange={event => setInput(event.target.value)} placeholder="输入问题，例如：可用资金有多少？"/><button type="submit" aria-label="发送" disabled={busy || !input.trim()}>↑</button></form>
  </aside>;
}
