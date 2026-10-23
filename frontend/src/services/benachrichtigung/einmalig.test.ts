import { beforeEach, describe, expect, it } from 'vitest'

import { beanspruche, vergissEinmalig } from './einmalig'

describe('einmalig', () => {
  beforeEach(() => {
    localStorage.clear()
    vergissEinmalig()
  })

  it('lässt den ersten Aufruf melden und den zweiten nicht', () => {
    expect(beanspruche('chat:mb-1:42', 5000, 1_000)).toBe(true)
    // Das zweite Fenster hängt am selben Ereignisstrom und fragt im selben
    // Moment. Bis 22.10.2026 spielte es den Ton ein zweites Mal.
    expect(beanspruche('chat:mb-1:42', 5000, 1_000)).toBe(false)
  })

  it('gibt denselben Schlüssel nach Ablauf der Frist wieder frei', () => {
    expect(beanspruche('vorfall:abc', 5000, 1_000)).toBe(true)
    expect(beanspruche('vorfall:abc', 5000, 1_001)).toBe(false)
    // Dasselbe Ereignis Minuten später ist ein neues Ereignis.
    expect(beanspruche('vorfall:abc', 5000, 6_001)).toBe(true)
  })

  it('trennt verschiedene Schlüssel', () => {
    expect(beanspruche('chat:mb-1:42', 5000, 1_000)).toBe(true)
    expect(beanspruche('chat:mb-1:43', 5000, 1_000)).toBe(true)
    expect(beanspruche('vorfall:abc', 5000, 1_000)).toBe(true)
  })

  it('verfällt nicht, wenn die Uhr des Fensters zurückspringt', () => {
    // Ein Zeitstempel in der Vergangenheit darf einen laufenden Vorbehalt
    // nicht aufheben: sonst meldete ein schief gehendes Fenster doppelt.
    expect(beanspruche('chat:mb-1:42', 5000, 10_000)).toBe(true)
    expect(beanspruche('chat:mb-1:42', 5000, 9_000)).toBe(false)
  })

  it('meldet ohne Ablage lieber einmal zu viel als gar nicht', () => {
    const kaputt = {
      getItem() {
        throw new Error('gesperrt')
      },
      setItem() {
        throw new Error('gesperrt')
      },
      removeItem() {
        throw new Error('gesperrt')
      },
    }
    const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
    Object.defineProperty(globalThis, 'localStorage', { value: kaputt, configurable: true })
    try {
      expect(beanspruche('chat:mb-1:42')).toBe(true)
      expect(beanspruche('chat:mb-1:42')).toBe(true)
    } finally {
      if (original) Object.defineProperty(globalThis, 'localStorage', original)
    }
  })

  it('räumt abgelaufene Einträge weg, statt sie zu stapeln', () => {
    for (let i = 0; i < 120; i += 1) beanspruche(`chat:alt:${i}`, 1000, 1_000)
    beanspruche('chat:neu:1', 5000, 100_000)
    const roh = JSON.parse(localStorage.getItem('msm:meldung:einmalig') || '{}')
    expect(Object.keys(roh)).toEqual(['chat:neu:1'])
  })
})
