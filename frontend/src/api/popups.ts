import { api } from './client'

export interface PanelPopup {
  id: number
  title: string
  content_markdown: string
  is_active: boolean
  start_at: string | null
  end_at: string | null
  button_text: string | null
  button_url: string | null
  created_at: string
  updated_at: string
}

export interface PanelPopupCreateInput {
  title: string
  content_markdown: string
  is_active?: boolean
  start_at?: string | null
  end_at?: string | null
  button_text?: string | null
  button_url?: string | null
}

export interface PanelPopupUpdateInput {
  title?: string
  content_markdown?: string
  is_active?: boolean
  start_at?: string | null
  end_at?: string | null
  button_text?: string | null
  button_url?: string | null
}

export async function getActivePopup(): Promise<PanelPopup | null> {
  return api<PanelPopup | null>('/popups/active')
}

export async function dismissPopup(popupId: number, mode: 'snooze' | 'permanent'): Promise<{ ok: boolean; mode: string }> {
  return api<{ ok: boolean; mode: string }>(`/popups/${popupId}/dismiss`, {
    method: 'POST',
    body: JSON.stringify({ mode }),
  })
}

export async function listAdminPopups(): Promise<PanelPopup[]> {
  return api<PanelPopup[]>('/popups/admin/list')
}

export async function createAdminPopup(input: PanelPopupCreateInput): Promise<PanelPopup> {
  return api<PanelPopup>('/popups/admin', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export async function updateAdminPopup(popupId: number, input: PanelPopupUpdateInput): Promise<PanelPopup> {
  return api<PanelPopup>(`/popups/admin/${popupId}`, {
    method: 'PUT',
    body: JSON.stringify(input),
  })
}

export async function deleteAdminPopup(popupId: number): Promise<void> {
  return api<void>(`/popups/admin/${popupId}`, {
    method: 'DELETE',
  })
}
