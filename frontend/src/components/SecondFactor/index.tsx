import { useEffect, useState } from 'react';
import * as bank from '../../services/bank';

interface SecondFactorProps {
  purpose: bank.SmsPurpose;
  referenceId: string;
  factor: bank.SecondFactor | null;
  onChange: (factor: bank.SecondFactor | null) => void;
  disabled?: boolean;
}

const errorMessage = (error: unknown) =>
  (error as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message || '短信发送失败，请稍后重试';

/** 红色强验证方式选择：验证器 App 或短信验证码（短信码绑定到当前这一笔操作）。 */
export function SecondFactor({ purpose, referenceId, factor, onChange, disabled }: SecondFactorProps) {
  const [method, setMethod] = useState<'totp' | 'sms'>(factor?.method ?? 'totp');
  const [countdown, setCountdown] = useState(0);
  const [hint, setHint] = useState('');
  const [inbox, setInbox] = useState<bank.SmsInboxMessage[] | null>(null);

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = setInterval(() => setCountdown(prev => (prev <= 1 ? 0 : prev - 1)), 1000);
    return () => clearInterval(timer);
  }, [countdown]);

  const choose = (next: 'totp' | 'sms') => {
    setMethod(next); setHint(''); setInbox(null);
    onChange(next === 'totp' ? { method: 'totp' } : null);
  };

  const send = async () => {
    setHint('');
    try {
      const challenge = await bank.sendSms(purpose, referenceId);
      onChange({ method: 'sms', challengeId: challenge.challenge_id });
      setCountdown(challenge.resend_after);
      setHint(`验证码已发送至 ${challenge.phone_masked}，${Math.round(challenge.expires_in / 60)} 分钟内有效`);
    } catch (error) { setHint(errorMessage(error)); }
  };

  const showInbox = async () => {
    try { setInbox(await bank.smsDevInbox()); } catch { setInbox([]); }
  };

  return <div className="second-factor">
    <div className="confirm">
      <button type="button" className={method === 'totp' ? '' : 'secondary'} disabled={disabled} onClick={() => choose('totp')}>验证器</button>
      <button type="button" className={method === 'sms' ? '' : 'secondary'} disabled={disabled} onClick={() => choose('sms')}>短信验证码</button>
    </div>
    {method === 'totp' && <p className="hint">请打开 Google Authenticator（或其他验证器 App），输入当前显示的 6 位验证码。</p>}
    {method === 'sms' && <>
      <div className="confirm">
        <button type="button" className="secondary" disabled={disabled || countdown > 0} onClick={send}>{countdown > 0 ? `${countdown}s 后可重发` : factor ? '重新获取' : '获取短信验证码'}</button>
        {factor && <button type="button" className="secondary" disabled={disabled} onClick={showInbox}>查看模拟短信</button>}
      </div>
      {hint && <p className="hint">{hint}</p>}
      {inbox && <p className="hint">{inbox.length ? inbox[0].content : '模拟短信收件箱未启用或暂无短信'}</p>}
    </>}
  </div>;
}
