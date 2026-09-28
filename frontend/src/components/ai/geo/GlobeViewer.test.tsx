/**
 * Die Mitte der Regionsanalyse ohne MapTiler. Die Zusagen: Statt „Karte nicht
 * verfügbar" steht das Kartenbild da, benannt als Mosaik mit Quelle — und die
 * Kamera bewegt es wirklich: Jeder Befehl holt ein Bild für einen neuen
 * Ausschnitt, das alte bleibt, bis das neue geladen ist.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import type { AiRegionalAnalysis } from '@/api/ai'
import i18n from '@/i18n'

const apiStream = vi.hoisted(() => vi.fn())
vi.mock('@/api/client', async (original) => ({ ...(await original<typeof import('@/api/client')>()), apiStream }))

const karte = vi.hoisted(() => ({ grund: 'not_configured' as 'not_configured' | 'failed', montiert: 0 }))
vi.mock('./MapTilerDetailMap', async () => {
  const { useEffect } = await import('react')
  return {
    MapTilerDetailMap: ({ onUnavailable }: { onUnavailable: (grund: string) => void }) => {
      useEffect(() => {
        karte.montiert += 1
        // Wie die echte Karte: erst nach der Abfrage ihrer Einrichtung.
        void Promise.resolve().then(() => onUnavailable(karte.grund))
      }, [])
      return null
    },
  }
})

import { GlobeViewer } from './GlobeViewer'
import { applyGeoCameraCommand, normalizeRegionalAnalysis } from './regionalAnalysis'

const BERLIN_BOX = [13.0883, 52.3382, 13.7611, 52.6755]
const KARTENBILD = {
  id: 'latest_imagery',
  kind: 'map',
  name: 'Kartenbild',
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export?bbox=1',
  bbox: BERLIN_BOX,
  mission: 'ArcGIS World Imagery',
  attribution: 'Esri, Vantor, Earthstar Geographics, and the GIS User Community',
}

function analyse(layers: Record<string, unknown>, scenes: unknown[] = []): AiRegionalAnalysis {
  const ergebnis = normalizeRegionalAnalysis({
    status: 'success',
    location: 'Berlin, Deutschland',
    country: 'Deutschland',
    coordinates: { latitude: 52.52, longitude: 13.405, bbox: BERLIN_BOX },
    satellite: { available: true, scenes, layers },
    camera: { mode: 'focus', command_id: 'analyse-1' },
  })
  if (!ergebnis) throw new Error('Analyse ungültig')
  return ergebnis
}

function ausschnittAus(bild: HTMLElement): number[] {
  const adresse = new URL(bild.getAttribute('src') ?? '', 'http://panel.test')
  return (adresse.searchParams.get('bbox') ?? '').split(',').map(Number)
}

const alleBilder = () => [...document.querySelectorAll('img')] as HTMLElement[]
/** Das Bild, das gerade lädt: noch unsichtbar und deshalb für Screenreader verborgen. */
async function erstesBild(): Promise<HTMLElement> {
  await waitFor(() => expect(alleBilder()).toHaveLength(1))
  return alleBilder()[0]
}
const sichtbar = () => screen.getByRole('img', { name: i18n.t('ai.geo.image.alt', { location: 'Berlin, Deutschland' }) })

