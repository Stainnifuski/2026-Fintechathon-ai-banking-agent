// Development-only recorded provider. This module is reachable only through the
// DEV preview; no synthetic IDs or candidates can reach a bank trading endpoint.
import recording from '../../../../tests/fixtures/wealth_portfolio_contract_v1/f09.json' with { type: 'json' };
import type { CandidateGroup, ProductCandidate } from '../../services/wealth';
import type { ProductDetailLoader } from './ProductDetail';
import type { ComparisonLoader } from './ProductComparison';
import { performanceText } from './productSelection.ts';

export const recordedGroups = (): CandidateGroup[] => structuredClone(recording.groups);
const allGroups: CandidateGroup[] = [...recording.groups, ...recording.next_groups];
const allCandidates = allGroups.flatMap<ProductCandidate>(group => group.items);
function findProduct(id: string) {
  for (const candidate of allCandidates) {
    const share = candidate.share_classes.find(item => item.product_id === id);
    if (share) return { candidate, share };
  }
  throw new Error('No recorded product detail for this ID');
}
export function recordedNextGroup(group: CandidateGroup, selectedFamilyIds: string[]): CandidateGroup {
  // Preserve the actual callback inputs for provider substitution; this recorded
  // provider does not calculate suitability, rank products, or remove selections.
  if (new Set(selectedFamilyIds).size !== selectedFamilyIds.length) throw new Error('Duplicate family selection');
  const source = group.next_cursor === `${group.bucket}-2` ? recording.next_groups : recording.groups;
  const result = source.find(item => item.bucket === group.bucket);
  if (!result || result.filter.mode !== group.filter.mode || result.filter.bucket !== group.filter.bucket) throw new Error('No recorded batch for this filter');
  return structuredClone(result);
}
export const recordedDetail: ProductDetailLoader = async (id, period) => {
  const { candidate, share } = findProduct(id);
  if (!['1m', '3m', '1y'].includes(period)) throw new Error('No recorded period');
  const firstDate = share.performance.one_year.status === 'insufficient_history' ? '2026-08-01' : '2025-09-30';
  const cutoff = period === '1m' ? '2026-08-30' : period === '3m' ? '2026-06-30' : '2025-09-30';
  const points = [{date: firstDate, nav:'1.0000'}, {date:'2026-09-30', nav:'1.0420'}].filter(point => point.date >= cutoff);
  const metric = { value_percent: points.length === 2 ? '4.20' : null, reason: points.length === 2 ? null : 'AT_LEAST_TWO_OBSERVATIONS' };
  return { name: candidate.name + (share.share_class ? ` · ${share.share_class}类` : ''), product_id: id,
    identity: 'F09契约样例 · 虚构数据', category: candidate.bucket, risk_level: share.product_risk_level,
    as_of: share.data_as_of, latest_nav: candidate.bucket === 'cash_management' ? undefined : { nav: '1.0420', date:'2026-09-30' },
    rules: { min_amount_fen: share.min_amount_fen, purchase_fee_rate: share.fees.purchase_fee_rate, sales_service_fee_rate: share.fees.sales_service_fee_rate },
    chart: candidate.bucket === 'cash_management' ? null : { points, window_start:points[0]?.date ?? null, window_end:points[points.length - 1]?.date ?? null,
      notice: 'F09虚构观察点，仅用于详情交互检查；不是实际基金行情或本人收益。', quality_flags:[],
      metrics: {nav_change:metric,max_drawdown:{value_percent:null,reason:'样例未提供'},volatility:{value_percent:null,reason:'样例未提供'}} },
    money_yield: candidate.bucket === 'cash_management' ? [{ date:'2026-09-30', income_per_10000:null, seven_day_annualized_percent:'4.20' }] : [],
  };
};
export const recordedComparison: ComparisonLoader = async ids => {
  if (ids.length < 2 || ids.length > 4 || new Set(ids).size !== ids.length) throw new Error('Choose two to four unique products');
  return ids.map(id => {
    const { candidate, share } = findProduct(id);
    const performance = performanceText(share.performance.one_year);
    return { product_id:id, name:candidate.name, share_class:share.share_class, product_version:share.product_version,
      product_risk_level:share.product_risk_level, min_amount_fen:share.min_amount_fen,
      purchase_fee_rate:share.fees.purchase_fee_rate, sales_service_fee_rate:share.fees.sales_service_fee_rate,
      redemption_fee_rules:share.fees.redemption_fee_rules, performance_label:performance.label, performance_value:performance.value, performance_dates:performance.date };
  });
};
