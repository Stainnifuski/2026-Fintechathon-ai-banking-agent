export const AUTHENTICATION_EXPIRED_EVENT='bank-authentication-expired';
export const authenticationExpiredMessage='登录已过期，请重新登录后继续操作；已受理交易不受影响。';

/** Only login-session failures; an invalid MFA code must not log the user out. */
export function isSessionAuthenticationFailure(error:unknown):boolean {
  const response=(error as {response?:{status?:number;data?:{detail?:unknown}}})?.response;
  return response?.status===401 && typeof response.data?.detail==='string' &&
    ['请先登录','无效的认证令牌','令牌缺少用户信息','用户不存在'].includes(response.data.detail);
}
