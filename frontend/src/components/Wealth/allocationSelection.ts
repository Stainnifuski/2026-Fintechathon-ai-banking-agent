import type { PortfolioPlan, PortfolioSelection } from '../../services/wealth';
import type { ProductSelectionState } from './productSelection';

/** Parse a percentage without binary floating-point money/ratio rounding. */
export function percentageRatio(text: string): string {
  if (!/^\d{1,3}(\.\d{1,2})?$/.test(text)) throw new Error('整体占比须为0至100，最多两位小数');
  const [whole, fraction = ''] = text.split('.');
  const hundredths = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (hundredths > 10000) throw new Error('整体占比不能超过100%');
  return `${Math.floor(hundredths / 10000)}.${String(hundredths % 10000).padStart(4, '0')}`;
}

export function collectPortfolioSelection(plan: PortfolioPlan, state: ProductSelectionState, percentages: Record<string, string>): PortfolioSelection {
  if (state.scopeKey !== plan.asset_scope_id) throw new Error('选择与整体方案不属于同一资产范围');
  const items = state.items.map(({ share }) => ({ product_id: share.product_id, product_version: share.product_version,
    share_class: share.share_class, target_ratio_total: percentageRatio(percentages[share.product_id] ?? '') }));
  return { portfolio_id: plan.portfolio_id, expected_version: plan.version, snapshot_id: plan.snapshot_id, context_id: plan.context_id, items };
}

export function selectionKey(selection: Pick<PortfolioSelection, 'items'>): string {
  return JSON.stringify(selection.items.map(i => [i.product_id, i.product_version, i.share_class, Number(i.target_ratio_total)]).sort((a,b)=>String(a[0]).localeCompare(String(b[0]))));
}
