/**
 * Die Bildeinstellungen müssen dort ankommen, wo gefilmt wird.
 *
 * Für den Ton gibt es dieselbe Prüfung in `audioSettings.test.ts`. Sie ist
 * entstanden, nachdem eine Wahl im Profil stumm nirgends ankam — beim Bild war
 * es lange nicht anders: Anruf und Videonotiz hatten ihre Werte fest im
 * Quelltext stehen.
 */

import { beforeEach, describe, expect, it } from 'vitest'
import {
  VIDEONOTIZ_KANTE,
  getVideoCaptureAufloesung,
  getVideoNoteConstraints,
  getVideoSendeGrenzen,
  getVideoSettings,
  saveVideoSettings,
} from './videoSettings'

beforeEach(() => {
  localStorage.clear()
})

describe('Vorgaben', () => {
  it('steht auf der höchsten Stufe, nicht auf der sparsamsten', () => {
    // Herunterregeln ist die Aufgabe der Leitung, nicht die einer Voreinstellung.
    expect(getVideoSettings()).toEqual({ aufloesung: '1080p', bildrate: 60 })
  })

  it('macht aus Unsinn im Speicher wieder die Vorgabe', () => {
    localStorage.setItem('msm_video_resolution', '4k')
    localStorage.setItem('msm_video_framerate', '144')
    expect(getVideoSettings()).toEqual({ aufloesung: '1080p', bildrate: 60 })
  })
})

describe('Speichern', () => {
  it('überlebt einen Schreib-Lese-Umlauf', () => {
    saveVideoSettings({ aufloesung: '720p', bildrate: 30 })
    expect(getVideoSettings()).toEqual({ aufloesung: '720p', bildrate: 30 })
  })

  it('lässt ein nicht übergebenes Feld in Ruhe', () => {
    saveVideoSettings({ aufloesung: '720p' })
    saveVideoSettings({ bildrate: 30 })
    expect(getVideoSettings().aufloesung).toBe('720p')
  })
})

describe('Kamera im Anruf', () => {
  it('gibt Maße und Bildrate aus der Wahl weiter', () => {
    saveVideoSettings({ aufloesung: '720p', bildrate: 30 })
    expect(getVideoCaptureAufloesung()).toEqual({ width: 1280, height: 720, frameRate: 30 })

    saveVideoSettings({ aufloesung: '1080p', bildrate: 60 })
    expect(getVideoCaptureAufloesung()).toEqual({ width: 1920, height: 1080, frameRate: 60 })
  })

  it('bleibt über der alten festen Vorgabe von 1,7 Mbit/s', () => {
    // Genau die stand vorher als `VideoPresets.h720` im Anruf und war der Grund
    // dafür, dass ein Gesprächsbild auch auf einer schnellen Leitung weich war.
    expect(getVideoSendeGrenzen().maxBitrate).toBeGreaterThan(1_700_000)
  })

  it('nimmt bei 30 Bildern die Hälfte der Bitrate', () => {
    saveVideoSettings({ aufloesung: '1080p', bildrate: 60 })
    const bei60 = getVideoSendeGrenzen()
    saveVideoSettings({ bildrate: 30 })
    const bei30 = getVideoSendeGrenzen()

    expect(bei30.maxBitrate).toBe(bei60.maxBitrate / 2)
    expect(bei30.maxFramerate).toBe(30)
  })
})

describe('Kamera für die Videonotiz', () => {
  it('ist quadratisch und nach vorn gerichtet', () => {
    const c = getVideoNoteConstraints()
    expect(c.facingMode).toBe('user')
    expect(c.width).toEqual({ ideal: VIDEONOTIZ_KANTE, max: VIDEONOTIZ_KANTE })
    expect(c.height).toEqual({ ideal: VIDEONOTIZ_KANTE, max: VIDEONOTIZ_KANTE })
  })

  it('deckelt die Kantenlänge, statt nur zu wünschen', () => {
    // Auf einer Webcam mit 2560×1440 lieferte `ideal: 720` allein ein Bild in
    // 1440×1440 — gemessen in der Desktop-App. Ein Wunsch ist kein Deckel, und
    // die Kamera nahm ihren besten Modus: viermal so viele Bildpunkte bei
    // gleicher Bitrate, für einen Kreis von wenigen Zentimetern.
    const c = getVideoNoteConstraints()
    expect((c.width as ConstrainULongRange).max).toBe(VIDEONOTIZ_KANTE)
    expect((c.height as ConstrainULongRange).max).toBe(VIDEONOTIZ_KANTE)
  })

  it('deckelt die Bildrate gerade nicht', () => {
    // Mehr Bilder kosten keine Bildpunkte und sind hier tatsächlich besser.
    expect((getVideoNoteConstraints().frameRate as ConstrainDoubleRange).max).toBeUndefined()
  })

  it('folgt der gewählten Bildrate', () => {
    saveVideoSettings({ bildrate: 30 })
    expect(getVideoNoteConstraints().frameRate).toEqual({ ideal: 30 })
  })

  it('fragt nichts mit `exact` an', () => {
    // Eine Kamera ohne Quadrat soll das nächstbeste Bild liefern und nicht mit
    // `OverconstrainedError` abbrechen — angezeigt wird ohnehin ein Kreis.
    const c = getVideoNoteConstraints()
    expect(JSON.stringify(c)).not.toContain('exact')
  })
})
