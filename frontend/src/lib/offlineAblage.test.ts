/**
 * Die Offline-Ablage gehört dem Konto, das sie angelegt hat.
 *
 * Bis 27.09.2026 lag sie unter festen Schlüsseln im localStorage. Ein zweites
 * Konto im selben Browser sah die Termine des ersten im Klartext, und dessen
 * ungesendete Änderungen gingen mit der neuen Anmeldung hinaus.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '@/api/client'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import {
  STORAGE_KEYS,
  clearMemoryStoreForTesting,
  enqueueMutation,
  getOfflineCalendarEvents,
  getOutbox,
  replayOutbox,
  setOfflineCalendarEvents,
} from '@/lib/offlineSync'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
  apiStream: vi.fn(),
}))

const TERMIN = {
  id: 1,
  event_id: 'evt-a',
  title: 'Arzttermin von Konto A',
  start: '2026-09-27T10:00:00Z',
  end: '2026-09-27T11:00:00Z',
  event_type: 'team',
  team_id: 3,
} as never

function rohAblage(): string {
  return Object.values(STORAGE_KEYS)
    .map((schluessel) => localStorage.getItem(schluessel) ?? '')
    .join('')
}

describe('Offline-Ablage je Konto', () => {
  beforeEach(() => {
    setzeAngemeldetesKonto(null)
    clearMemoryStoreForTesting()
    localStorage.clear()
    vi.mocked(client.api).mockReset()
  })

  afterEach(() => {
    setzeAngemeldetesKonto(null)
  })

  it('zeigt einem anderen Konto nichts vom vorigen und löscht es', () => {
    setzeAngemeldetesKonto(1)
    setOfflineCalendarEvents([TERMIN])
    expect(getOfflineCalendarEvents()).toHaveLength(1)

    // Sitzung endet ohne Abmelden, ein anderes Konto meldet sich an.
    setzeAngemeldetesKonto(2)

    expect(getOfflineCalendarEvents()).toEqual([])
    expect(rohAblage()).not.toContain('Arzttermin')
  })

  it('schickt die Warteschlange des vorigen Kontos nicht mit der neuen Anmeldung', async () => {
    setzeAngemeldetesKonto(1)
    enqueueMutation({ entity: 'note', action: 'delete', entityId: 'notiz-von-a' })

    setzeAngemeldetesKonto(2)
    await replayOutbox()

    expect(client.api).not.toHaveBeenCalled()
    expect(getOutbox()).toEqual([])
  })

  it('hört auf, wenn die Anmeldung während des Abgleichs wechselt', async () => {
    setzeAngemeldetesKonto(1)
    enqueueMutation({ entity: 'note', action: 'delete', entityId: 'erste' })
    enqueueMutation({ entity: 'note', action: 'delete', entityId: 'zweite' })
    vi.mocked(client.api).mockImplementation(async () => {
      setzeAngemeldetesKonto(2)
      return {}
    })

    await replayOutbox()

    expect(client.api).toHaveBeenCalledTimes(1)
    expect(rohAblage()).not.toContain('zweite')
  })

  it('gibt ohne Anmeldung nichts heraus und legt nichts ab', () => {
    setOfflineCalendarEvents([TERMIN])

    expect(getOfflineCalendarEvents()).toEqual([])
    expect(rohAblage()).toBe('')
  })

  it('behält die Ablage für dasselbe Konto nach einer abgelaufenen Sitzung', () => {
    setzeAngemeldetesKonto(1)
    setOfflineCalendarEvents([TERMIN])

    setzeAngemeldetesKonto(null)
    setzeAngemeldetesKonto(1)

    expect(getOfflineCalendarEvents()).toHaveLength(1)
  })
})
