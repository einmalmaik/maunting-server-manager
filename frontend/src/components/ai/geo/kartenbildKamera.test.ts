/**
 * Die Kamera ohne MapTiler. Die Zusagen: Jeder Befehl ergibt einen anderen
 * Ausschnitt, der in den Grenzen des Panels liegt (0,01° bis 60° × 40°,
 * innerhalb von Web Mercator), und die Bildform passt zur Fläche.
 */
import { describe, expect, it } from 'vitest'

import type { AiRegionalAnalysis } from '@/api/ai'
import {
  ZOOMSCHRITT,
  ausschnittUm,
  bildformFuer,
  kameraAusschnitt,
  startAusschnitt,
  type Ausschnitt,
} from './kartenbildKamera'

const BERLIN: Ausschnitt = [13.0883, 52.3382, 13.7611, 52.6755]
const ORT: AiRegionalAnalysis['coordinates'] = { latitude: 52.52, longitude: 13.405, bbox: BERLIN }

const spannen = ([west, sued, ost, nord]: Ausschnitt) => [ost - west, nord - sued]
const mitte = ([west, sued, ost, nord]: Ausschnitt) => [(west + ost) / 2, (sued + nord) / 2]

describe('kartenbildKamera', () => {
  it('wählt die Bildform, die die Fläche am wenigsten beschneidet', () => {
    expect(bildformFuer(1600, 900)).toBe('landscape')
    expect(bildformFuer(600, 840)).toBe('portrait')
    expect(bildformFuer(800, 700)).toBe('square')
    // Noch nicht vermessen: das Bild des Reiters.
    expect(bildformFuer(0, 0)).toBe('landscape')
  })

  it('beginnt bei der Box des Ortes, zwei Schritte näher oder so weit es geht', () => {
    expect(startAusschnitt(BERLIN, 'focus', ORT)).toEqual(BERLIN)
    expect(startAusschnitt(BERLIN)).toEqual(BERLIN)

    const nah = startAusschnitt(BERLIN, 'detail', ORT)
    expect(spannen(nah)[0]).toBeCloseTo(spannen(BERLIN)[0] * ZOOMSCHRITT * ZOOMSCHRITT, 4)
    expect(mitte(nah)[0]).toBeCloseTo(13.405, 4)
    expect(mitte(nah)[1]).toBeCloseTo(52.52, 4)

    const weit = startAusschnitt(BERLIN, 'overview', ORT)
    expect(spannen(weit)[0]).toBeCloseTo(60, 6)
    expect(spannen(weit)[1]).toBeCloseTo(40, 6)
    expect(mitte(weit)[0]).toBeCloseTo(13.405, 4)
  })

  it('zoomt um den Ort hinein und heraus', () => {
    const hinein = kameraAusschnitt(BERLIN, BERLIN, { mode: 'focus', action: 'zoom_in', command_id: 'a' }, ORT)
    expect(spannen(hinein)[0]).toBeCloseTo(spannen(BERLIN)[0] * ZOOMSCHRITT, 4)
    expect(spannen(hinein)[1]).toBeCloseTo(spannen(BERLIN)[1] * ZOOMSCHRITT, 4)
    expect(mitte(hinein)[0]).toBeCloseTo(13.405, 4)
    expect(mitte(hinein)[1]).toBeCloseTo(52.52, 4)

    const heraus = kameraAusschnitt(hinein, BERLIN, { mode: 'focus', action: 'zoom_out', command_id: 'b' }, ORT)
    expect(spannen(heraus)[0]).toBeCloseTo(spannen(BERLIN)[0], 3)
    expect(mitte(heraus)[0]).toBeCloseTo(13.405, 4)
  })

  it('verliert den Ort nicht, wenn seine Box weit neben ihm liegt', () => {
    // Nominatim, 23.09.2026: Hamburgs Box reicht bis ins Wattenmeer vor
    // Neuwerk, ihre Mitte liegt gut 50 km nordwestlich der Stadt.
    const HAMBURG: Ausschnitt = [8.1045, 53.3951, 10.3253, 54.0277]
    const hamburg: AiRegionalAnalysis['coordinates'] = { latitude: 53.5502, longitude: 10.0013, bbox: HAMBURG }
    const zeigt = ([west, sued, ost, nord]: Ausschnitt) =>
      west <= hamburg.longitude && hamburg.longitude <= ost && sued <= hamburg.latitude && hamburg.latitude <= nord

    // Der Start bleibt die Box, wie das Bild im Reiter.
    expect(startAusschnitt(HAMBURG, 'focus', hamburg)).toEqual(HAMBURG)
    expect(zeigt(startAusschnitt(HAMBURG, 'detail', hamburg))).toBe(true)

    let ausschnitt = HAMBURG
    for (let schritt = 0; schritt < 4; schritt += 1) {
      ausschnitt = kameraAusschnitt(ausschnitt, HAMBURG, { mode: 'focus', action: 'zoom_in', command_id: `h${schritt}` }, hamburg)
      expect(zeigt(ausschnitt)).toBe(true)
    }
    expect(mitte(ausschnitt)[0]).toBeCloseTo(10.0013, 4)
  })

  it('hält die Grenzen des Panels, statt eine Anfrage zu bauen, die es abweist', () => {
    let ausschnitt = BERLIN
    for (let schritt = 0; schritt < 12; schritt += 1) {
      ausschnitt = kameraAusschnitt(ausschnitt, BERLIN, { mode: 'focus', action: 'zoom_in', command_id: `i${schritt}` }, ORT)
    }
    expect(spannen(ausschnitt)[0]).toBeCloseTo(0.01, 6)
    expect(spannen(ausschnitt)[1]).toBeCloseTo(0.01, 6)

    for (let schritt = 0; schritt < 12; schritt += 1) {
      ausschnitt = kameraAusschnitt(ausschnitt, BERLIN, { mode: 'focus', action: 'zoom_out', command_id: `o${schritt}` }, ORT)
    }
    expect(spannen(ausschnitt)[0]).toBeCloseTo(60, 6)
    expect(spannen(ausschnitt)[1]).toBeCloseTo(40, 6)

    // Am Rand verschoben statt abgeschnitten: Spitzbergen bleibt 40° hoch.
    const norden = ausschnittUm(15.6, 78.2, 60, 40)
    expect(norden[3]).toBe(85)
    expect(norden[3] - norden[1]).toBeCloseTo(40, 6)
    const datumsgrenze = ausschnittUm(179.5, 0, 10, 10)
    expect(datumsgrenze[2]).toBe(180)
    expect(datumsgrenze[2] - datumsgrenze[0]).toBeCloseTo(10, 6)
  })

  it('fährt zu einer Sehenswürdigkeit und lässt Umgebung stehen', () => {
    const tor: AiRegionalAnalysis['coordinates'] = {
      latitude: 52.5163,
      longitude: 13.3777,
      bbox: [13.3774, 52.5161, 13.378, 52.5165],
    }
    const dort = kameraAusschnitt(BERLIN, BERLIN, { mode: 'detail', action: 'focus_location', command_id: 'f' }, tor)
    expect(mitte(dort)[0]).toBeCloseTo(13.3777, 4)
    expect(mitte(dort)[1]).toBeCloseTo(52.5163, 4)
    expect(spannen(dort)[0]).toBeCloseTo(0.02, 6)

    // Was selbst größer ist, behält seine Box.
    const insel: AiRegionalAnalysis['coordinates'] = {
      latitude: 52.52,
      longitude: 13.4,
      bbox: [13.38, 52.51, 13.42, 52.53],
    }
    expect(spannen(kameraAusschnitt(BERLIN, BERLIN, { mode: 'detail', action: 'focus_location', command_id: 'g' }, insel))[0])
      .toBeCloseTo(0.04, 6)
    expect(kameraAusschnitt(BERLIN, BERLIN, { mode: 'detail', action: 'focus_location', command_id: 'h' }, undefined)).toEqual(BERLIN)
  })

  it('beginnt bei einer neuen Analyse ohne Aktion wieder bei ihrem Start', () => {
    const nah = kameraAusschnitt(BERLIN, BERLIN, { mode: 'focus', action: 'zoom_in', command_id: 'a' }, ORT)
    expect(kameraAusschnitt(nah, BERLIN, { mode: 'focus', command_id: 'neu' }, ORT)).toEqual(BERLIN)
    expect(kameraAusschnitt(nah, BERLIN, { mode: 'focus', action: 'overview', command_id: 'u' }, ORT))
      .toEqual(startAusschnitt(BERLIN, 'overview', ORT))
  })
})
