/**
 * In der Android-App sollte ein eigener Dienst Termine melden, der nie einen
 * gültigen Zugang hatte. Bis 03.10.2026 kamen Erinnerungen dort nur bei offener
 * App. Jetzt plant die offene App sie bei Android ein und meldet sie nicht
 * selbst, sonst käme jede zweimal.
 */
import { act, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const STUNDE = 3_600_000
const termine = vi.hoisted(() => ({ liste: [] as unknown[] }))

vi.mock('@/api/client', () => ({
  api: vi.fn(async (pfad: string) =>
    pfad === '/calendar/due-reminders'
      ? [{ event_id: 'e1', title: 'Zahnarzt', start: 'x', location: '', time_hint: 'in 1 Tag', key: '5_e1__24h' }]
      : [],
  ),
}))
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
vi.mock('@/lib/offlineSync', () => ({
  loadCalendarEventsOfflineFirst: vi.fn(async () => ({ events: termine.liste, isOffline: false })),
}))
vi.mock('@/desktop/tauri', () => ({ erinnerungenPlanen: vi.fn().mockResolvedValue(undefined) }))
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
import { erinnerungenPlanen } from '@/desktop/tauri'
import { sendeGeraeteBenachrichtigung } from '@/lib/benachrichtigung'

const AGENT = navigator.userAgent

describe('ServerIncidentNotifier in der Android-App', () => {
  beforeEach(() => {
    vi.mocked(erinnerungenPlanen).mockClear()
    vi.mocked(sendeGeraeteBenachrichtigung).mockClear()
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    Object.defineProperty(navigator, 'userAgent', { value: 'Mozilla/5.0 (Linux; Android 14) Chrome/124', configurable: true })
    termine.liste = [
      {
        event_id: 'e1',
        title: 'Zahnarzt',
        start: new Date(Date.now() + 30 * STUNDE).toISOString(),
        vorkommen: '',
        istSerie: false,
        schluessel: 'e1:',
      },
    ]
    useAuthStore.setState({
      isAuthenticated: true,
      user: { id: 5, username: 'anna', device_notifications: true } as never,
    })
  })

  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
    Object.defineProperty(navigator, 'userAgent', { value: AGENT, configurable: true })
    useAuthStore.setState({ isAuthenticated: false, user: null })
  })

  it('plant die Termine bei Android ein und meldet sie nicht selbst', async () => {
    render(<ServerIncidentNotifier />)

    await waitFor(() => expect(erinnerungenPlanen).toHaveBeenCalled())
    const plan = vi.mocked(erinnerungenPlanen).mock.calls.at(-1)![0]
    expect(plan.map((e) => e.schluessel)).toEqual(['5_e1__48h', '5_e1__24h'])
    expect(plan[1].text).toContain('Zahnarzt')
    expect(plan[1].oeffentlich).not.toContain('Zahnarzt')
    expect(sendeGeraeteBenachrichtigung).not.toHaveBeenCalled()
  })

  it('nimmt beim Abmelden alle geplanten Erinnerungen vom Gerät', async () => {
    render(<ServerIncidentNotifier />)
    await waitFor(() => expect(erinnerungenPlanen).toHaveBeenCalled())

    await act(async () => {
      useAuthStore.setState({ isAuthenticated: false, user: null })
    })

    await waitFor(() => expect(vi.mocked(erinnerungenPlanen).mock.calls.at(-1)![0]).toEqual([]))
  })
})
