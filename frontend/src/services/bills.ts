import { apiClient } from './api';

export interface BillQuery { period: 'month' | 'year'; date: string; account_id?: string | null; category?: string | null; tag_id?: string | null; }
export interface Totals {
  inflow_fen: number; outflow_fen: number; net_inflow_fen: number;
  consumption_fen: number; refund_fen: number; linked_refund_fen: number; unlinked_refund_fen: number; net_consumption_fen: number;
  transfer_in_fen: number; transfer_out_fen: number; salary_fen: number;
  /** Compatibility fields: wealth cashflows are excluded from the whole report. */
  investment_purchase_fen: 0; investment_redemption_fen: 0;
  count: number; consumption_count: number; unknown_count: number;
}
export interface Anomaly { anomaly_id:string;fingerprint:string;review_status:'pending'|'normal'|'resolved';review_note:string;review_version:number;evidence_changed:boolean; kind: 'large' | 'duplicate' | 'increase'; title: string; reason: string; tx_ids: string[]; category?: string | null; }
export interface BillAnalysis {
  query: BillQuery; excluded_transaction_types: string[]; start: string; end_exclusive: string; previous_start: string;
  previous_end_exclusive: string; as_of: string; partial: boolean; totals: Totals; previous_totals: Totals;
  change_fen: number; change_percent: number | null;
  change_explanation: ChangeExplanation;
  categories: { category: string; label: string; refund_fen: number; net_amount_fen: number; amount_fen: number; count: number; share_percent: number; previous_fen: number; change_fen: number; change_percent: number | null }[];
  trend: { date: string; consumption_fen: number }[];
  anomalies: Anomaly[]; report: string[]; notices: string[];
}
export interface BillPage {
  total: number; page: number; page_size: number;
  items: (TransactionMetadata & { tx_id: string; account_id: string; type: string; category: string; amount_fen: number; timestamp: string; counterparty: string; note: string | null; status: string })[];
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
  one_off_events?: {at:string;label:string;amount_fen:number}[];
  reductions: { category: string; percent: number }[]; excluded_subscription_ids: string[];
}
export interface CashflowProjection {
  as_of: string; end_exclusive: string; days: number; account_id: string | null;
  opening_balance_fen: number; held_fen: number; opening_available_fen: number;
  planned_outflow_fen: number; estimated_income_fen: number; estimated_recurring_spending_fen: number; estimated_variable_spending_fen: number;
  baseline_end_fen: number; scenario_end_fen: number; scenario_saving_fen: number;
  calibration: {factor:number;sample_periods:number;applied:boolean;reason:string};
  minimum_baseline_fen: number; first_negative_date: string | null; scenario: CashflowScenario;
  spending_baselines: { category: string; daily_fen: number; sample_count: number; observed_months: number; estimated_total_fen: number; scenario_total_fen: number }[];
  events: { at: string; label: string; amount_fen: number; source: string; reference_id?: string; excluded_in_scenario: boolean; evidence_tx_ids: string[] }[];
  timeline: { date: string; planned_balance_fen: number; baseline_balance_fen: number; scenario_balance_fen: number; historical_low_balance_fen: number; historical_high_balance_fen: number }[];
  notices: string[];
}
export const subscriptionInsights = (query: BillQuery) => apiClient.get<SubscriptionInsights>('/bills/subscription-insights', { params: query }).then(r => r.data);
export const cashflow = (scenario: CashflowScenario) => apiClient.post<CashflowProjection>('/bills/cashflow', scenario).then(r => r.data);

export interface TransactionMetadata {
  raw_category?:string;effective_category?:string;category_source?:string;classification_version?:number;category_rule_version?:number;suggested_category?:string|null;suggestion_reason?:string|null;refunded_fen?:number;refundable_fen?:number|null;
  merchant_id?: string | null; merchant_name?: string | null; original_tx_id?: string | null;
  refund_link_source?: string | null; original_category?: string | null; refund_tx_ids?: string[]; transfer_pair_id?: string | null; paired_tx_id?: string | null;
  recurring_stream_id?: string | null; tags: { tag_id: string; name: string }[]; annotation_version: number;
}
export type BillTransaction = BillPage['items'][number];
export interface FoundationCatalog {
  merchants: { merchant_id: string; name: string; aliases: string[]; bank_verified: boolean }[];
  tags: { tag_id: string; name: string; transaction_count: number }[];
  transactions_total: number; metadata_total: number; notices: string[];
}
export interface RecurringItem {
  stream_id: string; account_id: string; merchant_id: string; merchant: string; kind: 'income' | 'expense';
  source: 'subscription' | 'detected' | 'manual'; anchor_at?:string|null; category?:string; subscription_id: string | null; frequency: string;
  anchor_day: number; month_end: boolean; amount_fen: number; status: string; enabled: boolean;
  version: number; sample_count: number; last_observed_at: string | null; next_expected_at: string | null; evidence_tx_ids: string[];
}
export const foundations = () => apiClient.get<FoundationCatalog>('/bills/foundations').then(r => r.data);
export const refreshFoundations = () => apiClient.post('/bills/foundations/refresh').then(r => r.data);
export const recurring = () => apiClient.get<{ items: RecurringItem[]; notices: string[] }>('/bills/recurring').then(r => r.data);
export const setRecurring = (item: RecurringItem, enabled: boolean) => apiClient.patch(`/bills/recurring/${encodeURIComponent(item.stream_id)}`, { enabled, expected_version: item.version });
export const saveTags = (tx: BillTransaction, tags: string[]) => apiClient.put<TransactionMetadata>(`/bills/transactions/${encodeURIComponent(tx.tx_id)}/annotations`, { tags, expected_version: tx.annotation_version }).then(r => r.data);
export const addMerchantAlias = (id: string, name: string) => apiClient.post(`/bills/merchants/${encodeURIComponent(id)}/aliases`, { name });
export const mergeMerchants = (target: string, source: string, preview_digest: string) => apiClient.post(`/bills/merchants/${encodeURIComponent(target)}/merge`, { source_merchant_id: source, preview_digest });
export const transaction = (id: string) => apiClient.get<BillTransaction>(`/bills/transactions/${encodeURIComponent(id)}`).then(r => r.data);

