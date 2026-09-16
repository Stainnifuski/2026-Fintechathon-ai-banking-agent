import type { CSSProperties } from 'react';
import type { ChatMessage } from '@/types';
import dayjs from 'dayjs';

interface MessageBubbleProps {
  message: ChatMessage;
}

/** 消息气泡组件 */
export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.isUser;
  const time = dayjs(message.timestamp).format('HH:mm');

  const getTypeIndicator = (): string | null => {
    switch (message.type) {
      case 'confirmation':
        return '⚠️ 待确认';
      case 'mfa_required':
        return '🔐 需要验证';
      case 'error':
        return '❌ 错误';
      case 'chart':
        return '📊 图表';
      default:
        return null;
    }
  };

  const typeIndicator = getTypeIndicator();

  const containerStyle: CSSProperties = {
    display: 'flex',
    flexDirection: 'column',
    alignItems: isUser ? 'flex-end' : 'flex-start',
    marginBottom: 16,
    padding: '0 16px',
  };

  const bubbleStyle: CSSProperties = {
    maxWidth: '70%',
    padding: '12px 16px',
    borderRadius: isUser ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
    backgroundColor: isUser
      ? '#1677ff'
      : message.type === 'error'
        ? '#fff2f0'
        : '#f5f5f5',
    color: isUser
      ? '#ffffff'
      : message.type === 'error'
        ? '#ff4d4f'
        : '#333333',
    fontSize: 14,
    lineHeight: '1.6',
    wordBreak: 'break-word' as const,
    boxShadow: '0 1px 2px rgba(0, 0, 0, 0.06)',
    border: message.type === 'error' ? '1px solid #ffccc7' : 'none',
  };

  const timeStyle: CSSProperties = {
    fontSize: 11,
    color: '#999',
    marginTop: 4,
    padding: '0 4px',
  };

  const indicatorStyle: CSSProperties = {
    fontSize: 12,
    color: message.type === 'error' ? '#ff4d4f' : '#fa8c16',
    marginBottom: 4,
    fontWeight: 500,
  };

  return (
    <div style={containerStyle}>
      {typeIndicator && !isUser && (
        <span style={indicatorStyle}>{typeIndicator}</span>
      )}
      <div style={bubbleStyle}>
        {message.content}
      </div>
      <span style={timeStyle}>{time}</span>
    </div>
  );
}
