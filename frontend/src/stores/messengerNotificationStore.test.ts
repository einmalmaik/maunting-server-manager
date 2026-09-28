/**
 * Die Messenger-Hinweise gehören dem Konto, das sie angelegt hat.
 *
 * Bis 29.09.2026 lagen Mailbox-Verzeichnis (Namen der Kontakte), gesperrte
 * Profile und Stummschaltungen unter festen Schlüsseln im localStorage und im
 * Speicher des Tabs. `clearSession` räumte sie nicht ab, und das nächste Konto
 * im selben Browser sah die Namen des vorigen.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '@/api/client'
import { KONTAKTE_CACHE_KEY, MESSENGER_KEYS, leereOfflineAblage } from '@/lib/offlineAblage'
import { useAuthStore } from '@/stores/authStore'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'
import type { User } from '@/types'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, api: vi.fn(), clearCsrfTokenMemory: vi.fn() }
})

const KONTO_A = { id: 1, username: 'anna' } as User
const KONTO_B = { id: 2, username: 'bert' } as User

function rohAblage(): string {
  return [...Object.values(MESSENGER_KEYS), KONTAKTE_CACHE_KEY]
    .map((schluessel) => localStorage.getItem(schluessel) ?? '')
    .join('')
}

async function meldeAn(user: User): Promise<void> {
  await useAuthStore.getState().finishLogin(user)
}

describe('Messenger-Hinweise je Konto', () => {
  beforeEach(() => {
    useAuthStore.getState().clearSession()
    leereOfflineAblage()
    localStorage.clear()
    vi.mocked(client.api).mockReset()
  })

  it('zeigt dem nächsten Konto nach abgelaufener Sitzung keine Namen des vorigen', async () => {
    await meldeAn(KONTO_A)
    vi.mocked(client.api).mockResolvedValueOnce([{ user_id: 9, username: 'Gesperrt-von-Anna' }])
    await useMessengerNotificationStore.getState().syncBlockedFromBackend()
    useMessengerNotificationStore.getState().registerMailbox('mb-anna', { name: 'Freund-von-Anna' })
    useMessengerNotificationStore.getState().muteChat('mb-anna')
    localStorage.setItem(KONTAKTE_CACHE_KEY, JSON.stringify({ friends: [{ username: 'Freund-von-Anna' }] }))
    expect(rohAblage()).toContain('Freund-von-Anna')

    // Sitzung läuft ab (kein Abmelden), ein anderes Konto meldet sich an.
    useAuthStore.getState().clearSession()
    await meldeAn(KONTO_B)

    const stand = useMessengerNotificationStore.getState()
    expect(stand.mailboxDirectory).toEqual({})
    expect(stand.blockedProfiles).toEqual({})
    expect(stand.blockedUserIds).toEqual([])
    expect(stand.isMuted('mb-anna')).toBe(false)
    expect(rohAblage()).not.toContain('Anna')
  })

  it('leert den Speicher, wenn die Sitzung endet, und gibt demselben Konto seinen Stand zurück', async () => {
    await meldeAn(KONTO_A)
    useMessengerNotificationStore.getState().muteChat('mb-anna')
    useMessengerNotificationStore.getState().registerMailbox('mb-anna', { name: 'Freund-von-Anna' })

    useAuthStore.getState().clearSession()
    expect(useMessengerNotificationStore.getState().mailboxDirectory).toEqual({})
    expect(useMessengerNotificationStore.getState().mutedChats).toEqual({})

    await meldeAn(KONTO_A)
    expect(useMessengerNotificationStore.getState().isMuted('mb-anna')).toBe(true)
    expect(useMessengerNotificationStore.getState().mailboxDirectory['mb-anna']?.name).toBe('Freund-von-Anna')
  })

  it('räumt die Ablage beim bewussten Abmelden weg', async () => {
    await meldeAn(KONTO_A)
    useMessengerNotificationStore.getState().registerMailbox('mb-anna', { name: 'Freund-von-Anna' })
    localStorage.setItem(KONTAKTE_CACHE_KEY, JSON.stringify({ friends: [{ username: 'Freund-von-Anna' }] }))

    vi.mocked(client.api).mockResolvedValue(undefined)
    await useAuthStore.getState().logout()

    expect(rohAblage()).toBe('')
  })
})