export interface CategoryRule { merchant_id: string; category: string; version: number; }
export const categoryRules = () => apiClient.get<CategoryRule[]>('/bills/category-rules').then(r=>r.data);
export const correctCategory = (tx: BillTransaction, category: string | null, apply: boolean) => apiClient.put(`/bills/transactions/${encodeURIComponent(tx.tx_id)}/classification`,{category,apply_to_merchant:apply,expected_version:tx.classification_version||0,expected_rule_version:tx.category_rule_version||0});
export const removeCategoryRule = (id: string, version: number) => apiClient.delete(`/bills/category-rules/${encodeURIComponent(id)}`,{data:{expected_version:version}});
export interface BudgetInput { month: string; account_id: string | null; category: string | null; amount_fen: number; warning_percent: number; active: boolean; expected_version: number; }
export interface BudgetItem extends Omit<BudgetInput,'expected_version'> {budget_id:string;version:number;spent_fen:number;remaining_fen:number;usage_percent:number;projected_spending_fen:number;projection_has_variable_baseline:boolean;status:string;}
export const budgets = (month:string) => apiClient.get<{items:BudgetItem[];notices:string[]}>('/bills/budgets',{params:{month}}).then(r=>r.data);
export const saveBudget = (body:BudgetInput) => apiClient.put('/bills/budgets',body);
export const reviewAnomaly = (item:Anomaly,result:BillAnalysis,status:string,note:string) => apiClient.put('/bills/anomaly-reviews',{query:result.query,as_of:result.as_of,anomaly_id:item.anomaly_id,fingerprint:item.fingerprint,status,note,expected_version:item.review_version});
export interface RecurringPlan {name:string;account_id:string;kind:'income'|'expense';frequency:string;amount_fen:number;anchor_at:string;month_end:boolean;category:string;enabled:boolean;active:boolean;expected_version:number;}
export const saveRecurringPlan = (body:RecurringPlan,id?:string) => id ? apiClient.put(`/bills/recurring/plans/${encodeURIComponent(id)}`,body) : apiClient.post('/bills/recurring/plans',body);
export interface MergePreview {target:string;source:string;transaction_count:number;alias_count:number;allowed:boolean;preview_digest:string;notices:string[];}
export const previewMerge = (target:string,source:string) => apiClient.get<MergePreview>(`/bills/merchants/${encodeURIComponent(target)}/merge-preview`,{params:{source_merchant_id:source}}).then(r=>r.data);
export interface MerchantHistory {event_id:string;action:string;created_at:string;source_name:string|null;target_name:string|null;can_undo:boolean;}
export const merchantHistory = () => apiClient.get<MerchantHistory[]>('/bills/merchant-history').then(r=>r.data);
export const undoMerchantMerge = (eventId:string) => apiClient.post(`/bills/merchant-history/${encodeURIComponent(eventId)}/undo`);
export const previewSplit = (id:string,name:string,tx_ids:string[],aliases:string[]) => apiClient.post<{preview_digest:string;transaction_count:number;moving_count:number;moving_tx_ids:string[];allowed:boolean;notices:string[]}>(`/bills/merchants/${encodeURIComponent(id)}/split-preview`,{name,tx_ids,aliases}).then(r=>r.data);
export const splitMerchant = (id:string,name:string,tx_ids:string[],aliases:string[],preview_digest:string) => apiClient.post(`/bills/merchants/${encodeURIComponent(id)}/split`,{name,tx_ids,aliases,preview_digest});
export interface ForecastComparison {snapshot_id:string;scope_changed:boolean;complete?:boolean;start_at?:string;end_at?:string;predicted_net_flow_fen?:number;actual_net_flow_fen?:number;error_fen?:number|null;}
export const forecasts = () => apiClient.get<{items:ForecastComparison[];notices:string[]}>('/bills/forecasts').then(r=>r.data);
export const saveForecast = (scenario:CashflowScenario) => apiClient.post('/bills/forecasts',{scenario:{...scenario,reductions:scenario.reductions.filter(r=>r.percent!==0)}});
