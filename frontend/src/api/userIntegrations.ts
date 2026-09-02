import { api } from './client'

export interface MailboxItem {
  id: number
  name: string
  email: string
  provider_type: string
  is_default: boolean
  imap_host: string | null
  imap_port: number | null
  imap_use_ssl: boolean
  imap_username: string | null
  smtp_host: string | null
  smtp_port: number | null
  smtp_use_tls: boolean
  smtp_username: string | null
  has_credentials: boolean
  sync_enabled: boolean
  notify_filter_rules: Array<{
    field: string
    operator: string
    value: string
  }>
  created_at: string
  updated_at: string
}

export interface MailboxCreateInput {
  name: string
  email: string
  provider_type?: string
  is_default?: boolean
  imap_host?: string
  imap_port?: number
  imap_use_ssl?: boolean
  imap_username?: string
  smtp_host?: string
  smtp_port?: number
  smtp_use_tls?: boolean
  smtp_username?: string
  password_or_token?: string
  sync_enabled?: boolean
  notify_filter_rules?: Array<Record<string, any>>
}

export interface MailboxUpdateInput {
  name?: string
  email?: string
  is_default?: boolean
  imap_host?: string
  imap_port?: number
  imap_use_ssl?: boolean
  imap_username?: string
  smtp_host?: string
  smtp_port?: number
  smtp_use_tls?: boolean
  smtp_username?: string
  password_or_token?: string
  sync_enabled?: boolean
  notify_filter_rules?: Array<Record<string, any>>
}

export interface CalendarItem {
  id: number
  name: string
  provider_type: string
  is_default: boolean
  caldav_url: string | null
  caldav_username: string | null
  has_credentials: boolean
  created_at: string
  updated_at: string
}

export interface CalendarCreateInput {
  name: string
  provider_type?: string
  is_default?: boolean
  caldav_url?: string
  caldav_username?: string
  password_or_token?: string
}

export interface CalendarUpdateInput {
  name?: string
  is_default?: boolean
  caldav_url?: string
  caldav_username?: string
  password_or_token?: string
}

export interface TestResult {
  ok: boolean
  message?: string
  details?: string
}

export const userIntegrationsApi = {
  // Mailboxes
  getMailboxes: () => api<MailboxItem[]>('/user/integrations/mailboxes'),
  createMailbox: (data: MailboxCreateInput) =>
    api<MailboxItem>('/user/integrations/mailboxes', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateMailbox: (id: number, data: MailboxUpdateInput) =>
    api<MailboxItem>(`/user/integrations/mailboxes/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteMailbox: (id: number) =>
    api<void>(`/user/integrations/mailboxes/${id}`, {
      method: 'DELETE',
    }),
  testMailbox: (id: number) =>
    api<TestResult>(`/user/integrations/mailboxes/${id}/test`, {
      method: 'POST',
    }),

  // Calendars
  getCalendars: () => api<CalendarItem[]>('/user/integrations/calendars'),
  createCalendar: (data: CalendarCreateInput) =>
    api<CalendarItem>('/user/integrations/calendars', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateCalendar: (id: number, data: CalendarUpdateInput) =>
    api<CalendarItem>(`/user/integrations/calendars/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteCalendar: (id: number) =>
    api<void>(`/user/integrations/calendars/${id}`, {
      method: 'DELETE',
    }),
  testCalendar: (id: number) =>
    api<TestResult>(`/user/integrations/calendars/${id}/test`, {
      method: 'POST',
    }),
}
