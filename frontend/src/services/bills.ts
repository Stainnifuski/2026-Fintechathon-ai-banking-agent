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
