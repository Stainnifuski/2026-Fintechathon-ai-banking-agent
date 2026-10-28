/** 金额先换成整数“分”再格式化，显示为千分位、两位小数；未知值显示“—”，不按 0 处理。 */
const group = (digits: string) => digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');

export function formatFen(fen: number | null | undefined): string {
  if (fen === null || fen === undefined || !Number.isFinite(fen)) return '—';
  const value = Math.round(fen);
  const abs = Math.abs(value);
  const body = `¥${group(Math.floor(abs / 100).toString())}.${(abs % 100).toString().padStart(2, '0')}`;
  return value < 0 ? `−${body}` : body;
}

export function formatYuan(yuan: number | null | undefined): string {
  if (yuan === null || yuan === undefined || !Number.isFinite(yuan)) return '—';
  return formatFen(Math.round(yuan * 100));
}

/** 卡号统一显示为“6222 **** 8842”。 */
export function maskCard(cardNumber: string | null | undefined): string {
  if (!cardNumber) return '—';
  const compact = cardNumber.replace(/\s+/g, '');
  const masked = compact.match(/^(\d{4})\*+(\d{4})$/);
  if (masked) return `${masked[1]} **** ${masked[2]}`;
  return /^\d{8,}$/.test(compact) ? `${compact.slice(0, 4)} **** ${compact.slice(-4)}` : compact;
}

/** 用户填写的金额字符串（如任务卡草稿里的 "300" / "1200.5"）；无法解析时原样显示。 */
export function formatYuanText(value: string): string {
  const yuan = Number(value);
  return value.trim() !== '' && Number.isFinite(yuan) ? formatYuan(yuan) : `¥${value}`;
}
