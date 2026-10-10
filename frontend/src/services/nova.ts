import { apiClient } from './api';

export type NovaMessage = { role: 'user' | 'assistant'; content: string };
export type NovaReply = {
  reply: string;
  mode: 'model' | 'offline';
  tool_calls: { name: string; result: { error?: string; local_time?: string; timezone?: string } }[];
};

export async function askNova(message: string, history: NovaMessage[], signal: AbortSignal): Promise<NovaReply> {
  const response = await apiClient.post<NovaReply>('/nova/chat', { message, history }, {
    timeout: 95000, signal,
  });
  return response.data;
}
