import { apiClient } from './api';
import type {
  ChatMessage,
  SendMessageRequest,
  ConfirmActionRequest,
} from '@/types';

/**
 * 聊天相关 API 封装
 */

/** 发送用户消息 */
export async function sendMessage(request: SendMessageRequest): Promise<ChatMessage> {
  const response = await apiClient.post<ChatMessage>('/chat/message', request);
  return response.data;
}

/** 确认或取消操作（黄色/红色级别） */
export async function confirmAction(request: ConfirmActionRequest): Promise<ChatMessage> {
  const response = await apiClient.post<ChatMessage>('/chat/confirm', request);
  return response.data;
}

/** 获取历史消息 */
export async function getChatHistory(
  sessionId: string,
  limit: number = 50
): Promise<ChatMessage[]> {
  const response = await apiClient.get<ChatMessage[]>('/chat/history', {
    params: { session_id: sessionId, limit },
  });
  return response.data;
}
