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
const nachrichtenStore = vi.hoisted(() => ({
  chime: vi.fn(),
  zustand: {
    syncBlockedFromBackend: vi.fn().mockResolvedValue(undefined),
    syncMailboxDirectoryFromBackend: vi.fn().mockResolvedValue(undefined),
    mailboxDirectory: {} as Record<string, { name: string; isGroup?: boolean; userId?: number }>,
    activeMailboxId: null as string | null,
    isMuted: (_mid: string) => false,
    isBlocked: (_userId: number) => false,
    incrementUnread: vi.fn(),
  },
}))

vi.mock('@/stores/messengerNotificationStore', () => ({
  useMessengerNotificationStore: { getState: () => nachrichtenStore.zustand },
  playNotificationChime: nachrichtenStore.chime,
}))

import { api } from '@/api/client'
import { ServerIncidentNotifier } from './ServerIncidentNotifier'
import { sendeGeraeteBenachrichtigung } from '@/lib/benachrichtigung'
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


/**
 * Der Weg einer Chat-Nachricht — über den Hub, nicht mehr über einen eigenen
 * `if`-Zweig in dieser Datei. Was hier geprüft wird, ist genau das, was am
 * 22.10.2026 kaputt war: ein Ton je Nachricht, kein Ton im stummen Chat, keine
 * zweite Meldung für die Kopie desselben Umschlags.
 */
describe('ServerIncidentNotifier — Chat über den Hub', () => {
  const zustand = nachrichtenStore.zustand

  const nachricht = (teile: Record<string, unknown> = {}) => ({
    type: 'e2ee_blind_message',
    blind_mailbox_id: 'mb-anna',
    sender_user_id: 6,
    recipient_id: 5,
    id: 4711,
    client_uuid: 'uuid-4711',
    is_control: false,
    ...teile,
  })

  const schicke = async (teile: Record<string, unknown> = {}) => {
    await act(async () => {
      window.dispatchEvent(new CustomEvent('msm:sync-event', { detail: nachricht(teile) }))
    })
  }

  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    useToastStore.getState().clearAll()
    nachrichtenStore.chime.mockClear()
    vi.mocked(sendeGeraeteBenachrichtigung).mockReset()
    vi.mocked(sendeGeraeteBenachrichtigung).mockResolvedValue({ weg: 'tauri', ok: true })
    zustand.mailboxDirectory = { 'mb-anna': { name: 'owner', isGroup: false, userId: 6 } }
    zustand.activeMailboxId = null
    zustand.isMuted = () => false
    zustand.isBlocked = () => false
    zustand.incrementUnread.mockClear()
    useAuthStore.setState({
      isAuthenticated: true,
      user: { id: 5, username: 'anna', device_notifications: true } as never,
    })
  })

  afterEach(() => {
    useAuthStore.setState({ isAuthenticated: false, user: null })
    localStorage.clear()
  })

  it('klingelt und zählt die Nachricht des Gegenübers', async () => {
    render(<ServerIncidentNotifier />)
    await schicke()
    await waitFor(() => expect(nachrichtenStore.chime).toHaveBeenCalledTimes(1))
    expect(zustand.incrementUnread).toHaveBeenCalledWith('mb-anna')
    expect(sendeGeraeteBenachrichtigung).toHaveBeenCalledTimes(1)
  })

  it('klingelt für dieselbe Nachricht nicht zweimal', async () => {
    // Der Umschlag geht je Gerät einmal raus; beide Kopien tragen dieselbe
    // `client_uuid`. Bis 22.10.2026 waren das zwei Töne.
    render(<ServerIncidentNotifier />)
    await schicke({ id: 4711 })
    await schicke({ id: 4712 })
    await waitFor(() => expect(nachrichtenStore.chime).toHaveBeenCalledTimes(1))
  })

  it('schweigt in einem stummen Chat, meldet die Mailbox aber weiter', async () => {
    // Kein Ton, aber die Erwähnungswache muss von der neuen Post erfahren —
    // sonst erschiene in einer stummen Gruppe nicht einmal das @-Abzeichen.
    zustand.isMuted = () => true
    const gehoert: string[] = []
    const horcher = (e: Event) => gehoert.push(String((e as CustomEvent).detail?.mid))
    window.addEventListener('msm:mailbox-neu', horcher)
    try {
      render(<ServerIncidentNotifier />)
      await schicke()
      expect(nachrichtenStore.chime).not.toHaveBeenCalled()
      expect(sendeGeraeteBenachrichtigung).not.toHaveBeenCalled()
      expect(gehoert).toContain('mb-anna')
    } finally {
      window.removeEventListener('msm:mailbox-neu', horcher)
    }
  })

  it('schweigt im offenen Chat und zählt nicht', async () => {
    zustand.activeMailboxId = 'mb-anna'
    // jsdom hat keinen echten Fokus; ohne diese Vorgabe wäre „offen" hier nie
    // erreicht und der Test prüfte den falschen Zweig.
    const fokus = vi.spyOn(document, 'hasFocus').mockReturnValue(true)
    try {
      render(<ServerIncidentNotifier />)
      await schicke()
      expect(nachrichtenStore.chime).not.toHaveBeenCalled()
      expect(sendeGeraeteBenachrichtigung).not.toHaveBeenCalled()
      expect(zustand.incrementUnread).not.toHaveBeenCalled()
    } finally {
      fokus.mockRestore()
    }
  })

  it('meldet die eigene Nachricht nicht', async () => {
    render(<ServerIncidentNotifier />)
    await schicke({ sender_user_id: 5 })
    expect(nachrichtenStore.chime).not.toHaveBeenCalled()
    expect(sendeGeraeteBenachrichtigung).not.toHaveBeenCalled()
  })
})
