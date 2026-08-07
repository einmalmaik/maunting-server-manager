/**
 * API-Zugriff auf die Hoster-Anbindung (Phase 6).
 *
 * Secrets werden vom Backend ausschliesslich beim Anlegen bzw. Rotieren
 * zurueckgegeben. Es gibt bewusst keinen Lesepfad fuer einen Klartext-Key —
 * das Frontend haelt einen frisch erzeugten Wert nur so lange im Zustand, wie
 * er dem Betreiber einmalig angezeigt wird.
 */
import { api } from './client'

export interface HosterIntegration {
  id: number
  name: string
  slug: string
  enabled: boolean
  service_user_id: number
  webhook_url: string | null
  terminate_grace_days: number
  api_key_hint: string | null
  webhook_secret_configured: boolean
  webhook_secret_hint: string | null
  created_at: string
  updated_at: string
}

export interface HosterIntegrationWrite {
  name: string
  slug: string
  enabled: boolean
  service_user_id: number
  webhook_url: string | null
  terminate_grace_days: number
}

export interface HosterSecret {
  value: string
  hint: string
}

export interface HosterProduct {
  id: number
  integration_id: number
  external_product_key: string
  game_type: string
  ram_limit_mb: number | null
  cpu_limit_percent: number | null
  disk_limit_gb: number | null
  node_id: number | null
  backup_interval_hours: number | null
  enabled: boolean
}

export type HosterProductWrite = Omit<HosterProduct, 'id' | 'integration_id'>

export interface HosterService {
  external_service_id: string
  desired_state: 'active' | 'suspended' | 'terminated'
  status: string
  status_code: string | null
  server_id: number | null
  task_id: string | null
  correlation_id: string
  terminate_after: string | null
  updated_at: string
}

export interface HosterDelivery {
  id: number
  event_type: string
  status: 'pending' | 'ok' | 'failed'
  attempt: number
  response_code: number | null
  error: string | null
  correlation_id: string
  created_at: string
  sent_at: string | null
}

export const hosterApi = {
  listIntegrations: () => api<HosterIntegration[]>('/hoster/integrations'),

  createIntegration: (payload: HosterIntegrationWrite) =>
    api<HosterSecret>('/hoster/integrations', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  updateIntegration: (id: number, payload: Partial<HosterIntegrationWrite>) =>
    api<HosterIntegration>(`/hoster/integrations/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  deleteIntegration: (id: number) =>
    api(`/hoster/integrations/${id}`, { method: 'DELETE' }),

  rotateApiKey: (id: number) =>
    api<HosterSecret>(`/hoster/integrations/${id}/api-key`, { method: 'POST' }),

  rotateWebhookSecret: (id: number) =>
    api<HosterSecret>(`/hoster/integrations/${id}/webhook-secret`, { method: 'POST' }),

  listProducts: (id: number) =>
    api<HosterProduct[]>(`/hoster/integrations/${id}/products`),

  saveProduct: (id: number, payload: HosterProductWrite) =>
    api<HosterProduct>(`/hoster/integrations/${id}/products`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  deleteProduct: (id: number, productId: number) =>
    api(`/hoster/integrations/${id}/products/${productId}`, { method: 'DELETE' }),

  listServices: (id: number) =>
    api<HosterService[]>(`/hoster/integrations/${id}/services`),

  listDeliveries: (id: number) =>
    api<HosterDelivery[]>(`/hoster/integrations/${id}/deliveries`),

  retryDelivery: (id: number, deliveryId: number) =>
    api(`/hoster/integrations/${id}/deliveries/${deliveryId}/retry`, { method: 'POST' }),
}
