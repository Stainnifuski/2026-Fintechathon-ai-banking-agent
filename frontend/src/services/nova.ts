import { apiClient } from './api';
import type { BillQuery } from './bills';

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

export type NovaMessage = { role: 'user' | 'assistant'; content: string };
export type NovaConversation = { session_id: string; title: string; created_at: string; updated_at: string };
export type NovaConversationDetail = NovaConversation & { messages: NovaMessage[] };
export type NovaTaskSession = { session_id: string; session_version: number; focus_task_id?: string | null; tasks: NovaTransferTask[] };

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
  reply: string;
  mode: 'model' | 'offline';
  tool_calls: { name: string; result: Record<string, unknown> & { error?: string; local_time?: string; timezone?: string } }[];
  session_id?: string;
  session_version?: number;
  focus_task_id?: string | null;
  tasks?: NovaTransferTask[];
};

export type NovaTransferTask = {
  chat_task_id: string; status: string; recipient?: string | null; amount_fen?: number | null;
  missing_fields: string[]; candidates: { candidate_id: string; label: string; kind: string }[];
  transfer_task_id?: string | null;
  task?: { task_id: string; message: string; phase: string; outcome: string; confirmation_card?: {
    recipient_name: string; amount: number; currency: string; payer_card_masked?: string | null;
    recipient_card_masked?: string | null; recipient_phone_masked?: string | null; note?: string | null;
  } | null } | null;
};

export type NovaTaskRequest = {
  request_id: string; session_id?: string; session_version?: number;
  focus_task_id?: string; candidate_id?: string;
};

export async function getNovaTaskSession(sessionId: string) {
  return apiClient.get<NovaTaskSession>(`/nova/task-sessions/${sessionId}`).then(r => r.data);
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
 * 流式提问：边接收边回调 tool/delta/reset，最终以 done 事件的完整结果为准。
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
