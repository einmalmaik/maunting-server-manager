/**
 * Der Deep-Link in ein Gespräch — und der Fall, in dem er bisher nie öffnete.
 *
 * Die Prüfung einer unbekannten Kennung merkt sich ihr Ziel, damit sie nicht bei
 * jedem Render erneut fragt. Wurde sie aber abgebrochen (die Kontaktliste hat
 * sich geändert, während `getProfile` lief), blieb der Merker stehen: der
 * nächste Lauf stieg sofort aus, und das Ergebnis des abgebrochenen war
 * verworfen. Der Chat öffnete nie.
 */

import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ChatContact } from '@/components/social/sidebar/ConversationListItem'

vi.mock('@/api/social', () => ({ getProfile: vi.fn() }))
vi.mock('@/services/gespraechsListe', () => ({
  ladeGespraeche: vi.fn(async () => new Map()),
}))

const { getProfile } = await import('@/api/social')
const { useKontaktAusUrl } = await import('./useKontaktAusUrl')

const ZIEL: ChatContact = {
  listKey: 'q-42',
  id: 42,
  userId: 42,
  username: 'Ziel',
  avatarUrl: null,
  status: 'online',
  deviceType: null,
  activityLabel: null,
  isFriend: false,
  teamName: null,
  isPublicUser: true,
}

const ANDERE: ChatContact = { ...ZIEL, listKey: 'q-7', id: 7, userId: 7, username: 'Andere' }

/** Ein Setter für alle Läufe: ein neuer je `optionen()` fände die Aufrufe nicht. */
function optionen(
  setActiveContact: (wert: ChatContact | null) => void,
  extra: Record<string, unknown> = {},
) {
  return {
    queryUserId: '42',
    queryGroupId: null,
    kontakte: [] as ChatContact[],
    gruppen: [],
    kontakteGeladen: true,
    activeContact: null,
    activeGroup: null,
    setActiveContact,
    setActiveGroup: vi.fn(),
    searchParams: new URLSearchParams('userId=42'),
    setSearchParams: vi.fn(),
    t: (schluessel: string) => schluessel,
    ...extra,
  }
}

beforeEach(() => {
  vi.mocked(getProfile).mockReset()
})

describe('useKontaktAusUrl', () => {
  it('öffnet den Chat auch, wenn der erste Lauf abgebrochen wurde', async () => {
    // Der erste Abruf hängt; währenddessen ändert sich die Kontaktliste (eine
    // Anwesenheit), der Effekt räumt auf und läuft neu an.
    let ersteAufloesen!: (wert: unknown) => void
    const erste = new Promise((aufloesen) => {
      ersteAufloesen = aufloesen
    })
    vi.mocked(getProfile)
      .mockImplementationOnce(() => erste as never)
      .mockResolvedValueOnce({
        username: 'Ziel',
        privacy: 'public',
        is_friend: false,
        presence: null,
        avatar_url: null,
      } as never)

    const setzeKontakt = vi.fn()
    const start = optionen(setzeKontakt)
    const { rerender } = renderHook(
      (props: ReturnType<typeof optionen>) => useKontaktAusUrl(props),
      { initialProps: start },
    )

    rerender(optionen(setzeKontakt, { kontakte: [ANDERE] }))
    ersteAufloesen({ username: 'Ziel', privacy: 'public', is_friend: false, presence: null })

    await waitFor(() => expect(setzeKontakt).toHaveBeenCalled())
    expect(setzeKontakt.mock.calls.at(-1)?.[0]).toMatchObject({
      userId: 42,
      username: 'Ziel',
      isPublicUser: true,
    })
    expect(vi.mocked(getProfile).mock.calls.length).toBe(2)
  })

  it('prüft dieselbe Kennung nach einem abgeschlossenen Lauf nicht erneut', async () => {
    vi.mocked(getProfile).mockResolvedValue({
      username: 'Ziel',
      privacy: 'public',
      is_friend: false,
      presence: null,
      avatar_url: null,
    } as never)

    const setzeKontakt = vi.fn()
    const start = optionen(setzeKontakt)
    const { rerender } = renderHook(
      (props: ReturnType<typeof optionen>) => useKontaktAusUrl(props),
      { initialProps: start },
    )
    await waitFor(() => expect(setzeKontakt).toHaveBeenCalled())

    rerender(optionen(setzeKontakt, { kontakte: [ANDERE] }))
    await new Promise((r) => setTimeout(r, 0))
    expect(vi.mocked(getProfile).mock.calls.length).toBe(1)
  })
})
