import { useSyncExternalStore } from 'react';

export type SecurityLock = {
  scope: 'trade' | 'login' | 'agent';
  reason_code: string;
  locked_until: string;
  retry_after: number;
  generation: number;
};
export type SecurityStatus = { locks: SecurityLock[]; trade_locked: boolean; message: string };
export const SECURITY_CHANGED_EVENT = 'bank-security-changed';
let current: SecurityStatus = { locks: [], trade_locked: false, message: '' };
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export function setSecurityStatus(value: SecurityStatus) {
  current = value;
  listeners.forEach(listener => listener());
}
export function useSecurityStatus() { return useSyncExternalStore(subscribe, () => current); }

export function publishSecurityDenial(error: unknown) {
  const detail = (error as { response?: { data?: { detail?: { code?: string } } } })?.response?.data?.detail;
  if (detail && ['SECURITY_LOCKED', 'LOGIN_LOCKED', 'AGENT_SESSION_LOCKED', 'SECURITY_RECONFIRM_REQUIRED'].includes(detail.code ?? '')) {
    window.dispatchEvent(new CustomEvent(SECURITY_CHANGED_EVENT));
  }
}
