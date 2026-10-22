import { apiClient } from './api';

export type AuditObject = { domain: string; object_type: string; object_id: string; version: string | null };
export type AuditEvent = {
  event_id: string; schema_version: number; trace_id: string | null; request_id: string | null;
  span_id: string | null; parent_span_id: string | null; subject_user_id: string;
  actor_id: string | null; actor_type: string; domain: string; action: string; phase: string;
  outcome: string; permission_level: string; error_code: string | null; source_module: string;
  occurred_at: string; recorded_at: string; duration_ms: number | null;
  facts: Record<string, unknown>; objects: AuditObject[]; legacy: boolean;
};
export type AuditPage = {
  items: AuditEvent[]; next_cursor?: string | null; truncated?: boolean;
  audit_health: { status: string; fallback_count: number };
};
export type AuditFilters = {
  since: string; until: string; domain?: string; action?: string; outcome?: string;
  permission_level?: string; object_type?: string; object_id?: string; view?: 'all' | 'business';
};
export const auditEvents = (params: AuditFilters & { cursor?: string; limit?: number }) =>
  apiClient.get<AuditPage>('/audit/events', { params }).then(r => r.data);
export const auditTrace = (id: string, cursor?: string) =>
  apiClient.get<AuditPage>(`/audit/traces/${encodeURIComponent(id)}`, { params: { cursor } }).then(r => r.data);
export const auditObject = (object: AuditObject) =>
  apiClient.get<AuditPage>(`/audit/objects/${encodeURIComponent(object.object_type)}/${encodeURIComponent(object.object_id)}`,
    { params: { domain: object.domain } }).then(r => r.data);
export const exportAudit = (params: AuditFilters) =>
  apiClient.get<Blob>('/audit/export', { params, responseType: 'blob' }).then(r => r.data);