describe('GlobeViewer ohne MapTiler', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('de')
  })

  afterEach(() => {
    karte.grund = 'not_configured'
    karte.montiert = 0
    apiStream.mockReset()
    vi.restoreAllMocks()
  })

  it('zeigt das Kartenbild als Mosaik mit Quelle statt „Karte nicht verfügbar"', async () => {
    render(<GlobeViewer data={analyse({ latest_imagery: KARTENBILD })} />)

    const bild = await erstesBild()
    expect(bild).toHaveAttribute('aria-hidden', 'true')
    expect(bild.getAttribute('src')).toBe('/api/ai/geo/image?kind=map&bbox=13.0883,52.3382,13.7611,52.6755')
    expect(screen.getByText(i18n.t('ai.geo.image.map'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('ai.geo.image.mosaic'))).toBeInTheDocument()
    expect(screen.getByText('Quelle: Esri, Vantor, Earthstar Geographics, and the GIS User Community')).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('ai.geo.mapUnavailableTitle'))).not.toBeInTheDocument()
    // Nichts lädt eine Karte, die es nicht gibt.
    expect(screen.queryByText(i18n.t('ai.geo.mapLoading'))).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(i18n.t('ai.geo.image.loading'))

    fireEvent.load(bild)
    expect(sichtbar()).toBe(bild)
    expect(bild.className).toContain('opacity-100')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('beginnt eine Detailansicht am Ort, nicht in der Mitte seiner Box', async () => {
    const nah = { ...analyse({ latest_imagery: KARTENBILD }), camera: { mode: 'detail' as const, command_id: 'analyse-nah' } }
    render(<GlobeViewer data={nah} />)

    const [west, sued, ost, nord] = ausschnittAus(await erstesBild())
    expect((west + ost) / 2).toBeCloseTo(13.405, 4)
    expect((sued + nord) / 2).toBeCloseTo(52.52, 4)
  })

  it('holt in einer hohen Mitte ein hohes Bild', async () => {
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(600)
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(840)

    render(<GlobeViewer data={analyse({ latest_imagery: KARTENBILD })} />)

    expect((await erstesBild()).getAttribute('src')).toContain('&shape=portrait')
  })

  it('holt kein neues Bild, wenn die Mitte auf dem Handy hinter einem anderen Reiter verschwindet', async () => {
    const beobachter: Array<() => void> = []
    vi.stubGlobal('ResizeObserver', class {
      constructor(rueckruf: () => void) {
        beobachter.push(rueckruf)
      }
      observe() {}
      disconnect() {}
    })
    const groesse = { breite: 390, hoehe: 700 }
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(() => groesse.breite)
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(() => groesse.hoehe)
    try {
      render(<GlobeViewer data={analyse({ latest_imagery: KARTENBILD })} />)
      const bild = await erstesBild()
      expect(bild.getAttribute('src')).toContain('&shape=portrait')

      // Anderer Reiter: `hidden` misst 0 × 0.
      groesse.breite = 0
      groesse.hoehe = 0
      act(() => beobachter.forEach((rueckruf) => rueckruf()))

      expect(alleBilder()).toEqual([bild])
      expect(bild.getAttribute('src')).toContain('&shape=portrait')
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('zoomt mit einem neuen Bild und lässt das alte stehen, bis es geladen ist', async () => {
    const start = analyse({ latest_imagery: KARTENBILD })
    const { rerender } = render(<GlobeViewer data={start} />)
    const erstes = await erstesBild()
    fireEvent.load(erstes)

    const gezoomt = applyGeoCameraCommand(start, { action: 'zoom_in', command_id: 'zoom-1' })
    rerender(<GlobeViewer data={gezoomt} />)

    // Das neue Bild lädt unsichtbar über dem alten.
    const bilder = alleBilder()
    expect(bilder).toHaveLength(2)
    const neues = bilder[1]
    expect(neues.className).toContain('opacity-0')
    expect(erstes.className).toContain('opacity-100')
    expect(screen.getByRole('status')).toHaveTextContent(i18n.t('ai.geo.image.loading'))
    const [west, sued, ost, nord] = ausschnittAus(neues)
    expect(ost - west).toBeCloseTo((BERLIN_BOX[2] - BERLIN_BOX[0]) * 0.35, 4)
    expect(nord - sued).toBeCloseTo((BERLIN_BOX[3] - BERLIN_BOX[1]) * 0.35, 4)
    // Um den Ort, nicht um die Mitte seiner Box.
    expect((west + ost) / 2).toBeCloseTo(13.405, 4)
    expect((sued + nord) / 2).toBeCloseTo(52.52, 4)

    vi.useFakeTimers()
    try {
      fireEvent.load(neues)
      expect(neues.className).toContain('opacity-100')
      expect(sichtbar()).toBe(neues)
      // Nach der Überblendung ist das alte Bild fort.
      act(() => vi.advanceTimersByTime(700))
      expect(alleBilder()).toEqual([neues])
    } finally {
      vi.useRealTimers()
    }
  })

  it('holt das vorige Bild zurück, wenn die Kamera während der Überblendung dorthin zurückkehrt', async () => {
    const start = analyse({ latest_imagery: KARTENBILD })
    const { rerender } = render(<GlobeViewer data={start} />)
    const erstes = await erstesBild()
    fireEvent.load(erstes)

    rerender(<GlobeViewer data={applyGeoCameraCommand(start, { action: 'overview', command_id: 'weit-1' })} />)
    const weit = alleBilder().at(-1) as HTMLElement
    vi.useFakeTimers()
    try {
      fireEvent.load(weit)
      expect(sichtbar()).toBe(weit)

      // Noch in der Überblendung dieselbe Analyse noch einmal: zurück zum Anfang,
      // dessen Bild längst geladen ist und kein `load` mehr meldet.
      rerender(<GlobeViewer data={{ ...start, camera: { mode: 'focus', command_id: 'analyse-2' } }} />)
      expect(sichtbar()).toBe(erstes)
      expect(screen.queryByRole('status')).not.toBeInTheDocument()

      act(() => vi.advanceTimersByTime(700))
      expect(alleBilder()).toEqual([erstes])
      expect(sichtbar()).toBe(erstes)
    } finally {
      vi.useRealTimers()
    }
  })

  it('meldet ein Bild, das beim zweiten Anlauf kommt, nicht mehr als fehlend', async () => {
    apiStream.mockResolvedValue(new Response('', { status: 502 }))
    const berlin = analyse({ latest_imagery: KARTENBILD })
    const { rerender } = render(<GlobeViewer data={berlin} />)
    fireEvent.error(await erstesBild())
    expect(await screen.findByText(i18n.t('ai.geo.image.unavailable'))).toBeInTheDocument()

    // Ein anderer Ort, dann wieder Berlin: derselbe Ausschnitt, neuer Anlauf.
    const potsdam = analyse({ latest_imagery: { ...KARTENBILD, bbox: [12.9, 52.3, 13.2, 52.5] } })
    rerender(<GlobeViewer data={potsdam} />)
    fireEvent.load(alleBilder().at(-1) as HTMLElement)
    rerender(<GlobeViewer data={{ ...berlin, camera: { mode: 'focus', command_id: 'analyse-2' } }} />)
    const zweiterAnlauf = alleBilder().find((bild) => bild.getAttribute('src')?.includes('13.0883')) as HTMLElement
    fireEvent.load(zweiterAnlauf)

    expect(sichtbar()).toBe(zweiterAnlauf)
    expect(screen.queryByText(i18n.t('ai.geo.image.unavailable'))).not.toBeInTheDocument()
  })

  it('fährt zur Sehenswürdigkeit, ohne MapTiler erneut zu fragen', async () => {
    const start = analyse({ latest_imagery: KARTENBILD })
    const { rerender } = render(<GlobeViewer data={start} />)
    fireEvent.load(await erstesBild())

    const dort = applyGeoCameraCommand(start, {
      action: 'focus_location',
      command_id: 'fokus-1',
      location: 'Brandenburger Tor, Berlin',
      coordinates: { latitude: 52.5163, longitude: 13.3777, bbox: [13.3774, 52.5161, 13.378, 52.5165] },
    })
    rerender(<GlobeViewer data={dort} />)

    const neues = alleBilder().at(-1) as HTMLElement
    const [west, sued, ost, nord] = ausschnittAus(neues)
    expect((west + ost) / 2).toBeCloseTo(13.3777, 4)
    expect((sued + nord) / 2).toBeCloseTo(52.5163, 4)
    expect(karte.montiert).toBe(1)
  })

  it('versucht MapTiler nach einem vorübergehenden Fehler beim nächsten Ort erneut', async () => {
    karte.grund = 'failed'
    const start = analyse({ latest_imagery: KARTENBILD })
    const { rerender } = render(<GlobeViewer data={start} />)
    await erstesBild()

    rerender(<GlobeViewer data={applyGeoCameraCommand(start, {
      action: 'focus_location',
      command_id: 'fokus-2',
      location: 'Potsdam',
      coordinates: { latitude: 52.3906, longitude: 13.0645, bbox: [12.9, 52.3, 13.2, 52.5] },
    })} />)

    await waitFor(() => expect(karte.montiert).toBe(2))
  })

  it('zeigt neben einer Szene das Kartenbild — nie die Szene als Karte', async () => {
    const szene = {
      id: 'latest_imagery',
      kind: 'scene',
      name: 'Satellitenszene',
      url: 'https://datahub.creodias.eu/odata/v1/Assets(1)/$value',
      scene_id: 'S2B_MSIL2A_20260912T101559',
      mission: 'Sentinel-2 L2A',
      captured_at: '2026-09-12T10:15:59Z',
      attribution: 'Copernicus Sentinel data 2026',
    }
    render(<GlobeViewer data={analyse(
      { latest_imagery: szene, map_imagery: { ...KARTENBILD, id: 'map_imagery' } },
      [{ id: szene.scene_id, mission: 'Sentinel-2 L2A', datetime: szene.captured_at, cloud_cover_percent: 8, preview_url: szene.url }],
    )} />)

    expect((await erstesBild()).getAttribute('src')).toContain('kind=map')
    expect(screen.queryByText('Sentinel-2 L2A')).not.toBeInTheDocument()
    expect(screen.queryByText(i18n.t('ai.geo.sceneMetadata'))).not.toBeInTheDocument()
  })

  it('meldet ohne Kartenbild die fehlende Karte, ohne sie zu laden', async () => {
    // Stände von vor 09/2026 kennen das Kartenbild nicht.
    const { kind: _kind, bbox: _bbox, ...alt } = KARTENBILD
    render(<GlobeViewer data={analyse({ latest_imagery: alt })} />)

    expect(await screen.findByText(i18n.t('ai.geo.mapUnavailableTitle'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('ai.geo.mapLoading'))).not.toBeInTheDocument()
    expect(alleBilder()).toEqual([])
  })
})
