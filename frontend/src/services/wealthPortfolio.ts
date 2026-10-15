import type { CandidateActionResult, CandidateReference, PortfolioCandidateGroup, PortfolioCreate, PortfolioPlan, PortfolioRecommendation, PortfolioSelection, PortfolioSnapshot } from '../types/wealthPortfolio';
export {wealthFeedback as portfolioExplanation} from '../utils/wealthFeedback';
function reasonText(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(v => v && typeof v === 'object' && 'msg' in v ? `${reasonText(v.loc)}: ${reasonText(v.msg)}` : reasonText(v)).filter(Boolean).join('；');
  if (typeof value === 'object') return Object.entries(value).map(([k, v]) => `${k}: ${reasonText(v)}`).join('；');
  return String(value);
}
export class PortfolioHttpError extends Error {
  constructor(public status: number, public detail: unknown) { super(`${status} ${reasonText(detail) || 'REQUEST_FAILED'}`); }
}
export interface PortfolioAdapter {
  create: (contextId: string, p: string | null, options?: Pick<PortfolioCreate, 'selected' | 'candidate_binding'>) => Promise<PortfolioPlan>;
  read: (id: string, contextId: string) => Promise<PortfolioPlan>;
  revise: (selection: PortfolioSelection) => Promise<PortfolioPlan>;
  snapshot: () => Promise<PortfolioSnapshot>;
  recommend: (request: PortfolioRecommendation) => Promise<PortfolioCandidateGroup>;
  select: (context: string, session: string, version: number, items: CandidateReference[]) => Promise<CandidateActionResult>;
  switchShare: (context: string, session: string, version: number, family: string, target: CandidateReference) => Promise<CandidateActionResult>;
  controls: () => Promise<{ context_id: string; snapshot_id: string; can_read: boolean; can_edit: boolean; write_reason: string | null; asset_totals: { available_cash_fen: number | null; held_fen: number | null; redemption_receivable_fen: number | null } }>;
}
export function portfolioAdapter(token: string, scenario?: string): PortfolioAdapter {
  const call = async <T,>(path: string, method = 'GET', body?: unknown): Promise<T> => {
    const result = await fetch(path, { method, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, ...(scenario ? { 'X-Portfolio-Scenario': scenario } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
    const value = await result.json(); if (!result.ok) throw new PortfolioHttpError(result.status, value.detail); return value as T;
  };
  const base = '/api/v1/wealth/portfolio';
  return {
    create: (contextId, p, options = {}) => call(base + '/plans', 'POST', { context_id: contextId, ...(p === null ? {} : { growth_ratio_ex_cash: p }), ...options }),
    read: (id, contextId) => call(`${base}/plans/${encodeURIComponent(id)}?context_id=${encodeURIComponent(contextId)}`),
    revise: selection => call(`${base}/plans/${encodeURIComponent(selection.portfolio_id)}`, 'PUT', selection),
    snapshot: () => call(base + '/snapshot'), recommend: request => call(base + '/recommendations', 'POST', request),
    select: (context, session, version, items) => call('/dev/candidates/select', 'POST', { context_id: context, action: { session_id: session, expected_version: version, items } }),
    switchShare: (context, session, version, family, target) => call('/dev/candidates/switch', 'POST', { context_id: context, action: { session_id: session, expected_version: version, family_id: family, target } }),
    controls: () => call('/dev/context'),
  };
}
export function percentToRatio(text: string): string {
  if (!/^\d+(\.\d+)?$/.test(text)) throw new Error('百分比应为非负十进制数');
  const [whole, fraction = ''] = text.split('.'); const digits = (whole + fraction).replace(/^0+/, '') || '0';
  const scale = fraction.length + 2; const padded = digits.padStart(scale + 1, '0');
  if (BigInt(digits) > 100n * 10n ** BigInt(fraction.length)) throw new Error('单项百分比不能超过100%');
  return padded.slice(0, -scale) + '.' + padded.slice(-scale);
}
export function ratioToPercent(text: string): string {
  const [a, b = ''] = text.split('.'); const digits = a + b.padEnd(2, '0'); const split = a.length + 2;
  const tail = digits.slice(split).replace(/0+$/, '');
  return (digits.slice(0, split).replace(/^0+(?=\d)/, '') || '0') + (tail ? '.' + tail : '');
}
export {isIntegerPercentageRatio} from '../utils/wealthPercent';
export function fenRatio(fen: number, total: number): string {
  if (!Number.isSafeInteger(fen) || !Number.isSafeInteger(total) || total <= 0) throw new Error('金额不是有效整数分');
  if (!fen) return '0'; if (fen === total) return '1';
  let scale = 28; while (BigInt(fen) * 10n ** BigInt(scale) / BigInt(total) < 10n ** 27n) scale++;
  const n = BigInt(fen) * 10n ** BigInt(scale), d = BigInt(total); let q = n / d;
  if (n % d * 2n > d || n % d * 2n === d && q % 2n === 1n) q++;
  const digits = q.toString().padStart(scale + 1, '0'); return digits.slice(0, -scale) + '.' + digits.slice(-scale);
}
export function ratioFen(text: string, total: number): bigint | null {
  const [a, b = ''] = text.split('.'); const n = BigInt(a + b) * BigInt(total), d = 10n ** BigInt(b.length);
  return n % d === 0n ? n / d : null;
}
