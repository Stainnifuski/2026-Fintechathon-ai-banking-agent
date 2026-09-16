import { create } from 'zustand';
import type { ChatMessage } from '@/types';
import * as chatApi from '@/services/chat';

/** 默认用户 ID（演示用，实际应从认证模块获取） */
const DEFAULT_USER_ID = 'U123456';

/** 聊天状态接口 */
interface ChatState {
  sessionId: string | null;
  messages: ChatMessage[];
  isLoading: boolean;
  error: string | null;
  pendingConfirmation: ChatMessage | null;
  pendingMfa: ChatMessage | null;
  sendMessage: (content: string) => Promise<void>;
  confirmAction: (messageId: string, action: 'confirm' | 'cancel', mfaCode?: string) => Promise<void>;
  loadHistory: () => Promise<void>;
  clearError: () => void;
  reset: () => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessionId: null,
  messages: [],
  isLoading: false,
  error: null,
  pendingConfirmation: null,
  pendingMfa: null,

  /** 发送消息 */
  sendMessage: async (content: string) => {
    const { sessionId } = get();

    const userMessage: ChatMessage = {
      messageId: `local-${Date.now()}`,
      sessionId: sessionId ?? '',
      type: 'text',
      content,
      timestamp: new Date().toISOString(),
      isUser: true,
    };

    set({ isLoading: true, error: null, messages: [...get().messages, userMessage] });

    try {
      const response = await chatApi.sendMessage({
        sessionId: sessionId ?? undefined,
        userId: DEFAULT_USER_ID,
        message: content,
      });

      const newSessionId = response.sessionId || sessionId;

      let pendingConfirmation: ChatMessage | null = null;
      let pendingMfa: ChatMessage | null = null;

      if (response.type === 'confirmation') {
        pendingConfirmation = response;
      } else if (response.type === 'mfa_required') {
        pendingMfa = response;
      }

      set({
        sessionId: newSessionId,
        messages: [...get().messages, { ...response, isUser: false }],
        isLoading: false,
        pendingConfirmation,
        pendingMfa,
      });
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : '发送消息失败，请重试';
      set({ isLoading: false, error: errorMessage });
    }
  },

  /** 确认或取消操作 */
  confirmAction: async (messageId: string, action: 'confirm' | 'cancel', mfaCode?: string) => {
    const { sessionId } = get();
    if (!sessionId) return;

    set({ isLoading: true, error: null });

    try {
      const response = await chatApi.confirmAction({
        sessionId,
        messageId,
        action,
        mfaCode,
      });

      set({
        messages: [...get().messages, { ...response, isUser: false }],
        isLoading: false,
        pendingConfirmation: null,
        pendingMfa: null,
      });
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : '操作失败，请重试';
      set({ isLoading: false, error: errorMessage });
    }
  },

  /** 加载历史消息 */
  loadHistory: async () => {
    const { sessionId } = get();
    if (!sessionId) return;

    set({ isLoading: true });

    try {
      const history = await chatApi.getChatHistory(sessionId);
      set({ messages: history, isLoading: false });
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : '加载历史消息失败';
      set({ isLoading: false, error: errorMessage });
    }
  },

  /** 清除错误 */
  clearError: () => set({ error: null }),

  /** 重置会话 */
  reset: () =>
    set({
      sessionId: null,
      messages: [],
      isLoading: false,
      error: null,
      pendingConfirmation: null,
      pendingMfa: null,
    }),
}));
