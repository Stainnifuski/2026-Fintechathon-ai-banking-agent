import Modal from '../Modal';
import { useState, useEffect, useCallback, type CSSProperties, type ChangeEvent } from 'react';
import type { ChatMessage, MfaData } from '@/types';

interface MFAVerifyProps {
  message: ChatMessage;
  onSubmit: (code: string) => void;
  onCancel: () => void;
}

/** MFA 验证组件（红色级别） */
export function MFAVerify({ message, onSubmit, onCancel }: MFAVerifyProps) {
  const [code, setCode] = useState('');
  const [countdown, setCountdown] = useState(0);

  const mfaData = message.actionRequired?.data as MfaData | undefined;
  const phone = mfaData?.phone ?? '***';
  const method = mfaData?.method ?? 'sms';

  /** 获取验证码方法的显示文本 */
  const getMethodLabel = (): string => {
    switch (method) {
      case 'sms':
        return '短信验证码';
      case 'face':
        return '人脸识别';
      case 'ukey':
        return 'U盾验证';
      default:
        return '验证码';
    }
  };

  /** 发送验证码 */
  const handleSendCode = useCallback(() => {
    if (countdown > 0) return;
    setCountdown(60);
  }, [countdown]);

  /** 倒计时 */
  useEffect(() => {
    if (countdown <= 0) return;
    const timer = setInterval(() => {
      setCountdown((prev) => (prev <= 1 ? 0 : prev - 1));
    }, 1000);
    return () => clearInterval(timer);
  }, [countdown]);

  /** 验证码输入 */
  const handleCodeChange = (e: ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value.replace(/\D/g, '').slice(0, 6);
    setCode(value);
  };

  /** 提交验证码 */
  const handleSubmit = () => {
    if (code.length === 6) {
      onSubmit(code);
    }
  };


  const dialogStyle: CSSProperties = {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: '28px 24px',
    width: 400,
    maxWidth: '90vw',
    boxShadow: '0 8px 30px rgba(0, 0, 0, 0.15)',
  };

  const titleStyle: CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontSize: 18,
    fontWeight: 600,
    color: '#ff4d4f',
    marginBottom: 8,
  };

  const subtitleStyle: CSSProperties = {
    fontSize: 13,
    color: '#999',
    marginBottom: 24,
  };

  const phoneBoxStyle: CSSProperties = {
    backgroundColor: '#fff1f0',
    border: '1px solid #ffccc7',
    borderRadius: 8,
    padding: '12px 16px',
    textAlign: 'center' as const,
    marginBottom: 20,
  };

  const phoneTextStyle: CSSProperties = {
    fontSize: 16,
    fontWeight: 600,
    color: '#333',
    letterSpacing: 2,
  };

  const inputRowStyle: CSSProperties = {
    display: 'flex',
    gap: 10,
    marginBottom: 24,
  };

  const inputStyle: CSSProperties = {
    flex: 1,
    padding: '10px 14px',
    borderRadius: 8,
    border: '1px solid #d9d9d9',
    fontSize: 16,
    letterSpacing: 4,
    textAlign: 'center' as const,
    outline: 'none',
  };

  const sendBtnStyle: CSSProperties = {
    padding: '10px 16px',
    borderRadius: 8,
    border: '1px solid #ff4d4f',
    backgroundColor: countdown > 0 ? '#f5f5f5' : '#fff',
    color: countdown > 0 ? '#999' : '#ff4d4f',
    fontSize: 13,
    cursor: countdown > 0 ? 'not-allowed' : 'pointer',
    whiteSpace: 'nowrap' as const,
    fontWeight: 500,
  };

  const buttonRowStyle: CSSProperties = {
    display: 'flex',
    gap: 12,
  };

  const cancelBtnStyle: CSSProperties = {
    flex: 1,
    padding: '10px 0',
    borderRadius: 8,
    border: '1px solid #d9d9d9',
    backgroundColor: '#fff',
    color: '#666',
    fontSize: 14,
    cursor: 'pointer',
    fontWeight: 500,
  };

  const submitBtnStyle: CSSProperties = {
    flex: 1,
    padding: '10px 0',
    borderRadius: 8,
    border: 'none',
    backgroundColor: code.length === 6 ? '#ff4d4f' : '#ffccc7',
    color: '#fff',
    fontSize: 14,
    cursor: code.length === 6 ? 'pointer' : 'not-allowed',
    fontWeight: 600,
  };

  return (
    <Modal role="dialog" aria-modal="true">
      <div style={dialogStyle}>
        <div style={titleStyle}>
          <span>🔐</span>
          <span>安全验证</span>
        </div>
        <p style={subtitleStyle}>
          此操作需要进行 {getMethodLabel()} 验证
        </p>

        {method === 'sms' && (
          <>
            <div style={phoneBoxStyle}>
              <p style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>
                验证码将发送至
              </p>
              <p style={phoneTextStyle}>{phone}</p>
            </div>

            <div style={inputRowStyle}>
              <input
                style={inputStyle}
                type="text"
                placeholder="请输入6位验证码"
                value={code}
                onChange={handleCodeChange}
                maxLength={6}
              />
              <button style={sendBtnStyle} onClick={handleSendCode} disabled={countdown > 0}>
                {countdown > 0 ? `${countdown}s` : '获取验证码'}
              </button>
            </div>
          </>
        )}

        <div style={buttonRowStyle}>
          <button style={cancelBtnStyle} onClick={onCancel}>
            取消
          </button>
          <button
            style={submitBtnStyle}
            onClick={handleSubmit}
            disabled={code.length !== 6}
          >
            提交验证
          </button>
        </div>
      </div>
    </Modal>
  );
}
