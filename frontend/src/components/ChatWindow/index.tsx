import { useState, useRef, useEffect, type CSSProperties, type KeyboardEvent, type ChangeEvent } from 'react';
import { useChatStore } from '@/store/chatStore';
import { MessageBubble } from '@/components/MessageBubble';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { MFAVerify } from '@/components/MFAVerify';

/** 聊天窗口主组件 */
export function ChatWindow() {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const {
    messages,
    isLoading,
    error,
    pendingConfirmation,
    pendingMfa,
    sendMessage,
    confirmAction,
    clearError,
  } = useChatStore();

  /** 自动滚动到底部 */
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  /** 发送消息 */
  const handleSend = async () => {
    const trimmed = input.trim();
    if (!trimmed || isLoading) return;
    setInput('');
    await sendMessage(trimmed);
  };

  /** 键盘事件 */
  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  /** 确认转账 */
  const handleConfirm = () => {
    if (pendingConfirmation) {
      confirmAction(pendingConfirmation.messageId, 'confirm');
    }
  };

  /** 取消转账 */
  const handleCancelConfirm = () => {
    if (pendingConfirmation) {
      confirmAction(pendingConfirmation.messageId, 'cancel');
    }
  };

  /** 提交 MFA 验证码 */
  const handleMfaSubmit = (code: string) => {
    if (pendingMfa) {
      confirmAction(pendingMfa.messageId, 'confirm', code);
    }
  };

  /** 取消 MFA */
  const handleMfaCancel = () => {
    if (pendingMfa) {
      confirmAction(pendingMfa.messageId, 'cancel');
    }
  };

  /** 输入框变化 */
  const handleInputChange = (e: ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
  };

  const containerStyle: CSSProperties = {
    display: 'flex',
    flexDirection: 'column',
    height: '100vh',
    maxWidth: 480,
    margin: '0 auto',
    backgroundColor: '#fff',
    boxShadow: '0 0 20px rgba(0, 0, 0, 0.08)',
  };

  const headerStyle: CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '16px 20px',
    backgroundColor: '#1677ff',
    color: '#fff',
    fontSize: 17,
    fontWeight: 600,
    letterSpacing: 1,
    flexShrink: 0,
  };

  const messagesStyle: CSSProperties = {
    flex: 1,
    overflowY: 'auto',
    padding: '16px 0',
    backgroundColor: '#fafafa',
  };

  const emptyStyle: CSSProperties = {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    height: '100%',
    color: '#bbb',
    fontSize: 14,
    gap: 8,
  };

  const errorStyle: CSSProperties = {
    margin: '0 16px 8px',
    padding: '8px 12px',
    backgroundColor: '#fff2f0',
    border: '1px solid #ffccc7',
    borderRadius: 6,
    color: '#ff4d4f',
    fontSize: 13,
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  };

  const inputAreaStyle: CSSProperties = {
    display: 'flex',
    alignItems: 'flex-end',
    gap: 10,
    padding: '12px 16px',
    borderTop: '1px solid #f0f0f0',
    backgroundColor: '#fff',
    flexShrink: 0,
  };

  const textareaStyle: CSSProperties = {
    flex: 1,
    padding: '10px 14px',
    borderRadius: 20,
    border: '1px solid #e0e0e0',
    fontSize: 14,
    resize: 'none',
    outline: 'none',
    lineHeight: '1.5',
    maxHeight: 100,
    fontFamily: 'inherit',
  };

  const sendBtnStyle: CSSProperties = {
    padding: '10px 20px',
    borderRadius: 20,
    border: 'none',
    backgroundColor: isLoading || !input.trim() ? '#bfdcff' : '#1677ff',
    color: '#fff',
    fontSize: 14,
    cursor: isLoading || !input.trim() ? 'not-allowed' : 'pointer',
    fontWeight: 600,
    whiteSpace: 'nowrap',
    flexShrink: 0,
  };

  const loadingDotsStyle: CSSProperties = {
    display: 'flex',
    gap: 4,
    padding: '12px 24px',
    alignItems: 'center',
  };

  const dotStyle: CSSProperties = {
    width: 8,
    height: 8,
    borderRadius: '50%',
    backgroundColor: '#1677ff',
    opacity: 0.4,
  };

  return (
    <div style={containerStyle}>
      {/* 标题栏 */}
      <div style={headerStyle}>
        <span>🏦 AI 银行助手</span>
      </div>

      {/* 消息列表 */}
      <div style={messagesStyle}>
        {messages.length === 0 ? (
          <div style={emptyStyle}>
            <span style={{ fontSize: 36 }}>💬</span>
            <span>您好！我是 AI 银行助手</span>
            <span style={{ fontSize: 12, color: '#ccc' }}>
              您可以问我查余额、转账、理财等问题
            </span>
          </div>
        ) : (
          <>
            {messages.map((msg) => (
              <MessageBubble key={msg.messageId} message={msg} />
            ))}
            {isLoading && (
              <div style={loadingDotsStyle}>
                <div style={{ ...dotStyle, animation: 'pulse 1.4s ease-in-out infinite' }} />
                <div style={{ ...dotStyle, animation: 'pulse 1.4s ease-in-out 0.2s infinite' }} />
                <div style={{ ...dotStyle, animation: 'pulse 1.4s ease-in-out 0.4s infinite' }} />
              </div>
            )}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      {/* 错误提示 */}
      {error && (
        <div style={errorStyle}>
          <span>{error}</span>
          <button
            onClick={clearError}
            style={{
              background: 'none',
              border: 'none',
              color: '#ff4d4f',
              cursor: 'pointer',
              fontSize: 16,
              padding: '0 4px',
            }}
          >
            ✕
          </button>
        </div>
      )}

      {/* 输入区域 */}
      <div style={inputAreaStyle}>
        <textarea
          style={textareaStyle}
          value={input}
          onChange={handleInputChange}
          onKeyDown={handleKeyDown}
          placeholder="输入您的问题..."
          rows={1}
          disabled={isLoading}
        />
        <button
          style={sendBtnStyle}
          onClick={handleSend}
          disabled={isLoading || !input.trim()}
        >
          发送
        </button>
      </div>

      {/* 转账确认弹窗 */}
      {pendingConfirmation && (
        <ConfirmDialog
          message={pendingConfirmation}
          onConfirm={handleConfirm}
          onCancel={handleCancelConfirm}
        />
      )}

      {/* MFA 验证弹窗 */}
      {pendingMfa && (
        <MFAVerify
          message={pendingMfa}
          onSubmit={handleMfaSubmit}
          onCancel={handleMfaCancel}
        />
      )}
    </div>
  );
}
