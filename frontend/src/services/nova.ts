import { apiClient } from './api';
import type { BillQuery } from './bills';
import type { BankCard, CardAction, CardOperation, Subscription, TaskSnapshot } from './bank';
import type { CalendarEvent } from './calendar';

export type NovaPageContext = {
  version: 1;
  page: 'home' | 'transfer' | 'insights' | 'cards' | 'subscriptions' | 'wealth' | 'thirdParty';
  cards?: { selected_card_id?: string };
  subscriptions?: { selected_subscription_id?: string };
  shop?: {
    shop: 'flower' | 'cake';
    selected?: { type: 'product' | 'order'; id: string; quantity?: number };
  };
  calendar?: {
    visible_month: string;
    selected_date: string;
    timezone: string;
    selected_event?: { event_id: string; starts_at: string };
  };
  filters?: BillQuery;
  details?: { category?: string; tx_ids: string[]; page: number };
  selected?: { type: 'transaction'; id: string };
  observed_as_of?: string;
};

export type NovaMessage = { role: 'user' | 'assistant'; content: string; request_id?: string };
export type NovaConversation = { session_id: string; title: string; created_at: string; updated_at: string };
export type NovaConversationDetail = NovaConversation & { messages: NovaMessage[] };
/** 聊天中的店铺订单：已下单时含订单（付款由本人在卡片中确认），否则为预填表单。 */
export type NovaChatShopTask = {
  entry_id: string; origin_request_id: string; shop?: 'flower' | 'cake' | null; shop_name?: string | null;
  prefill: { product_id?: string; quantity?: number }; notice?: string | null;
  order?: { order_id: string; product_id: string; product_name: string; quantity: number; unit_price_fen: number;
    total_fen: number; status: string; payment_request_id?: string | null } | null;
};
/** 聊天中的日程：信息完整时已创建（event），否则为预填表单（prefill）。 */
export type NovaChatCalendarTask = {
  entry_id: string; origin_request_id: string; timezone: string; notice?: string | null;
  prefill: { title?: string; start_date?: string; start_time?: string; end_date?: string; end_time?: string; location?: string; description?: string };
  event_id?: string | null; event?: CalendarEvent | null; deleted: boolean;
};
/** 聊天中的取消订阅任务：确认凭证只由本人页面向订阅接口获取。 */
export type NovaChatSubscriptionTask = {
  entry_id: string; origin_request_id: string; subscription_id?: string | null; subscription?: Subscription | null;
};
/** 聊天中的卡片任务：未生成预览时是可编辑表单，生成后由本人确认/验证。 */
export type NovaChatCardTask = {
  entry_id: string; origin_request_id: string; action: CardAction; card_id?: string | null;
  card?: BankCard | null; prefill: Record<string, string | boolean>; notice?: string | null;
  operation?: CardOperation | null;
};
export type NovaTaskSession = { session_id: string; session_version: number; focus_task_id?: string | null; tasks: NovaTransferTask[]; collection_cards: NovaCollectionCard[]; card_operations?: NovaChatCardTask[]; subscription_tasks?: NovaChatSubscriptionTask[]; calendar_tasks?: NovaChatCalendarTask[]; shop_tasks?: NovaChatShopTask[] };

export async function listNovaConversations(): Promise<NovaConversation[]> {
  return apiClient.get<NovaConversation[]>('/bank/nova/conversations').then(response => response.data);
}
export async function createNovaConversation(): Promise<NovaConversation> {
  return apiClient.post<NovaConversation>('/bank/nova/conversations').then(response => response.data);
}
export async function getNovaConversation(sessionId: string): Promise<NovaConversationDetail> {
  return apiClient.get<NovaConversationDetail>(`/bank/nova/conversations/${sessionId}`).then(response => response.data);
}
export type NovaReply = {
  task_switch?: NovaTaskSwitch | null;
  execution_queue?: NovaExecutionQueue | null;
  grounding?: {
    version: string;
    status: 'grounded' | 'partial' | 'abstained' | 'blocked';
    reason_codes: string[];
    sources: { evidence_id: string; tool: string; source: string; as_of: string | null; content_hash: string; outcome: string }[];
  };
  wealth_cards?: NovaWealthCard[];
  reply: string;
  mode: 'model' | 'offline';
  tool_calls: { name: string; result: Record<string, unknown> & { error?: string; local_time?: string; timezone?: string } }[];
  session_id?: string;
  session_version?: number;
  focus_task_id?: string | null;
  tasks?: NovaTransferTask[];
  collection_cards?: NovaCollectionCard[];
};

