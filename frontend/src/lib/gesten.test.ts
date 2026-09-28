/**
 * @vitest-environment node
 *
 * Die Gestenerkennung.
 *
 * Geprüft wird das, was am Telefon den Unterschied zwischen „funktioniert" und
 * „löst dauernd versehentlich aus" macht: dass Scrollen kein Wischen ist, dass
 * ein zittriger Daumen den Langdruck nicht abbricht, und dass der Weg der
 * angefassten Blase gedeckelt bleibt.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  erkenneWischen,
  hatSichBewegt,
  LANGDRUCK_TOLERANZ_PX,
  rueckmeldung,
  WISCH_HOECHSTWEG_PX,
  WISCH_SCHWELLE_PX,
  wischWeg,
} from './gesten'

describe('erkenneWischen', () => {
  it('erkennt erst jenseits der Schwelle', () => {
    expect(erkenneWischen(WISCH_SCHWELLE_PX - 1, 0)).toBeNull()
    expect(erkenneWischen(WISCH_SCHWELLE_PX + 1, 0)).toBe('rechts')
    expect(erkenneWischen(-(WISCH_SCHWELLE_PX + 1), 0)).toBe('links')
  })

  it('lässt senkrechtes Scrollen in Ruhe', () => {
    // Der Daumen zieht beim Scrollen fast immer leicht schräg. Würde das als
    // Wischen zählen, öffnete jede Listenbewegung eine Antwort.
    expect(erkenneWischen(70, 200)).toBeNull()
    expect(erkenneWischen(70, 30)).toBe('rechts')
  })
})

describe('hatSichBewegt', () => {
  it('verzeiht das Zittern eines ruhig gehaltenen Daumens', () => {
    expect(hatSichBewegt(LANGDRUCK_TOLERANZ_PX - 1, LANGDRUCK_TOLERANZ_PX - 1)).toBe(false)
  })

  it('meldet eine echte Bewegung, auch nur in einer Richtung', () => {
    expect(hatSichBewegt(0, LANGDRUCK_TOLERANZ_PX + 1)).toBe(true)
    expect(hatSichBewegt(-(LANGDRUCK_TOLERANZ_PX + 1), 0)).toBe(true)
  })
})

describe('wischWeg', () => {
  it('folgt dem Finger bis zur Schwelle eins zu eins', () => {
    expect(wischWeg(40)).toBe(40)
    expect(wischWeg(-40)).toBe(-40)
  })

  it('wird jenseits der Schwelle zäh und bleibt gedeckelt', () => {
    const weit = wischWeg(400)
    expect(weit).toBeGreaterThan(WISCH_SCHWELLE_PX)
    expect(weit).toBeLessThanOrEqual(WISCH_HOECHSTWEG_PX)
    // Auch nach links, sonst wandert die Blase beim Archivieren aus dem Bild.
    expect(wischWeg(-400)).toBeGreaterThanOrEqual(-WISCH_HOECHSTWEG_PX)
  })
})

describe('rueckmeldung', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('rüttelt, wo das Gerät es kann', () => {
    const vibrate = vi.fn()
    vi.stubGlobal('navigator', { vibrate })
    rueckmeldung(20)
    expect(vibrate).toHaveBeenCalledWith(20)
  })

  it('bleibt still, wo es das nicht kann, statt zu werfen', () => {
    // iOS kennt `vibrate` nicht, und manche Browser werfen beim Aufruf. Ein
    // ausbleibendes Rütteln darf die Geste nicht scheitern lassen.
    vi.stubGlobal('navigator', {})
    expect(() => rueckmeldung()).not.toThrow()

    vi.stubGlobal('navigator', {
      vibrate: () => {
        throw new Error('verweigert')
      },
    })
    expect(() => rueckmeldung()).not.toThrow()
  })
})
