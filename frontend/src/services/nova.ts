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
export type NovaReply = {
  reply: string;
  mode: 'model' | 'offline';
  tool_calls: { name: string; result: Record<string, unknown> & { error?: string; local_time?: string; timezone?: string } }[];
};

export async function askNova(message: string, history: NovaMessage[], signal: AbortSignal, pageContext: NovaPageContext): Promise<NovaReply> {
  const response = await apiClient.post<NovaReply>('/nova/chat', { message, history, page_context: pageContext }, {
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
export async function streamNova(message: string, history: NovaMessage[], signal: AbortSignal,
  pageContext: NovaPageContext, onEvent: (event: NovaStreamEvent) => void): Promise<NovaReply> {
  const token = localStorage.getItem('token');
  const response = await fetch('/api/v1/nova/chat/stream', {
    method: 'POST', signal,
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ message, history, page_context: pageContext }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401) localStorage.removeItem('token');
    throw novaError(response.status, typeof body?.detail === 'object' && body.detail ? body.detail : { message: '查询失败，请重新发送。' });
  }
  if (!response.body) return askNova(message, history, signal, pageContext);

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