export type NovaExecutionQueue={queue_id:string;version:number;status:string;request_id:string;message:string;
  steps:{goal_id:string;objective:string;status:string;request_id:string;depends_on:string[];reply:string;review_operation_id?:string|null}[]};
export const getNovaExecutionQueue=(session:string)=>apiClient.get<NovaExecutionQueue|null>(`/nova/execution-queues/${session}`).then(r=>r.data);
export const advanceNovaExecutionQueue=(session:string,version:number,debug=false,debugRequestId?:string)=>apiClient.post<NovaReply>(`/nova/execution-queues/${session}/advance`,{version,debug,debug_request_id:debugRequestId},{timeout:95000}).then(r=>r.data);
export const stopNovaExecutionQueue=(session:string,version:number)=>apiClient.post<NovaExecutionQueue>(`/nova/execution-queues/${session}/stop`,{version}).then(r=>r.data);

export type NovaTransferTask = {
  chat_task_id: string; origin_request_id?: string | null; status: string; recipient?: string | null; amount_fen?: number | null;
  form: { recipient: string; amount_yuan: string;
    phone_masked?: string | null; card_masked?: string | null;
    payer_account_id: string; note: string };
  missing_fields: string[]; candidates: { candidate_id: string; label: string; kind: string }[];
  transfer_task_id?: string | null;
  task?: TaskSnapshot | null;
};

export type NovaCollectionCard = {
  collection_id: string; origin_request_id: string; status: 'DRAFT' | 'SENT' | 'CANCELLED';
  requested_names: string[]; selected_payer_user_ids: string[];
  receive_account_id: string; amount_fen: number | null; split_equally: boolean; note: string;
  contacts: { contact_id: string; user_id: string; name: string; nickname: string | null; label: string }[];
  accounts: { account_id: string; card_masked: string; account_type: string }[];
  unknown_names: string[]; ambiguous_names: string[]; payment_request_ids?: string[];
};

export const refreshNovaCollection = (sessionId: string, collectionId: string, sessionVersion: number) =>
  apiClient.post<NovaTaskSession>(`/nova/task-sessions/${sessionId}/collections/${collectionId}/refresh`,
    { session_version: sessionVersion }).then(r => r.data);

export const linkNovaCardTask = (sessionId: string, entryId: string, operationId: string) =>
  apiClient.post<{ card_operations: NovaChatCardTask[] }>(`/nova/task-sessions/${sessionId}/card-tasks/${entryId}/operation`,
    { operation_id: operationId }).then(r => r.data.card_operations);
export const dismissNovaCardTask = (sessionId: string, entryId: string) =>
  apiClient.post<{ card_operations: NovaChatCardTask[] }>(`/nova/task-sessions/${sessionId}/card-tasks/${entryId}/dismiss`)
    .then(r => r.data.card_operations);

export const selectNovaSubscriptionTask = (sessionId: string, entryId: string, subscriptionId: string) =>
  apiClient.post<{ subscription_tasks: NovaChatSubscriptionTask[] }>(`/nova/task-sessions/${sessionId}/subscription-tasks/${entryId}/select`,
    { subscription_id: subscriptionId }).then(r => r.data.subscription_tasks);
export const dismissNovaSubscriptionTask = (sessionId: string, entryId: string) =>
  apiClient.post<{ subscription_tasks: NovaChatSubscriptionTask[] }>(`/nova/task-sessions/${sessionId}/subscription-tasks/${entryId}/dismiss`)
    .then(r => r.data.subscription_tasks);

export const linkNovaCalendarTask = (sessionId: string, entryId: string, eventId: string) =>
  apiClient.post<{ calendar_tasks: NovaChatCalendarTask[] }>(`/nova/task-sessions/${sessionId}/calendar-tasks/${entryId}/event`,
    { event_id: eventId }).then(r => r.data.calendar_tasks);
export const dismissNovaCalendarTask = (sessionId: string, entryId: string) =>
  apiClient.post<{ calendar_tasks: NovaChatCalendarTask[] }>(`/nova/task-sessions/${sessionId}/calendar-tasks/${entryId}/dismiss`)
    .then(r => r.data.calendar_tasks);

export const linkNovaShopTask = (sessionId: string, entryId: string, shop: 'flower' | 'cake', orderId: string) =>
  apiClient.post<{ shop_tasks: NovaChatShopTask[] }>(`/nova/task-sessions/${sessionId}/shop-tasks/${entryId}/order`,
    { shop, order_id: orderId }).then(r => r.data.shop_tasks);
