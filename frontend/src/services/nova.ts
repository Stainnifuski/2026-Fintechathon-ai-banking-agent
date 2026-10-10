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
