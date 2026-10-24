import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const hub = vi.hoisted(() => ({
  melde: vi.fn(async () => ({ plan: {}, system: null, doppelt: false })),
}))

vi.mock('./hub', async (importOriginal) => {
  const echt = await importOriginal<typeof import('./hub')>()
  return { ...echt, melde: hub.melde }
})

import { useAuthStore } from '@/stores/authStore'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'

import { meldeChat } from './chatMeldung'

const MID = 'mb-chat'
const ICH = 1
const PARTNER = 2

function t(schluessel: string, werte?: Record<string, unknown>): string {
  return werte?.name ? `${schluessel}:${String(werte.name)}` : schluessel
}

function eingang(zusatz: Partial<Parameters<typeof meldeChat>[0]> = {}) {
  return {
    mailbox: MID,
    senderId: PARTNER,
    clientUuid: `u-${++zaehler}`,
    istGruppe: false,
    t,
    ...zusatz,
  }
}

let ereignisse: string[] = []
let zaehler = 0

beforeEach(() => {
  window.localStorage.clear()
  vi.clearAllMocks()
  ereignisse = []
  const merken = (e: Event) => ereignisse.push(e.type)
  window.addEventListener('msm:mailbox-neu', merken)
  window.addEventListener('msm:messages-updated', merken)
  useAuthStore.setState({
    isAuthenticated: true,
    user: { id: ICH, username: 'alpha', device_notifications: true } as never,
  })
  useMessengerNotificationStore.setState({
    unreadCounts: {},
    totalUnreadCount: 0,
    mutedChats: {},
    blockedUserIds: [],
    activeMailboxId: null,
    mailboxDirectory: { [MID]: { name: 'Beta', userId: PARTNER } },
  })
  vi.spyOn(document, 'hasFocus').mockReturnValue(true)
})

afterEach(() => {
  useAuthStore.setState({ isAuthenticated: false, user: null })
  vi.restoreAllMocks()
})

describe('meldeChat', () => {
  it('zählt und meldet eine Nachricht des Gegenübers', async () => {
    meldeChat(eingang())

    expect(useMessengerNotificationStore.getState().unreadCounts[MID]).toBe(1)
    expect(hub.melde).toHaveBeenCalledTimes(1)
    const [, text] = hub.melde.mock.calls[0] as unknown as [unknown, { titel: string }]
    expect(text.titel).toBe('notifications.messageTitle:Beta')
    expect(ereignisse).toContain('msm:mailbox-neu')
  })

  it('zählt dieselbe Nachricht aus zwei Wegen nur einmal', () => {
    const doppelt = eingang()
    meldeChat(doppelt)
    meldeChat(doppelt)

    expect(useMessengerNotificationStore.getState().unreadCounts[MID]).toBe(1)
    expect(hub.melde).toHaveBeenCalledTimes(1)
  })

  it('meldet die eigene Nachricht nicht', () => {
    meldeChat(eingang({ senderId: ICH }))

    expect(useMessengerNotificationStore.getState().unreadCounts[MID]).toBeUndefined()
    expect(hub.melde).not.toHaveBeenCalled()
  })

  it('behandelt eine Lesequittung des eigenen anderen Geräts als gelesen', () => {
    useMessengerNotificationStore.setState({
      unreadCounts: { [MID]: 3 },
      totalUnreadCount: 3,
    })

    meldeChat(eingang({ senderId: ICH, controlType: 'read_receipt', isControl: true }))

    expect(useMessengerNotificationStore.getState().unreadCounts[MID]).toBe(0)
    expect(hub.melde).not.toHaveBeenCalled()
  })

  it('lässt Steuerumschläge ohne Meldung, kündigt aber eine Aktualisierung an', () => {
    meldeChat(eingang({ controlType: 'delivery_receipt', isControl: true }))

    expect(useMessengerNotificationStore.getState().unreadCounts[MID]).toBeUndefined()
    expect(hub.melde).not.toHaveBeenCalled()
    expect(ereignisse).toContain('msm:messages-updated')
  })

  it('schweigt in einem stummen Chat, lässt die Erwähnungswache aber sehen', () => {
    useMessengerNotificationStore.setState({
      mutedChats: { [MID]: 0 },
    })

    meldeChat(eingang())

    expect(useMessengerNotificationStore.getState().unreadCounts[MID]).toBeUndefined()
    expect(hub.melde).not.toHaveBeenCalled()
    expect(ereignisse).toContain('msm:mailbox-neu')
  })

  it('schweigt im offenen, fokussierten Chat und zählt nicht', () => {
    useMessengerNotificationStore.setState({ activeMailboxId: MID })

    meldeChat(eingang())

    expect(useMessengerNotificationStore.getState().unreadCounts[MID]).toBeUndefined()
    expect(hub.melde).not.toHaveBeenCalled()
  })

  it('schweigt bei einem blockierten Kontakt', () => {
    useMessengerNotificationStore.setState({ blockedUserIds: [PARTNER] })

    meldeChat(eingang())

    expect(useMessengerNotificationStore.getState().unreadCounts[MID]).toBeUndefined()
    expect(hub.melde).not.toHaveBeenCalled()
  })
})
