import { apiClient } from './api';

export interface CalendarEvent {
  event_id: string;
  title: string;
  description: string;
  location: string;
  starts_at: string;
  ends_at: string | null;
  all_day: boolean;
  recurring: boolean;
  source: 'manual' | 'ics';
}

export interface EventCreate {
  title: string;
  description: string;
  location: string;
  starts_at: string;
  ends_at: string | null;
}

export const listEvents = (start: string, end: string, timeZone: string) =>
  apiClient.get<CalendarEvent[]>('/calendar/events', { params: { start, end, time_zone: timeZone } }).then(r => r.data);
export const createEvent = (event: EventCreate) =>
  apiClient.post<CalendarEvent>('/calendar/events', event).then(r => r.data);
export const deleteEvent = (id: string) => apiClient.delete(`/calendar/events/${id}`);
export const importCalendar = (file: File, timeZone: string) => {
  const body = new FormData();
  body.append('file', file);
  body.append('time_zone', timeZone);
  return apiClient.post<{ created: number; updated: number; skipped: number; total: number }>(
    '/calendar/import', body, { headers: { 'Content-Type': 'multipart/form-data' } },
  ).then(r => r.data);
};
