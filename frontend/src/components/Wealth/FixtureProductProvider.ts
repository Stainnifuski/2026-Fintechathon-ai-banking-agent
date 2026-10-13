// Development-only recorded provider. This module is reachable only through the
// DEV preview; no synthetic IDs or candidates can reach a bank trading endpoint.
import recording from '../../../../tests/fixtures/wealth_portfolio_contract_v1/f09.json' with { type: 'json' };
import type { CandidateGroup, ProductCandidate } from '../../services/wealth';
import type { ProductDetailLoader } from './ProductDetail';
import type { ComparisonLoader } from './ProductComparison';
import { performanceText } from './productSelection.ts';
import f11 from '../../../../tests/fixtures/wealth_portfolio_contract_v1/f11.json' with { type: 'json' };
import type { PortfolioPlan, PortfolioSelection, PreparedPortfolio } from '../../services/wealth';
import { selectionKey } from './allocationSelection.ts';
import { readCandidateGroup } from '../../services/wealthCandidates.ts';
import { readPortfolioPlan, readReviewSnapshot } from '../../services/wealthPortfolioReview.ts';
import { emptyProductSelection } from './productSelection.ts';

export const recordedGroups = (): CandidateGroup[] => recording.groups.map(readCandidateGroup);
export const recordedF11Plan = (): PortfolioPlan => readPortfolioPlan(structuredClone(f11.plan),f11.plan.context_id);
export const recordedF11Snapshot = () => readReviewSnapshot(structuredClone(f11.snapshot),recordedF11Plan());
export function recordedF11Selection() {
  const state = emptyProductSelection(f11.snapshot.asset_scope_id);
  for (const item of f11.plan.selected_positions) {
    const candidate = recordedGroups().flatMap<ProductCandidate>(group => group.items).find(c => c.family_id === item.family_id)!;
    const share = candidate.share_classes.find(s => s.product_id === item.product_id)!;
    state.items.push({candidate:structuredClone(candidate),share:structuredClone(share)});
  }
  return state;
}
export async function recordedRevision(selection: PortfolioSelection): Promise<PortfolioPlan> {
  if (selection.portfolio_id !== f11.plan.portfolio_id || selection.snapshot_id !== f11.plan.snapshot_id || selection.context_id !== f11.plan.context_id) throw new Error('此选择尚无比例修订录制。可加载 F11 七产品样例继续检查。');
  const requested = selectionKey(selection);
  const output = requested === selectionKey({items:readPortfolioPlan(f11.revision,f11.revision.context_id).selected_positions}) ? f11.revision : requested === selectionKey({items:recordedF11Plan().selected_positions}) ? f11.plan : null;
  if (!output || selection.expected_version > output.version || selection.expected_version < 2) throw new Error('此比例尚未录制，不能冒充配置服务校验成功。可重新加载样例，或将 S1 调为15%、S2调为5%，其余保持不变。');
  return readPortfolioPlan(structuredClone(output),output.context_id);
}
export async function recordedPrepare(plan:PortfolioPlan):Promise<PreparedPortfolio> {
  if (![f11.plan,f11.revision].some(p=>JSON.stringify(p)===JSON.stringify(plan)))throw new Error('未登记的整体版本，不能准备样例差额');
  return {portfolio_id:plan.portfolio_id,portfolio_version:plan.version,execution_plan_id:null,execution_plan_version:null,stage:'BUY',
    actions:plan.selected_positions.map(p=>{if(p.current_fen==null)throw new Error('Recorded valuation missing');return {...p,current_fen:p.current_fen,difference_fen:p.target_fen-p.current_fen,direction:'purchase',amount_fen:p.target_fen-p.current_fen,units:'0.0000',status:'READY'};})};
}
const allGroups: CandidateGroup[] = [...recording.groups, ...recording.next_groups].map(readCandidateGroup);
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
  return readCandidateGroup(structuredClone(result));
}
export const recordedDetail: ProductDetailLoader = async (id, period) => {
  const { candidate, share } = findProduct(id);
  if (!['1m', '3m', '1y'].includes(period)) throw new Error('No recorded period');
  const firstDate = share.performance.one_year.status === 'insufficient_history' ? '2026-08-01' : '2025-09-30';
  const cutoff = period === '1m' ? '2026-08-30' : period === '3m' ? '2026-06-30' : '2025-09-30';
  const points = [{date: firstDate, nav:'1.0000'}, {date:'2026-09-30', nav:'1.0420'}].filter(point => point.date >= cutoff);
  const metric = { value_percent: points.length === 2 ? '4.20' : null, reason: points.length === 2 ? null : 'AT_LEAST_TWO_OBSERVATIONS' };
  return { name: candidate.name + (share.share_class ? ` · ${share.share_class}类` : ''), product_id: id,
    identity: 'F09契约样例 · 虚构数据', category: candidate.bucket, risk_level: share.product_risk_level??undefined,
    as_of: share.data_as_of??'来源日期未知', latest_nav: candidate.bucket === 'cash_management' ? undefined : { nav: '1.0420', date:'2026-09-30' },
    rules: { min_amount_fen: share.min_amount_fen??undefined, purchase_fee_rate: share.fees.purchase_fee_rate, sales_service_fee_rate: share.fees.sales_service_fee_rate },
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
      redemption_fee_rules:share.fees.redemption_fee_rules==null?null:JSON.stringify(share.fees.redemption_fee_rules), performance_label:performance.label, performance_value:performance.value, performance_dates:performance.date };
  });
};
