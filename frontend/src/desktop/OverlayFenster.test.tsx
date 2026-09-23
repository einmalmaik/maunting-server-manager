/**
 * Das Overlay der Desktop-App — der Sprachschwarm des Wake-Words.
 *
 * Was hier zählt, sind die Zusagen des Fensters: es zeigt sich erst, wenn
 * jemand sprechen will; das Schaufenster zeigt ohne Mikrofon; ESC, X und
 * Stille schließen es; und der Schwarm zeigt dieselbe Form wie im Panel —
 * das Logo, wenn ein Werkzeug läuft, die Erde bei einer Regionalanalyse.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AiRegionalAnalysis } from '@/api/ai'
import i18n from '@/i18n'
import type { Sprachzeile, Sprachzustand } from '@/components/ai/voice/useSprachsitzung'

const hoerer = vi.hoisted(() => new Map<string, (ereignis: { payload: unknown }) => void>())
const starten = vi.fn()
const beenden = vi.fn()
const overlaySichtbar = vi.fn()
const trefferflaechen = vi.fn((_flaechen: unknown) => Promise.resolve())

let sitzung: {
  zustand: Sprachzustand
  abgelaufen: boolean
  zeilen: Sprachzeile[]
  werkzeug: string | null
  werkzeugLaeuft: boolean
  werkzeugStarts: number
  fehler: string | null
  geoData: AiRegionalAnalysis | null
  regionalContextActive: boolean
}

const schwarm = vi.hoisted(() => ({
  zuletzt: null as null | { zustand: string; ort: unknown; impulse: number; vorfuehrung: boolean },
}))

vi.mock('@tauri-apps/api/event', () => ({
  listen: (name: string, rueckruf: (ereignis: { payload: unknown }) => void) => {
    hoerer.set(name, rueckruf)
    // Nur den eigenen Hörer austragen: das Abmelden kommt asynchron und darf
    // den eines schon neu gezeichneten Fensters nicht mitnehmen.
    return Promise.resolve(() => {
      if (hoerer.get(name) === rueckruf) hoerer.delete(name)
    })
  },
}))

vi.mock('@/components/ai/voice/useSprachsitzung', () => ({
  useSprachsitzung: () => ({ ...sitzung, pegel: () => 0, starten, beenden }),
}))

vi.mock('@/components/ai/voice/Schwarm', async (original) => ({
  ...(await original<typeof import('@/components/ai/voice/Schwarm')>()),
  Schwarm: (props: { zustand: string; ort?: unknown; impulse?: number; vorfuehrung?: boolean }) => {
    schwarm.zuletzt = {
      zustand: props.zustand,
      ort: props.ort ?? null,
      impulse: props.impulse ?? 0,
      vorfuehrung: props.vorfuehrung ?? false,
    }
    return null
  },
}))

vi.mock('@/components/ai/voice/audioGeraete', () => ({
  registriereAudioGeraete: vi.fn(),
  registriereAudioVerarbeitung: vi.fn(),
}))

vi.mock('./transport', () => ({
  istAngemeldet: () => true,
  stillAnmelden: vi.fn(),
}))

vi.mock('./tauri', () => ({
  konfigLaden: () => Promise.reject(new Error('im Test keine Konfiguration')),
  overlaySichtbar: (sichtbar: boolean) => overlaySichtbar(sichtbar),
  overlayTrefferflaechen: (flaechen: unknown) => trefferflaechen(flaechen),
}))

vi.mock('./sprachKoordination', async (original) => ({
  ...(await original<typeof import('./sprachKoordination')>()),
  sprachstartMelden: () => Promise.resolve(),
  beiFremdemSprachstart: () => () => undefined,
  sprachzustandVerdrahten: () => () => undefined,
}))

import { OverlayFenster } from './OverlayFenster'
import {
  OVERLAY_SCHAUFENSTER,
  OVERLAY_SPRACHE_ENDE,
  OVERLAY_SPRACHE_START,
  OVERLAY_ZUSTAND_TEST,
} from './sprachKoordination'

const BERLIN: AiRegionalAnalysis = {
  status: 'success',
  location: 'Berlin',
  country: 'Deutschland',
  coordinates: { latitude: 52.52, longitude: 13.405, bbox: [13.08, 52.33, 13.76, 52.67] },
}

async function senden(name: string, payload: unknown = null) {
  await waitFor(() => expect(hoerer.has(name)).toBe(true))
  act(() => hoerer.get(name)?.({ payload }))
}

async function offen(teil: Partial<typeof sitzung> = {}, inApp = false) {
  sitzung = { ...sitzung, ...teil }
  const ergebnis = render(<OverlayFenster inApp={inApp} />)
  await senden(OVERLAY_SPRACHE_START)
  return ergebnis
}

describe('OverlayFenster', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('de')
  })

  beforeEach(() => {
    hoerer.clear()
    starten.mockClear()
    beenden.mockClear()
    overlaySichtbar.mockClear()
    trefferflaechen.mockClear()
    schwarm.zuletzt = null
    sitzung = {
      zustand: 'bereit',
      abgelaufen: false,
      zeilen: [],
      werkzeug: null,
      werkzeugLaeuft: false,
      werkzeugStarts: 0,
      fehler: null,
      geoData: null,
      regionalContextActive: true,
    }
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('zeigt nichts, bis jemand sprechen will', async () => {
    const { container } = render(<OverlayFenster />)
    await waitFor(() => expect(hoerer.has(OVERLAY_SPRACHE_START)).toBe(true))

    // Sonst blitzte beim App-Start ein leerer Schwarm auf.
    expect(container).toBeEmptyDOMElement()
    expect(starten).not.toHaveBeenCalled()
  })

  it('zeigt beim Start den Schwarm und beginnt die Sitzung', async () => {
    await offen({ zustand: 'hoert' })

    await waitFor(() => expect(starten).toHaveBeenCalledOnce())
    expect(schwarm.zuletzt).toMatchObject({ zustand: 'listening', vorfuehrung: false })
    expect(screen.getByText(i18n.t('ai.voice.zustand.hoert'))).toBeInTheDocument()
  })

  it('zeigt im Schaufenster ohne Mikrofon, und die Diagnose-Knoepfe waehlen die Form', async () => {
    sitzung.zeilen = [{ wer: 'ki', text: 'Eine Zeile aus einer fremden Sitzung' }]
    render(<OverlayFenster />)
    await senden(OVERLAY_SCHAUFENSTER)

    expect(starten).not.toHaveBeenCalled()
    expect(schwarm.zuletzt).toMatchObject({ zustand: 'ready', vorfuehrung: true })

    await senden(OVERLAY_ZUSTAND_TEST, 'denkt')
    expect(schwarm.zuletzt?.zustand).toBe('thinking')
    expect(screen.getByText(i18n.t('ai.voice.zustand.denkt'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('ai.voice.hint.denkt'))).toBeInTheDocument()
    // Das Schaufenster hat kein Gespräch — also auch keine Untertitel.
    expect(screen.queryByText(/fremden Sitzung/)).not.toBeInTheDocument()

    // Unbekanntes aus dem Ereignis färbt nichts um.
    await senden(OVERLAY_ZUSTAND_TEST, 'irgendwas')
    expect(schwarm.zuletzt?.zustand).toBe('thinking')
  })

  it('sagt beim laufenden Werkzeug, was es tut, und formt das Logo', async () => {
    await offen({ zustand: 'denkt', werkzeug: 'read_server_status', werkzeugLaeuft: true, werkzeugStarts: 2 })

    expect(screen.getByText(i18n.t('ai.toolsRunning.read_server_status'))).toBeInTheDocument()
    expect(screen.queryByText('read_server_status')).not.toBeInTheDocument()
    expect(schwarm.zuletzt).toMatchObject({ zustand: 'working', impulse: 2 })
  })

  it('wird bei einer Regionalanalyse zur Erde und nennt den Ort', async () => {
    await offen({ zustand: 'spricht', geoData: BERLIN })

    expect(schwarm.zuletzt?.ort).toMatchObject({ latitude: 52.52, longitude: 13.405 })
    expect(screen.getByText('Berlin')).toBeInTheDocument()
  })

  it('zeigt keine Erde mehr, wenn das Gespraech die Region verlassen hat', async () => {
    await offen({ zustand: 'spricht', geoData: BERLIN, regionalContextActive: false })

    expect(schwarm.zuletzt?.ort).toBeNull()
    expect(screen.queryByText('Berlin')).not.toBeInTheDocument()
  })

  it('zeigt die letzten drei Zeilen als Untertitel', async () => {
    await offen({
      zustand: 'spricht',
      zeilen: [
        { wer: 'ich', text: 'Erste Frage' },
        { wer: 'ki', text: 'Erste Antwort' },
        { wer: 'ich', text: 'Zweite Frage' },
        { wer: 'ki', text: 'Zweite Antwort' },
      ],
    })

    expect(screen.queryByText('Erste Frage')).not.toBeInTheDocument()
    expect(screen.getByText('Erste Antwort')).toBeInTheDocument()
    expect(screen.getByText('Zweite Antwort')).toBeInTheDocument()
    expect(screen.getAllByText(i18n.t('mss.overlay.ich'))).toHaveLength(1)
  })

  it('blendet die aelteste Zeile erst aus, wenn die Untertitel ueber ihre Box hinauslaufen', async () => {
    // jsdom rechnet kein Layout: die Höhe der Zeilen, die der Box und ihr
    // Saum (6 px oben und unten, für den Schriftschatten) kommen von hier.
    let zeilenHoehe = 57
    const spione = [
      vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(() => zeilenHoehe),
      vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(69),
      vi.spyOn(window, 'getComputedStyle').mockReturnValue({
        paddingTop: '6px',
        paddingBottom: '6px',
      } as CSSStyleDeclaration),
    ]
    try {
      const { rerender } = await offen({ zustand: 'spricht', zeilen: [{ wer: 'ki', text: 'Drei Zeilen lang' }] })
      const box = () => screen.getByText(/^Drei Zeilen lang|^Etwas mehr$/).parentElement!.parentElement!
      // Passt genau hinein: nichts wird durchsichtig.
      expect(box().className).not.toContain('mask-image')

      // Der Saum zählt nicht als Platz — 64 px Zeilen laufen über 57 px hinaus.
      zeilenHoehe = 64
      sitzung = { ...sitzung, zeilen: [{ wer: 'ki', text: 'Etwas mehr' }] }
      rerender(<OverlayFenster />)
      expect(box().className).toContain('mask-image')
    } finally {
      for (const spion of spione) spion.mockRestore()
    }
  })

  it('stellt den Fehler vor den Zustand und zeigt die Stoerung', async () => {
    await offen({ zustand: 'bereit', fehler: 'ai.voice.errors.audio' })

    expect(screen.getByText(i18n.t('ai.voice.errors.audio'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('ai.voice.hint.error'))).toBeInTheDocument()
    expect(schwarm.zuletzt?.zustand).toBe('fault')
  })

  it('sagt, dass die Sitzung abgelaufen ist', async () => {
    await offen({ zustand: 'verbindet', abgelaufen: true })

    expect(screen.getByText(i18n.t('ai.voice.zustand.abgelaufen'))).toBeInTheDocument()
    expect(schwarm.zuletzt?.zustand).toBe('expired')
  })

  it('zeigt den Zustand in dem Ton, in dem der Schwarm leuchtet', async () => {
    const { container } = await offen({ zustand: 'denkt', werkzeug: 'list_my_servers', werkzeugLaeuft: true })

    const buehne = container.querySelector('[data-zustand]') as HTMLElement
    expect(buehne.dataset.zustand).toBe('working')
    const marke = buehne.querySelector('span[aria-hidden="true"]') as HTMLElement
    expect(marke.style.backgroundColor).toContain('--dna-voice-think')
  })

  it('schwebt frei — ohne Kasten und ohne abgedunkelten Hintergrund', async () => {
    // Früher lag der Schwarm auf einer halbdurchsichtigen schwarzen Fläche,
    // in der App zusätzlich über einer Abdunklung des ganzen Bildschirms.
    const desktop = await offen()
    const fenster = desktop.container.querySelector('[data-zustand]') as HTMLElement
    expect(fenster.className).not.toMatch(/bg-|rounded|border|msm-modal-overlay/)
    desktop.unmount()

    const app = await offen({}, true)
    const schwebe = app.container.querySelector('[data-zustand]') as HTMLElement
    expect(app.container.querySelector('.msm-modal-overlay')).toBeNull()
    expect(schwebe.className).not.toMatch(/bg-/)
    // Die App darunter bleibt bedienbar; nur der Schließen-Knopf fängt Klicks.
    expect(schwebe.className).toContain('pointer-events-none')
    expect(screen.getByRole('button', { name: i18n.t('mss.overlay.schliessen') }).className).toContain('pointer-events-auto')
  })

  it('schliesst mit X und ESC — Sitzung aus, Fenster weg', async () => {
    await offen({ zustand: 'hoert' })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.overlay.schliessen') }))

    expect(beenden).toHaveBeenCalledOnce()
    expect(overlaySichtbar).toHaveBeenLastCalledWith(false)

    await senden(OVERLAY_SPRACHE_START)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(beenden).toHaveBeenCalledTimes(2)
  })

  it('schliesst, wenn der Sprach-Hotkey ein zweites Mal kommt', async () => {
    await offen({ zustand: 'spricht' })

    await senden(OVERLAY_SPRACHE_ENDE)

    expect(beenden).toHaveBeenCalledOnce()
    expect(overlaySichtbar).toHaveBeenLastCalledWith(false)
  })

  it('schliesst nach zwanzig Sekunden Stille — das Schaufenster nicht', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await offen({ zustand: 'bereit' })
    act(() => {
      vi.advanceTimersByTime(20_000)
    })
    // Ein Fehltrigger des Wake-Words hinterlässt kein offenes Mikrofon.
    expect(beenden).toHaveBeenCalledOnce()

    beenden.mockClear()
    await senden(OVERLAY_SCHAUFENSTER)
    act(() => {
      vi.advanceTimersByTime(60_000)
    })
    expect(beenden).not.toHaveBeenCalled()
  })

  it('nimmt auf dem Desktop nur am X Klicks an, damit der Rest durchlässt', async () => {
    const mass = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
      { left: 436, top: 12, width: 32, height: 32, right: 468, bottom: 44, x: 436, y: 12, toJSON: () => ({}) } as DOMRect,
    )
    try {
      const { container, unmount } = await offen()
      await waitFor(() => expect(trefferflaechen).toHaveBeenCalledWith([[432, 8, 40, 40]]))
      // Ein neues Maß meldet neu.
      const bisher = trefferflaechen.mock.calls.length
      act(() => {
        window.dispatchEvent(new Event('resize'))
      })
      expect(trefferflaechen).toHaveBeenCalledTimes(bisher + 1)
      // Gezogen wird nicht: `start_dragging` erlaubt keine Capability, ein
      // Ziehbereich wäre nur ein Fehler in der Konsole.
      expect(container.querySelector('[data-tauri-drag-region]')).toBeNull()
      unmount()

      // In der App lässt `pointer-events` durch; Rust hat dort nichts zu tun.
      trefferflaechen.mockClear()
      await offen({}, true)
      expect(trefferflaechen).not.toHaveBeenCalled()
    } finally {
      mass.mockRestore()
    }
  })
})
