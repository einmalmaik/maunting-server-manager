/**
 * Bis 09/2026 verschickte das Backend `achievement_unlocked`, und niemand
 * hörte zu: neue Abzeichen sah nur, wer die Liste selbst öffnete.
 */

import { act, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/client', () => ({ api: vi.fn().mockResolvedValue([]) }))
vi.mock('@/lib/benachrichtigung', () => ({
  sendeGeraeteBenachrichtigung: vi.fn(),
  pruefeUndFrageGeraeteBerechtigung: vi.fn().mockResolvedValue(false),
}))
vi.mock('@/services/pushAbo', () => ({ abonniere: vi.fn(), kuendige: vi.fn() }))
vi.mock('@/services/mailboxPush', () => ({ kuendigeMailboxPush: vi.fn().mockResolvedValue(undefined) }))
vi.mock('@/services/deliveryReceiptService', () => ({
  sendE2eeDeliveryReceipt: vi.fn(),
  checkAndDispatchPendingDeliveryReceipts: vi.fn(),
}))
vi.mock('@/lib/offlineSync', () => ({ loadCalendarEventsOfflineFirst: vi.fn().mockResolvedValue([]) }))
vi.mock('@/stores/messengerNotificationStore', () => ({
  useMessengerNotificationStore: {
    getState: () => ({
      syncBlockedFromBackend: vi.fn().mockResolvedValue(undefined),
      syncMailboxDirectoryFromBackend: vi.fn().mockResolvedValue(undefined),
    }),
  },
  playNotificationChime: vi.fn(),
}))

import { api } from '@/api/client'
import { ServerIncidentNotifier } from './ServerIncidentNotifier'
import { useAuthStore } from '@/stores/authStore'
import { useToastStore } from '@/stores/toastStore'

describe('ServerIncidentNotifier — Errungenschaften', () => {
  beforeEach(() => {
    useToastStore.getState().clearAll()
    useAuthStore.setState({
      isAuthenticated: true,
      user: { id: 5, username: 'anna', device_notifications: true } as never,
    })
  })

  afterEach(() => {
    useAuthStore.setState({ isAuthenticated: false, user: null })
  })

  it('zeigt eine Freischaltung als Toast', async () => {
    render(<ServerIncidentNotifier />)
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent('msm:sync-event', {
          detail: {
            type: 'achievement_unlocked',
            achievement: { id: 'terminal_commander', title: 'Terminal Commander', points: 30 },
          },
        }),
      )
    })
    const meldungen = useToastStore.getState().toasts.map((t) => t.message)
    expect(meldungen.some((m) => m.includes('Terminal Commander') && m.includes('+30'))).toBe(true)
  })

  it('schweigt bei einem Ereignis ohne Titel', async () => {
    render(<ServerIncidentNotifier />)
    await act(async () => {
      window.dispatchEvent(new CustomEvent('msm:sync-event', { detail: { type: 'achievement_unlocked' } }))
    })
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })
})

describe('ServerIncidentNotifier — Vorfälle & Persistenz', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    useToastStore.getState().clearAll()
    useAuthStore.setState({
      isAuthenticated: true,
      user: { id: 5, username: 'anna', device_notifications: true } as never,
    })
  })

  afterEach(() => {
    useAuthStore.setState({ isAuthenticated: false, user: null })
    localStorage.clear()
    sessionStorage.clear()
    vi.mocked(api).mockReset().mockResolvedValue([])
  })

  it('zeigt einen Vorfall als Toast und speichert ihn konto-gebunden im localStorage', async () => {
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === '/system/incident-alerts') {
        return [
          {
            id: 101,
            uuid: 'inc-uuid-1',
            server_id: 1,
            server_name: 'Hightail Server',
            title: 'Autopilot: process_not_running',
            description: 'Der Prozess läuft nicht',
            type: 'process_not_running',
            severity: 'critical',
            timestamp: new Date().toISOString(),
          },
        ]
      }
      return []
    })

    const { unmount } = render(<ServerIncidentNotifier />)
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => t.message.includes('Hightail Server'))).toBe(true)
    })

    // Im localStorage unter dem kontobezogenen Schlüssel hinterlegt
    const stored = JSON.parse(localStorage.getItem('msm_alerted_incidents_user_5') || '[]')
    expect(stored).toContain('inc-uuid-1')

    // Toast-Store leeren
    useToastStore.getState().clearAll()
    unmount()

    // Erneuter Mount (z.B. Tab-Neuladen oder neuer App-Start): kein erneuter Toast!
    render(<ServerIncidentNotifier />)
    await new Promise((r) => setTimeout(r, 50))
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('isoliert gemeldete Vorfälle pro Benutzerkonto (AGENTS.md Regel 50)', async () => {
    // Vorfall bereits für User 5 als gesehen hinterlegt
    localStorage.setItem('msm_alerted_incidents_user_5', JSON.stringify(['inc-uuid-common']))

    // Nun meldet sich User 9 an
    useAuthStore.setState({
      isAuthenticated: true,
      user: { id: 9, username: 'bob', device_notifications: true } as never,
    })

    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === '/system/incident-alerts') {
        return [
          {
            id: 102,
            uuid: 'inc-uuid-common',
            server_id: 2,
            server_name: 'DayZ Server',
            title: 'CrashLoop',
            description: 'Abgestürzt',
            type: 'CrashLoop',
            severity: 'critical',
            timestamp: new Date().toISOString(),
          },
        ]
      }
      return []
    })

    render(<ServerIncidentNotifier />)
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => t.message.includes('DayZ Server'))).toBe(true)
    })

    // User 9 speichert es in seinem eigenen Schlüssel
    const stored9 = JSON.parse(localStorage.getItem('msm_alerted_incidents_user_9') || '[]')
    expect(stored9).toContain('inc-uuid-common')
  })
})

