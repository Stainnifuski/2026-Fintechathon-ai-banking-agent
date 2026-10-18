import type { CandidateGroup, CandidateShare, ProductCandidate } from '../../services/wealth';

export interface SelectedProduct { candidate: ProductCandidate; share: CandidateShare }
export interface ProductSelectionState {
  scopeKey: string;
  items: SelectedProduct[];
  preferredShares: Record<string, string>;
}
export const emptyProductSelection = (scopeKey: string): ProductSelectionState => ({ scopeKey, items: [], preferredShares: {} });
export const validShares = (candidate: ProductCandidate) => candidate.share_classes.filter(share => share.tradable && ['on_sale','purchase_eligible'].includes(share.status) && share.product_risk_level != null);
export function displayedShare(candidate: ProductCandidate, state: ProductSelectionState): CandidateShare | undefined {
  const valid = validShares(candidate);
  return valid.find(share => share.product_id === state.preferredShares[candidate.family_id])
    ?? valid.find(share => share.product_id === (candidate.evidence?.default_share as {product_id?:string} | undefined)?.product_id)
    ?? [...valid].sort((a,b) => a.product_id.localeCompare(b.product_id))[0];
}
export function toggleProduct(state: ProductSelectionState, candidate: ProductCandidate): ProductSelectionState {
  const existing = state.items.find(item => item.candidate.family_id === candidate.family_id);
  if (existing) return { ...state, items: state.items.filter(item => item.candidate.family_id !== candidate.family_id) };
  const share = displayedShare(candidate, state);
  if (!share) return state;
  return { ...state, items: [...state.items, { candidate, share }] };
}
export function changeShare(state: ProductSelectionState, candidate: ProductCandidate, productId: string): ProductSelectionState {
  const share = validShares(candidate).find(item => item.product_id === productId);
  if (!share) return state;
  return { ...state, preferredShares: { ...state.preferredShares, [candidate.family_id]: productId },
    items: state.items.map(item => item.candidate.family_id === candidate.family_id ? { candidate, share } : item) };
}
export const selectionForScope = (state: ProductSelectionState, scopeKey: string) => state.scopeKey === scopeKey ? state : emptyProductSelection(scopeKey);
export function selectedReferences(state: ProductSelectionState) {
  return state.items.map(({ candidate, share }) => ({ family_id: candidate.family_id, product_id: share.product_id,
    product_version: share.product_version, disclosure_version: share.disclosure_version, share_class: share.share_class }));
}
export function groupMessage(group: CandidateGroup): string | null {
  switch (group.status) {
    case 'ok': return null;
    case 'risk_mismatch': return group.risk_explanation
      ? `当前评估 R${group.risk_explanation.user_level}，该组候选风险 ${group.risk_explanation.product_levels.map(level => `R${level}`).join('／')}，均超过评估能力。`
      : '风险适配说明缺失，请重新查询，不能据此选择产品。';
    case 'no_theme_match': return '未找到符合该主题的产品。';
    case 'no_category_match': return group.filter.mode==='name'?'未找到匹配该名称或代码的产品。':'该类别暂无匹配产品。';
    case 'no_eligible_products': return '暂无符合条件且可申购的产品。';
    default: return '资料暂不可用，请稍后重新查询。';
  }
}
export function performanceText(performance: CandidateShare['performance']['one_year']) {
  const labels: Record<string, string> = { total_return: '近一年总回报', nav_change: '近一年净值变化', seven_day_annualized: '七日年化', benchmark: '业绩比较基准' };
  const label = labels[performance.metric] ?? '历史指标（口径待核对）';
  const unknownMetric = !(performance.metric in labels);
  return { label, value: unknownMetric || performance.status !== 'available' || performance.value_percent == null ? performance.reason || '数据缺失' : `${performance.value_percent}%`,
    date: `${performance.start_date || '起点未知'}—${performance.end_date || '终点未知'}` };
}
