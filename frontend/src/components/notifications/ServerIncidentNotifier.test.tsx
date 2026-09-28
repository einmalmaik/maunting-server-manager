/**
 * Bis 09/2026 verschickte das Backend `achievement_unlocked`, und niemand
 * hörte zu: neue Abzeichen sah nur, wer die Liste selbst öffnete.
 */

import { act, render } from '@testing-library/react'
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