export const dismissNovaShopTask = (sessionId: string, entryId: string) =>
  apiClient.post<{ shop_tasks: NovaChatShopTask[] }>(`/nova/task-sessions/${sessionId}/shop-tasks/${entryId}/dismiss`)
    .then(r => r.data.shop_tasks);

export const sendNovaCollection = (sessionId: string, collectionId: string, payload: {
  session_version: number; payer_user_ids: string[]; receive_account_id: string;
  amount: number; split_equally: boolean; note: string;
}) => apiClient.post<NovaTaskSession>(`/nova/task-sessions/${sessionId}/collections/${collectionId}/send`, payload).then(r => r.data);

export type NovaTaskRequest = {
  debug?: boolean;
  task_switch_token?:string;accept_task_switch?:boolean;
  request_id: string; session_id?: string; session_version?: number;
  focus_task_id?: string; candidate_id?: string; wealth_task_id?:string; wealth_stage?:string;
};

export type NovaDebugTrace = {
  version: number; request_id: string; session_id?: string | null;
  started_at: string; truncated: boolean;
  events: { at: string; stage: string; content: unknown }[];
};
export type NovaDebugTraceSummary = {
  request_id: string; started_at: string; event_count: number; has_error: boolean;
};
export const getNovaDebugTrace = (requestId: string) =>
  apiClient.get<NovaDebugTrace>(`/nova/debug/${encodeURIComponent(requestId)}/view`,
    { headers: { 'Cache-Control': 'no-store' } }).then(r => r.data);
export const listNovaDebugTraces = (sessionId: string) =>
  apiClient.get<{ traces: NovaDebugTraceSummary[] }>(`/nova/debug/conversations/${encodeURIComponent(sessionId)}`,
    { headers: { 'Cache-Control': 'no-store' } }).then(r => r.data.traces);

