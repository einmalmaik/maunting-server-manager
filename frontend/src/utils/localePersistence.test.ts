/**
 * Die Sprachwahl ist eine Einwilligungsfrage, deshalb wird sie geprüft.
 *
 * Zwei Zusagen stehen hier: Ohne Einwilligung liegt nichts im Speicher, und ein
 * fehlender oder gesperrter Speicher führt nicht zum Absturz. Die zweite fehlte,
 * und weil `i18n.ts` diese Funktionen beim Laden des Moduls aufruft, nahm der
 * Fehler jeden Import von `@/i18n` mit.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  clearPersistedLocale,
  getPersistedLocale,
  isLocalePersistenceAllowed,
  setPersistedLocale,
} from './localePersistence'

function einwilligung(optional: boolean) {
  localStorage.setItem('cookie_consent', JSON.stringify({ optional }))
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('Einwilligung', () => {
  it('erlaubt nichts, solange keine vorliegt', () => {
    expect(isLocalePersistenceAllowed()).toBe(false)
    expect(getPersistedLocale()).toBeNull()
  })

  it('erlaubt nichts bei abgelehnten optionalen Cookies', () => {
    einwilligung(false)
    expect(isLocalePersistenceAllowed()).toBe(false)
  })

  it('erlaubt nichts, wenn der Eintrag unlesbar ist', () => {
    localStorage.setItem('cookie_consent', '{kaputt')
    expect(isLocalePersistenceAllowed()).toBe(false)
  })

  it('erlaubt die Ablage nach Zustimmung', () => {
    einwilligung(true)
    setPersistedLocale('de')
    expect(getPersistedLocale()).toBe('de')
  })

  it('räumt eine alte Ablage weg, wenn die Zustimmung fehlt', () => {
    einwilligung(true)
    setPersistedLocale('de')

    localStorage.setItem('cookie_consent', JSON.stringify({ optional: false }))
    setPersistedLocale('en')

    expect(localStorage.getItem('i18nextLng')).toBeNull()
  })
})

describe('Ohne nutzbaren Speicher', () => {
  /** Ein Browser mit gesperrten Cookies wirft bereits beim Zugriff. */
  function speicherSperren() {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('gesperrt', 'SecurityError')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('gesperrt', 'SecurityError')
    })
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new DOMException('gesperrt', 'SecurityError')
    })
  }

  it('verweigert statt zu werfen', () => {
    speicherSperren()
    expect(() => isLocalePersistenceAllowed()).not.toThrow()
    expect(isLocalePersistenceAllowed()).toBe(false)
  })

  it('gibt keine Sprache zurück', () => {
    speicherSperren()
    expect(getPersistedLocale()).toBeNull()
  })

  it('lässt Schreiben und Löschen ins Leere laufen', () => {
    speicherSperren()
    expect(() => setPersistedLocale('de')).not.toThrow()
    expect(() => clearPersistedLocale()).not.toThrow()
  })
})
