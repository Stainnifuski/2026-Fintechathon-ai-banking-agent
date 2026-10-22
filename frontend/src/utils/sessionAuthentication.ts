export const AUTHENTICATION_EXPIRED_EVENT='bank-authentication-expired';
export const authenticationExpiredMessage='登录已过期，请重新登录后继续操作；已受理交易不受影响。';

/** Only login-session failures; an invalid MFA code must not log the user out. */
export function isSessionAuthenticationFailure(error:unknown):boolean {
  const response=(error as {response?:{status?:number;data?:{detail?:unknown}}})?.response;
  if (response?.status !== 401) return false;
  const detail = response.data?.detail;
  if (typeof detail === 'string') return ['请先登录','无效的认证令牌','令牌缺少用户信息','用户不存在'].includes(detail);
  return !!detail && typeof detail === 'object' && 'code' in detail &&
    ['AUTH_REQUIRED','TOKEN_EXPIRED','INVALID_TOKEN'].includes(String(detail.code));
}