export async function downloadNovaDebug(requestId: string): Promise<void> {
  const response = await apiClient.get<Blob>(`/nova/debug/${encodeURIComponent(requestId)}`, { responseType: 'blob' });
  const url = URL.createObjectURL(response.data);
  const link = document.createElement('a');
  link.href = url;
  link.download = `nova-debug-${requestId}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export type NovaTaskSwitch={token:string;current_label:string;target_label:string};
export const getNovaTaskSwitch=(session:string)=>apiClient.get<NovaTaskSwitch|null>(`/nova/task-flow/${session}`).then(r=>r.data);

export async function getNovaTaskSession(sessionId: string) {
  return apiClient.get<NovaTaskSession>(`/nova/task-sessions/${sessionId}`).then(r => r.data);
}

export type NovaTaskForm = {
  recipient: string; amount_yuan: string; phone?: string | null; card?: string | null;
  clear_phone: boolean; clear_card: boolean; payer_account_id: string; note: string;
};
export async function saveNovaTaskForm(sessionId: string, taskId: string, sessionVersion: number,
                                       form: NovaTaskForm, requestId = crypto.randomUUID()): Promise<NovaTaskSession> {
  return apiClient.post<NovaTaskSession>(`/nova/task-sessions/${sessionId}/tasks/${taskId}/form`, {
    request_id: requestId, session_version: sessionVersion, ...form,
  }).then(response => response.data);
}
export async function cancelNovaTaskCard(sessionId: string, taskId: string, sessionVersion: number,
                                         requestId = crypto.randomUUID()): Promise<NovaTaskSession> {
  return apiClient.post<NovaTaskSession>(`/nova/task-sessions/${sessionId}/tasks/${taskId}/cancel`, {
    request_id: requestId, session_version: sessionVersion,
  }).then(response => response.data);
}

export async function askNova(message: string, signal: AbortSignal, pageContext: NovaPageContext, taskRequest?: NovaTaskRequest): Promise<NovaReply> {
  const response = await apiClient.post<NovaReply>('/nova/chat', { message, page_context: pageContext, ...taskRequest }, {
    timeout: 95000, signal,
  });
  return response.data;
}

export type NovaStreamEvent =
  | { type: 'tool'; name: string }
  | { type: 'delta'; text: string }
  | { type: 'reset' };

type NovaErrorDetail = { code?: string; message?: string };
/** 与 axios 错误同形，调用方沿用 response.data.detail 读取错误信息。 */
const novaError = (status: number, detail: NovaErrorDetail) => Object.assign(new Error(detail.message || 'NOVA 请求失败'), {
  response: { status, data: { detail } },
});

/**
 * 流式提问：先回调工具进度，后端核验后的正文通过 delta 发布；done 为完整结果。
 * 浏览器不支持读取响应流时退回一次性接口。
 */
export async function streamNova(message: string, signal: AbortSignal,
  pageContext: NovaPageContext, onEvent: (event: NovaStreamEvent) => void, taskRequest?: NovaTaskRequest): Promise<NovaReply> {
  const token = localStorage.getItem('token');
  const response = await fetch('/api/v1/nova/chat/stream', {
    method: 'POST', signal,
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ message, page_context: pageContext, ...taskRequest }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401) localStorage.removeItem('token');
    throw novaError(response.status, typeof body?.detail === 'object' && body.detail ? body.detail : { message: '查询失败，请重新发送。' });
  }
  if (!response.body) return askNova(message, signal, pageContext, taskRequest);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    let boundary: number;
    while ((boundary = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = block.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n');
      if (!data) continue;
      const event = JSON.parse(data) as { type: string } & Record<string, unknown>;
      if (event.type === 'done') return event as unknown as NovaReply;
      if (event.type === 'error') throw novaError(503, event as NovaErrorDetail);
      onEvent(event as NovaStreamEvent);
    }
    if (done) throw novaError(503, { message: '连接中断，未收到完整回答，请重新发送。', code: 'NOVA_STREAM_INTERRUPTED' });
  }
}


export interface NovaWealthCommand {
 kind:'assets'|'positions'|'returns'|'detail'|'compare'|'assessment'|'recommend'|'planning'|'purchase'|'redeem'|'resume'|'abandon'|'clarify';
 products:{product_id:string;product_version:number;name:string}[];
 category?:string|null; cash_field?:'reserve'|'invest'|'total'|null; cash_yuan?:string|null;
 amounts_yuan:(string|null)[]; redemption_input:'units'|'market_value'|'holding_ratio';
 auto_prepare?:boolean;
 redemption_value?:string|null; redemption_mode:'standard'|'quick';
 period:'day'|'month'|'year'|'cumulative'; anchor_date?:string|null; question:string;
}
export interface NovaWealthCard {
 parent_task_id?:string;
 origin_kind?:'positions';
 task_id:string; version:number; nova_session_id:string; request_id:string; context_id:string;
 command:NovaWealthCommand; view_context?:NovaWealthView; closed:boolean; paused?:boolean; instruction?:{id:string;action:string;stage:string|null}; draft_epoch?:number; execution_plan_id?:string;
 pending_backtrack?:{token:string;stage:string;label:string;invalidated:string[]};
 reopen_stage?:string;
}
export const getNovaWealthCards=(session:string)=>apiClient.get<NovaWealthCard[]>(`/nova/wealth-sessions/${session}`).then(r=>r.data);
export const updateNovaWealthCard=(card:NovaWealthCard,change:{execution_plan_id?:string;closed?:boolean;paused?:boolean;reset_draft?:boolean;backtrack_stage?:string;backtrack_token?:string;accept_backtrack?:boolean})=>
 apiClient.post<NovaWealthCard[]>(`/nova/wealth-sessions/${card.nova_session_id}/cards/${card.task_id}`,{version:card.version,...change}).then(r=>r.data);

export type NovaWealthView={selection_kind?:'positions'|'products';completed_stages?:string[];cash_field?:'reserve'|'investment'|'total';cash_yuan?:string;amounts_yuan?:Record<string,string>;visible_products:NovaWealthCommand['products'];selected_products:NovaWealthCommand['products']};
const pendingWealthViews=new Map<string,Promise<unknown>>();
export function saveNovaWealthView(card:NovaWealthCard,view:NovaWealthView){
 const previous=pendingWealthViews.get(card.task_id);
 const next=(previous??Promise.resolve()).catch(()=>{}).then(()=>apiClient.post(`/nova/wealth-sessions/${card.nova_session_id}/cards/${card.task_id}/view`,{version:card.version,...view}));
 pendingWealthViews.set(card.task_id,next);void next.catch(()=>{});return next;
}
export async function flushNovaWealthView(taskId?:string){if(taskId)await pendingWealthViews.get(taskId);}

export const deleteNovaConversation=(session:string)=>apiClient.delete(`/bank/nova/conversations/${session}`);
