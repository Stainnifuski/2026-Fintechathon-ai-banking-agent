import { apiClient } from './api';

export type { PortfolioPlan, PortfolioSnapshot, PortfolioSelection } from '../types/wealthPortfolio';
import type { PortfolioPlan, PortfolioSelection, PortfolioCandidateGroup } from '../types/wealthPortfolio';
import { formatFen } from '../utils/money';
export type CandidateGroup = PortfolioCandidateGroup;
export type ProductCandidate = CandidateGroup['items'][number];
export type CandidateShare = ProductCandidate['share_classes'][number];
export interface PortfolioAction {
  product_id: string; product_version: number; disclosure_version: number; account_id: string;
  bucket: string; name?: string; current_fen: number; target_fen: number; difference_fen: number;
  direction: 'purchase' | 'redeem'; amount_fen: number; units: string; status: string;
}
export interface PreparedPortfolio {
  portfolio_id: string; portfolio_version: number; execution_plan_id: string | null;
  execution_plan_version: number | null; actions: PortfolioAction[]; stage: string; execution_plan?: Plan;
}
export const revisePortfolio = (selection: PortfolioSelection) => put<PortfolioPlan>(`/portfolio/plans/${selection.portfolio_id}`, selection);
export const preparePortfolio = (plan: PortfolioPlan,paymentAccountId?:string) => post<PreparedPortfolio>(`/portfolio/plans/${plan.portfolio_id}/prepare`, { expected_version: plan.version, snapshot_id: plan.snapshot_id,...(paymentAccountId?{payment_account_id:paymentAccountId}:{}) });

export interface Command {
  operation_id: string; direction: 'purchase' | 'redeem'; product_id: string;
  product_version: number; disclosure_version: number; account_id: string;
  amount_fen: number; units: string; plan_id?: string | null; plan_version?: number;
  command_version?: 1 | 2; redemption_mode?: 'standard' | 'quick'; nav_quote_id?: string | null;
  quoted_cash_fen?: number | null; context_id?: string | null; context_epoch?: number | null;
}
export interface Product {
  product_risk_source?: string; project_policy_version?: string | null; underlying_source_kind?: string;
  product_id: string; version: number; name: string; kind: string; category: string;
  product_risk_level: number; status: string; nav: string | null; nav_date: string | null;
  rules: { min_amount_fen: number; holding_days: number; cash_business_days: number; purchase_fee_rate: string;
    sales_service_fee_rate?:string|null; redeem_fee_bands?:{days_lt:number;rate:string}[]; quick_redemption?: {start: string; end: string; daily_limit_fen: number} };
  disclosure: { version: number; nature: string; manager: string; seller: string; risk: string;
    fees: string; liquidity: string; pricing: string; settlement: string; cancellation: string; limitations: string };
}
export interface Order {
  operation_id: string; account_id: string; version_id: string; direction: string;
  amount_fen: number; units: string; digest: string; status: string; pricing_date: string;
  accepted_at?: string; product_id?: string; product_name?: string; product_version?: number;
  disclosure_version?: number; share_class?: string | null; daily_metric?: DailyMetric;
  confirm_date: string; cash_date: string; details: { waiting_reason?: string | null;
    verification?: { method: string; simulated: boolean; replacement_required: boolean };
    actual_nav?: string; actual?: { units?: string; fee_fen: number; cash_fen?: number; realized_pnl_fen?: number } };
}
export interface Assets {
  asset_summary?: AssetSummary;
  concentration?: { families_fen: Record<string,number>; categories_fen: Record<string,number>; overconcentrated_families: string[]; notice: string };
  historical_hypothesis?: { metrics: { return: string | null; max_drawdown: string | null; volatility: string | null } | null; notice: string; unavailable_reason?: string } | null;
  cash: { account_id: string; account_type: string; balance_fen: number; held_fen: number; available_fen: number }[];
  lots: { lot_id: string; product_id: string; product_name: string; account_id: string;
    units: string; redeemable_units: string; locked_units: string; held_units: string;
    cost_fen: number; market_value_fen: number | null; unrealized_pnl_fen: number | null; nav_date: string | null;
    quote_update_status?: string; quote_update_error?: string | null;
    version_id?: string; product_version?: number; disclosure_version?: number; share_class?: string | null;
    nav?: string | null; acquired_date?: string; unlock_date?: string; daily_metric?: DailyMetric }[];
  orders: Order[]; receivable_fen: number; realized_pnl_fen: number; bank_assets_fen: number | null; business_at: string;
  as_of?: string; context_id?: string; scope?: 'ordinary' | 'disposable_simulation'; valuation_complete?: boolean;
}
export interface AssetSummary {
  schema_version:'wealth-asset-summary-v1';asset_scope_id:string;account_ids:string[];snapshot_id:string;
  as_of:string;business_at:string;cash_balance_fen:number;held_fen:number;available_cash_fen:number;
  positions_market_value_fen:number|null;redemption_receivable_fen:number;total_assets_fen:number|null;
  valuation_complete:boolean;missing_fields:string[];data_source:string;
}
export interface DailyMetric { status: 'ok' | 'unavailable' | 'not_applicable'; label: string; value_percent: string | null;
  date: string | null; previous_date: string | null; reason: string | null; source: string }
export interface CancellationEligibility { operation_id: string; eligibility: 'ALLOWED' | 'DENIED' | 'UNKNOWN'; reason: string;
  order_digest: string; version_id: string; deadline: string | null; consequence: string }
