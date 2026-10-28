/** Card type shown beside an account in payment and collection forms. */
export function cardTypeLabel(accountType: string): string {
  if (accountType === 'savings' || accountType === 'checking') return 'debit';
  if (accountType === 'credit') return 'credit';
  return accountType;
}
