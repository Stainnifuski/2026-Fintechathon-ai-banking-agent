import { useEffect } from 'react';
import { apiClient } from '../../services/api';
import { SECURITY_CHANGED_EVENT, setSecurityStatus, useSecurityStatus, type SecurityStatus } from '../../services/securityState';
import { beijingTime } from '../../utils/time';

export default function SecurityNotice({ userId }: { userId: string }) {
  const status = useSecurityStatus();
  useEffect(() => {
    let active = true;
    setSecurityStatus({ locks: [], trade_locked: false, message: '' });
    const refresh = () => {
      void apiClient.get<SecurityStatus>('/auth/security/status').then(response => {
        if (active) setSecurityStatus(response.data);
      }).catch(() => {});
    };
    refresh();
    const timer = window.setInterval(refresh, 30_000);
    window.addEventListener(SECURITY_CHANGED_EVENT, refresh);
    return () => {
      active = false;
      clearInterval(timer);
      window.removeEventListener(SECURITY_CHANGED_EVENT, refresh);
      setSecurityStatus({ locks: [], trade_locked: false, message: '' });
    };
  }, [userId]);
  if (!status.locks.length) return null;
  return <div role="status" className="notice global-notice security-notice">
    {status.locks.map((lock, index) => <p key={`${lock.scope}-${index}`}>
      <strong>{lock.scope === 'trade' ? '交易安全锁定' : lock.scope === 'agent' ? '部分智能会话已暂停' : '登录暂时受限'}</strong>
      {' · '}{({ TOTP_INVALID: '验证码多次错误', SMS_INVALID: '验证码多次错误', TOTP_REPLAY: '重复使用验证码',
        PROMPT_INJECTION_BLOCKED: '多次请求绕过安全规则', INVALID_CREDENTIALS: '登录多次失败',
        DIGEST_MISMATCH: '多次修改授权摘要', PARAM_DIGEST_MISMATCH: '多次修改授权参数',
        PAYER_FORBIDDEN: '多次尝试使用无权操作的账户' } as Record<string, string>)[lock.reason_code] ?? '检测到异常操作'}
      {'，预计 '}{beijingTime(lock.locked_until)}{' 后可重新操作。'}
    </p>)}
    <p>{status.trade_locked
      ? '仍可查询账户及交易结果、取消待执行任务或主动锁卡。解锁后请重新生成交易并本人确认、验证。'
      : '账户查询及其他未受限业务仍可使用；会话暂停仅影响对应智能会话。'}</p>
  </div>;
}