export interface OrderRow extends Order { object_type: 'orders'; product_id: string; product_version: number; product_name: string }
export interface PositionRow { object_type: 'positions'; position_key: string; account_id: string; product_id: string; product_version: number;
  product_name: string; disclosure_version: number | null; share_class: string | null; lot_ids: string[]; lots: Assets['lots'];
  units: string; redeemable_units: string; held_units: string; locked_units: string; market_value_fen: number | null;
  unrealized_pnl_fen: number | null; cost_fen: number; total_ratio: string | null; nav: string | null; nav_date: string | null; daily_metric: DailyMetric | null }
export interface OrderTableData { object_type: 'orders'; scope_key: string; rows: OrderRow[] }
 export interface PositionTableData { object_type: 'positions'; scope_key: string; rows: PositionRow[]; business_date?:string }
export interface Assessment { assessment_id: string; capacity_level: number; purchase_allowed: boolean; confirmed: boolean; expires_at: string }
export interface Questionnaire { version: string; profile_version: number; daily_limit?: number|null; rolling_year_limit?: number|null; notice?: string; questions: { id: string; title: string; options: string[] }[] }
export interface Preview { authorization_id: string; authorization_status: string; command: Command; digest: string;
  notice?: string; execution_policy?: string|null;
  product_risk_level: number; suitability: { capacity_level: number; expires_at: string; matched: boolean } | null;
  product_name: string; disclosure: Product['disclosure']; estimate: { units?: string; fee_fen: number; cash_fen?: number;
    nav_available_at?:string;daily_used_fen?:number;daily_remaining_fen?:number;
    contract?:{start:string;end:string;daily_limit_fen:number;window:string} } | null;
  estimated_nav: string | null; estimated_nav_date: string | null; pricing_date: string; confirm_date: string; cash_date: string; expires_at: string }
export interface PlanRequest { kind: 'new_cash' | 'adjustment'; account_id: string; version: number; budget_fen: number;
  reserve_fen: number | null; horizon_days: number; excluded_products: string[]; budget_source?: 'bank_available_cash' | 'user_specified' }
export interface VerificationMode { mode: 'UNIFIED_MFA'; methods: ('totp' | 'sms')[]; sms_simulated: boolean; simulated: boolean; replacement_required: boolean }
export interface PlanPreview { verification_reference_id: string; plan_id: string; version: number; items: Preview[] }
export interface Plan { plan_id: string; version: number; status: string; stage: string; next_action?: string; last_error?:{code:string;message:string}; request: PlanRequest;
  history?: { version: number; stage: string; items: { command: Command; status: string; bank_result?: Order }[] }[];
  diagnostic?: { note: string; goal_shortfall_fen: number; allocation: { category: string; current_fen: number; target_percent: number; target_fen: number; difference_fen: number }[] };
  configuration: { investment_fen: number; retained_cash_fen: number; unallocated_fen: number; estimated_fee_fen?:number|null; account_remaining_cash_fen?:number; summary?:string[] };
  items: { command: Command; reason: string; status: string; bank_result?: Order }[] }
export interface ExternalAsset { asset_id: string; label: string; category: string; amount_fen: number; currency: string }
export interface Goal { goal_id: string; version: number; title: string; amount_fen: number; due_date: string; liquidity: string; priority: number;
  allocations: { asset_ref: string; amount_fen: number }[] }
export interface Profile { version: number; data: { monthly_income_fen?: number; monthly_expenses_fen?: number;
  liabilities_fen?: number; external_assets?: ExternalAsset[]; updated_at?: string }; goals: Goal[] }
export interface ChatReply { mode: string; kind: string; message: string; session_id?: string; version?: number;
  requirement_card?: {version:number;bank_cash:{available_fen:number;account_id:string;as_of:string}|null;fields:Record<string,{state:string;value?:unknown;resolved_fen?:number;resolved_days?:number;quote?:string}>};
  client_message_id?: string;
  plan?: Plan; command?: Command; products?: Product[];
  task?: WealthTask; assets?: Assets; order?: Order;
  citations?: { document_id: string; title: string; excerpt: string; source_type: string; source_url?: string; published_at?: string; version: string; content_hash: string }[] }
export interface WealthTask {
  plan_id?: string | null;
  task_id: string; session_id: string; account_id: string; context_id: string; version: number;
  status: string; control_mode: 'AGENT' | 'HANDOFF_PENDING' | 'HUMAN'; owner_id: string | null;
  waiting_reason: string | null; goal: string;
  notes: {action: string; reason: string; occurred_at: string}[];
  nodes: {node_id: string; kind: string; status: string; depends_on: string[]; operation_id?: string | null; command?: Command | null}[];
}
export const money = (fen: number | null | undefined) => fen == null ? '未知' : formatFen(fen);
export const get = <T,>(path: string) => apiClient.get<T>(`/wealth${path}`).then(r => r.data);
export const post = <T,>(path: string, body: unknown, novaTask?:string) => apiClient.post<T>(`/wealth${path}`, body, novaTask?{headers:{'X-Nova-Wealth-Task':novaTask}}:undefined).then(r => r.data);
export const put = <T,>(path: string, body: unknown) => apiClient.put<T>(`/wealth${path}`, body).then(r => r.data);
export function fen(text: string): number {
  if (!/^\d+(\.\d{1,2})?$/.test(text)) throw new Error('金额须精确到分');
  const [whole, fraction = ''] = text.split('.');
  const value = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (!Number.isSafeInteger(value) || value > 2_000_000_000) throw new Error('超出模拟金额上限');
  return value;
}
