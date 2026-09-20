/**
 * @vitest-environment node
 *
 * Die gemeinsame Mechanik der automatischen Sperre.
 *
 * Der wichtigste Test hier ist der unscheinbarste: die Schlüsselnamen des
 * Tresors. Sie hießen vor der Zusammenlegung `mss:vault_autolock_minutes` und
 * `mss:vault_lock_on_blur`, und sie müssen weiter so heißen. Ein umbenannter
 * Schlüssel sieht aus wie eine Einstellung auf Standard und ist in Wahrheit
 * eine stillschweigend zurückgesetzte Sicherheitseinstellung.
 */

import { beforeEach, describe, expect, it } from 'vitest'

import {
  fristAbgelaufen,
  liesFensterwechsel,
  liesSperrfrist,
  schreibeFensterwechsel,
  schreibeSperrfrist,
} from './autoSperre'

let ablage: Map<string, string>

beforeEach(() => {
  ablage = new Map()
  ;(globalThis as any).localStorage = {
    getItem: (k: string) => ablage.get(k) ?? null,
    setItem: (k: string, v: string) => ablage.set(k, v),
    removeItem: (k: string) => ablage.delete(k),
    clear: () => ablage.clear(),
  }
})

describe('autoSperre', () => {
  describe('Schlüsselnamen', () => {
    it('schreibt die Tresor-Einstellungen dorthin, wo sie schon immer lagen', () => {
      schreibeSperrfrist('mss:vault', 30)
      schreibeFensterwechsel('mss:vault', true)

      expect(ablage.get('mss:vault_autolock_minutes')).toBe('30')
      expect(ablage.get('mss:vault_lock_on_blur')).toBe('true')
    })

    it('liest eine vor der Zusammenlegung geschriebene Einstellung', () => {
      ablage.set('mss:vault_autolock_minutes', '10')
      ablage.set('mss:vault_lock_on_blur', 'true')

      expect(liesSperrfrist('mss:vault', 15)).toBe(10)
      expect(liesFensterwechsel('mss:vault')).toBe(true)
    })

    it('hält Tresor und Messenger auseinander', () => {
      schreibeSperrfrist('mss:vault', 30)
      schreibeSperrfrist('mss:messenger', 5)

      expect(liesSperrfrist('mss:vault', 15)).toBe(30)
      expect(liesSperrfrist('mss:messenger', 15)).toBe(5)
    })
  })

  describe('Lesen ohne gespeicherten Wert', () => {
    it('nimmt den Standard', () => {
      expect(liesSperrfrist('mss:messenger', 15)).toBe(15)
      expect(liesFensterwechsel('mss:messenger')).toBe(false)
    })

    it('nimmt den Standard auch bei Unsinn in der Ablage', () => {
      ablage.set('mss:messenger_autolock_minutes', 'bald')
      expect(liesSperrfrist('mss:messenger', 15)).toBe(15)

      ablage.set('mss:messenger_autolock_minutes', '-5')
      expect(liesSperrfrist('mss:messenger', 15)).toBe(15)
    })
  })

  describe('fristAbgelaufen', () => {
    it('sperrt nie bei null Minuten', () => {
      // `0` heißt „nie" und nicht „sofort". Der Tresor beschriftete diese Zahl
      // jahrelang mit „Sofort beim Verlassen" und tat das Gegenteil.
      expect(fristAbgelaufen(0, 0)).toBe(false)
      expect(fristAbgelaufen(Date.now() - 10 * 60_000, 0)).toBe(false)
    })

    it('sperrt erst, wenn die Frist wirklich um ist', () => {
      const jetzt = Date.now()
      expect(fristAbgelaufen(jetzt, 5)).toBe(false)
      expect(fristAbgelaufen(jetzt - 4 * 60_000, 5)).toBe(false)
      expect(fristAbgelaufen(jetzt - 5 * 60_000 - 1, 5)).toBe(true)
    })
  })
})
