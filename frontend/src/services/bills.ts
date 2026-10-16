import { apiClient } from './api';

export interface BillQuery { period: 'month' | 'year'; date: string; account_id?: string | null; category?: string | null; }
export interface Totals {
  inflow_fen: number; outflow_fen: number; net_inflow_fen: number;
  consumption_fen: number; refund_fen: number; net_consumption_fen: number;
  transfer_in_fen: number; transfer_out_fen: number; salary_fen: number;
  /** Compatibility fields: wealth cashflows are excluded from the whole report. */
  investment_purchase_fen: 0; investment_redemption_fen: 0;
  count: number; consumption_count: number; unknown_count: number;
}
export interface Anomaly { kind: 'large' | 'duplicate' | 'increase'; title: string; reason: string; tx_ids: string[]; category?: string | null; }
export interface BillAnalysis {
  query: BillQuery; excluded_transaction_types: string[]; start: string; end_exclusive: string; previous_start: string;
  previous_end_exclusive: string; as_of: string; partial: boolean; totals: Totals; previous_totals: Totals;
  change_fen: number; change_percent: number | null;
  change_explanation: ChangeExplanation;
  categories: { category: string; label: string; amount_fen: number; count: number; share_percent: number; previous_fen: number; change_fen: number; change_percent: number | null }[];
  trend: { date: string; consumption_fen: number }[];
  anomalies: Anomaly[]; report: string[]; notices: string[];
}
export interface BillPage {
  total: number; page: number; page_size: number;
  items: { tx_id: string; account_id: string; type: string; category: string; amount_fen: number; timestamp: string; counterparty: string; note: string | null; status: string }[];
}
export interface BillReply { kind: 'analysis' | 'clarification'; mode: 'offline' | 'model' | 'fallback'; message: string; analysis?: BillAnalysis | null; }
export const analysis = (query: BillQuery) => apiClient.get<BillAnalysis>('/bills/analysis', { params: query }).then(r => r.data);
export const details = (query: BillQuery, page: number, as_of: string, tx_ids?: string) =>
  apiClient.get<BillPage>('/bills/transactions', { params: { ...query, page, as_of, tx_ids } }).then(r => r.data);
export const ask = (message: string, context: BillQuery) => apiClient.post<BillReply>('/bills/messages', { message, context }).then(r => r.data);

export interface Evidence { tx_id: string; account_id: string; amount_fen: number; timestamp: string; counterparty: string; }
export interface ChangeDriver {
  category: string; merchant: string; current_fen: number; previous_fen: number;
  current_count: number; previous_count: number; change_fen: number;
  frequency_effect_fen: number; unit_amount_effect_fen: number; entry_exit_effect_fen: number;
  new_large_single: boolean; explanation: string; current_evidence: Evidence[]; previous_evidence: Evidence[];
}
export interface ChangeExplanation {
  total_change_fen: number; frequency_effect_fen: number; unit_amount_effect_fen: number;
  entry_exit_effect_fen: number; drivers: ChangeDriver[]; notices: string[];
}
export interface SubscriptionInsight {
  account_id: string; merchant: string; subscription_ids: string[]; active_subscription_ids: string[];
  configured_monthly_fen: number; current_fen: number; previous_fen: number;
  current_count: number; previous_count: number; flags: string[]; explanations: string[];
  current_evidence: Evidence[]; previous_evidence: Evidence[];
}
export interface SubscriptionInsights { as_of: string; items: SubscriptionInsight[]; notices: string[]; }
export interface CashflowScenario {
  days: number; account_id: string | null;
  reductions: { category: string; percent: number }[]; excluded_subscription_ids: string[];
}
export interface CashflowProjection {
  as_of: string; end_exclusive: string; days: number; account_id: string | null;
  opening_balance_fen: number; held_fen: number; opening_available_fen: number;
  planned_outflow_fen: number; estimated_income_fen: number; estimated_variable_spending_fen: number;
  baseline_end_fen: number; scenario_end_fen: number; scenario_saving_fen: number;
  minimum_baseline_fen: number; first_negative_date: string | null; scenario: CashflowScenario;
  spending_baselines: { category: string; daily_fen: number; sample_count: number; observed_months: number; estimated_total_fen: number; scenario_total_fen: number }[];
  events: { at: string; label: string; amount_fen: number; source: string; reference_id?: string; excluded_in_scenario: boolean; evidence_tx_ids: string[] }[];
  timeline: { date: string; planned_balance_fen: number; baseline_balance_fen: number; scenario_balance_fen: number; historical_low_balance_fen: number; historical_high_balance_fen: number }[];
  notices: string[];
}
export const subscriptionInsights = (query: BillQuery) => apiClient.get<SubscriptionInsights>('/bills/subscription-insights', { params: query }).then(r => r.data);
export const cashflow = (scenario: CashflowScenario) => apiClient.post<CashflowProjection>('/bills/cashflow', scenario).then(r => r.data);
