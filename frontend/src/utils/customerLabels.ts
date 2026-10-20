import type { Account } from '../services/bank';

export function accountLabel(accountId: string | null | undefined, accounts: Account[]): string {
  const account = accounts.find(item => item.account_id === accountId);
  return account ? `账户尾号 ${account.card_number.slice(-4)}` : '账户';
}

export function counterpartyLabel(value: string | null | undefined): string {
  if (!value) return '对方账户';
  return /^A(?:\d+|[0-9A-F]{10,})$/i.test(value) ? '对方账户信息不可用' : value;
}
