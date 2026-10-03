import { apiClient } from './api';

export interface Command {
  operation_id: string; direction: 'purchase' | 'redeem'; product_id: string;
  product_version: number; disclosure_version: number; account_id: string;
  amount_fen: number; units: string; plan_id?: string | null; plan_version?: number;
  command_version?: 1 | 2; redemption_mode?: 'standard' | 'quick'; nav_quote_id?: string | null;
  quoted_cash_fen?: number | null; context_id?: string | null; context_epoch?: number | null;
}
export interface Product {
  product_id: string; version: number; name: string; kind: string; category: string;
  product_risk_level: number; status: string; nav: string | null; nav_date: string | null;
  rules: { min_amount_fen: number; holding_days: number; cash_business_days: number; purchase_fee_rate: string;
    quick_redemption?: {start: string; end: string; daily_limit_fen: number} };
  disclosure: { version: number; nature: string; manager: string; seller: string; risk: string;
    fees: string; liquidity: string; pricing: string; settlement: string; cancellation: string; limitations: string };
}
export interface Order {
  operation_id: string; account_id: string; version_id: string; direction: string;
  amount_fen: number; units: string; digest: string; status: string; pricing_date: string;
  confirm_date: string; cash_date: string; details: { waiting_reason?: string | null;
    verification?: { method: string; simulated: boolean; replacement_required: boolean };
    actual_nav?: string; actual?: { units?: string; fee_fen: number; cash_fen?: number; realized_pnl_fen?: number } };
}
export interface Assets {
  concentration?: { families_fen: Record<string,number>; categories_fen: Record<string,number>; overconcentrated_families: string[]; notice: string };
  historical_hypothesis?: { metrics: { return: string | null; max_drawdown: string | null; volatility: string | null } | null; notice: string; unavailable_reason?: string } | null;
  cash: { account_id: string; account_type: string; balance_fen: number; held_fen: number; available_fen: number }[];
  lots: { lot_id: string; product_id: string; product_name: string; account_id: string;
    units: string; redeemable_units: string; locked_units: string; held_units: string;
    cost_fen: number; market_value_fen: number | null; unrealized_pnl_fen: number | null; nav_date: string | null }[];
  orders: Order[]; receivable_fen: number; realized_pnl_fen: number; bank_assets_fen: number; business_at: string;
  as_of?: string; context_id?: string; scope?: 'ordinary' | 'disposable_simulation';
}
export interface Assessment { assessment_id: string; capacity_level: number; purchase_allowed: boolean; confirmed: boolean; expires_at: string }
export interface Questionnaire { version: string; profile_version: number; questions: { id: string; title: string; options: string[] }[] }
export interface Preview { authorization_id: string; authorization_status: string; command: Command; digest: string;
  product_risk_level: number; suitability: { capacity_level: number; expires_at: string; matched: boolean } | null;
  product_name: string; disclosure: Product['disclosure']; estimate: { units?: string; fee_fen: number; cash_fen?: number } | null;
  estimated_nav: string | null; estimated_nav_date: string | null; pricing_date: string; confirm_date: string; cash_date: string; expires_at: string }
export interface PlanRequest { kind: 'new_cash' | 'adjustment'; account_id: string; version: number; budget_fen: number;
  reserve_fen: number; horizon_days: number; excluded_products: string[]; budget_source?: 'bank_available_cash' | 'user_specified' }
export interface VerificationMode { mode: 'SIMULATED_CLICK' | 'TOTP'; simulated: boolean; replacement_required: boolean }
export interface PlanPreview { plan_id: string; version: number; items: Preview[] }
export interface Plan { plan_id: string; version: number; status: string; stage: string; next_action?: string; request: PlanRequest;
  history?: { version: number; stage: string; items: { command: Command; status: string; bank_result?: Order }[] }[];
  diagnostic?: { note: string; goal_shortfall_fen: number; allocation: { category: string; current_fen: number; target_percent: number; target_fen: number; difference_fen: number }[] };
  configuration: { investment_fen: number; retained_cash_fen: number; unallocated_fen: number };
  items: { command: Command; reason: string; status: string; bank_result?: Order }[] }
export interface ExternalAsset { asset_id: string; label: string; category: string; amount_fen: number; currency: string }
export interface Goal { goal_id: string; version: number; title: string; amount_fen: number; due_date: string; liquidity: string; priority: number;
  allocations: { asset_ref: string; amount_fen: number }[] }
export interface Profile { version: number; data: { monthly_income_fen?: number; monthly_expenses_fen?: number;
  liabilities_fen?: number; external_assets?: ExternalAsset[]; updated_at?: string }; goals: Goal[] }
export interface ChatReply { mode: string; kind: string; message: string; session_id?: string; version?: number;
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
  nodes: {node_id: string; kind: string; status: string; depends_on: string[]; operation_id?: string | null}[];
}
export const money = (fen: number | null | undefined) => fen == null ? '未知' : `¥${(fen / 100).toFixed(2)}`;
export const get = <T,>(path: string) => apiClient.get<T>(`/wealth${path}`).then(r => r.data);
export const post = <T,>(path: string, body: unknown) => apiClient.post<T>(`/wealth${path}`, body).then(r => r.data);
export const put = <T,>(path: string, body: unknown) => apiClient.put<T>(`/wealth${path}`, body).then(r => r.data);
export function fen(text: string): number {
  if (!/^\d+(\.\d{1,2})?$/.test(text)) throw new Error('金额须精确到分');
  const [whole, fraction = ''] = text.split('.');
  const value = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (!Number.isSafeInteger(value) || value > 2_000_000_000) throw new Error('超出模拟金额上限');
  return value;
}
