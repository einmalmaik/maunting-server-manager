/**
 * Das Bild einer Regionsanalyse. Die Zusagen: ein Kartenbild steht als Mosaik
 * ohne Überflug da und nie mit Datum, eine Szene mit Zeitpunkt und Bewölkung;
 * die Quelle steht daneben; und das Bild kommt immer vom Panel — nie von
 * ArcGIS oder Copernicus direkt.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import type { AiSatelliteLayer } from '@/api/ai'
import i18n from '@/i18n'

const apiStream = vi.hoisted(() => vi.fn())
vi.mock('@/api/client', () => ({ apiStream }))

import { Regionalbild } from './Regionalbild'

const KARTENBILD: AiSatelliteLayer = {
  id: 'latest_imagery',
  kind: 'map',
  name: 'Kartenbild',
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export?bbox=1',
  bbox: [13.0883, 52.3382, 13.7611, 52.6755],
  attribution: 'Esri, Vantor, Earthstar Geographics, and the GIS User Community',
}

const SZENE: AiSatelliteLayer = {
  id: 'latest_imagery',
  kind: 'scene',
  name: 'Satellitenszene',
  url: 'https://datahub.creodias.eu/odata/v1/Assets(1)/$value',
  scene_id: 'S2B_MSIL2A_20260912T101559',
  mission: 'Sentinel-2 L2A',
  captured_at: '2026-09-12T10:15:59Z',
  cloud_cover_percent: 8.2,
  attribution: 'Copernicus Sentinel data 2026',
}

describe('Regionalbild', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('de')
  })

  afterEach(() => {
    apiStream.mockReset()
    delete (window as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__
    vi.unstubAllGlobals()
  })

  it('zeigt das Kartenbild als Mosaik ohne Datum, mit Quelle, vom eigenen Panel', () => {
    // Selbst ein Zeitpunkt, der sich hineinverirrt, macht es nicht zur Aufnahme.
    render(<Regionalbild layer={{ ...KARTENBILD, captured_at: '2026-09-12T10:15:59Z' }} location="Berlin" />)

    const bild = screen.getByRole('img', { name: i18n.t('ai.geo.image.alt', { location: 'Berlin' }) })
    expect(bild).toHaveAttribute('src', '/api/ai/geo/image?kind=map&bbox=13.0883,52.3382,13.7611,52.6755')
    expect(screen.getByText(i18n.t('ai.geo.image.map'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('ai.geo.image.mosaic'))).toBeInTheDocument()
    expect(screen.queryByText(/September/)).not.toBeInTheDocument()
    expect(screen.getByText('Quelle: Esri, Vantor, Earthstar Geographics, and the GIS User Community')).toBeInTheDocument()
    expect(apiStream).not.toHaveBeenCalled()
  })

  it('zeigt bei einer Szene Mission, Aufnahmezeitpunkt, Bewölkung und Copernicus', () => {
    render(<Regionalbild layer={SZENE} location="Berlin" />)

    expect(screen.getByRole('img')).toHaveAttribute(
      'src', '/api/ai/geo/image?kind=scene&scene_id=S2B_MSIL2A_20260912T101559',
    )
    expect(screen.getByText('Sentinel-2 L2A')).toBeInTheDocument()
    expect(screen.getByText('Aufgenommen am 12. September 2026, 8 % Bewölkung')).toBeInTheDocument()
    expect(screen.getByText('Quelle: Copernicus Sentinel data 2026')).toBeInTheDocument()
  })

  it('blendet das Bild erst ein, wenn es geladen ist, und meldet einen Fehlschlag', async () => {
    apiStream.mockResolvedValue(new Response('{"detail":"AI_GEO_IMAGE_UNAVAILABLE"}', { status: 502 }))
    render(<Regionalbild layer={KARTENBILD} location="Berlin" />)
    const bild = screen.getByRole('img')
    expect(bild.className).toContain('opacity-0')
    expect(screen.getByRole('figure')).toHaveAttribute('aria-busy', 'true')

    fireEvent.load(bild)
    expect(bild.className).toContain('opacity-100')
    expect(screen.getByRole('figure')).toHaveAttribute('aria-busy', 'false')

    fireEvent.error(bild)
    // Erst die Nachfrage beim Panel entscheidet — und es hat kein Bild.
    expect(await screen.findByText(i18n.t('ai.geo.image.unavailable'))).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(apiStream).toHaveBeenCalledTimes(1)
  })

  it('lädt nach einem abgelaufenen Sitzungscookie neu, statt das Bild aufzugeben', async () => {
    // `apiStream` frischt die Sitzung bei einem 401 selbst auf; danach liefert
    // das Panel das Bild.
    apiStream.mockResolvedValue(new Response(new Uint8Array([0xff, 0xd8, 0xff]), { status: 200 }))
    render(<Regionalbild layer={KARTENBILD} location="Berlin" />)

    fireEvent.error(screen.getByRole('img'))

    await waitFor(() => expect(screen.getByRole('img')).toHaveAttribute(
      'src', '/api/ai/geo/image?kind=map&bbox=13.0883,52.3382,13.7611,52.6755&versuch=2',
    ))
    expect(apiStream).toHaveBeenCalledWith(
      '/ai/geo/image?kind=map&bbox=13.0883,52.3382,13.7611,52.6755',
      expect.objectContaining({ method: 'GET' }),
    )
    fireEvent.load(screen.getByRole('img'))
    expect(screen.getByRole('img').className).toContain('opacity-100')
    expect(screen.queryByText(i18n.t('ai.geo.image.unavailable'))).not.toBeInTheDocument()

    // Scheitert auch der zweite Anlauf, fehlt das Bild — ohne dritte Nachfrage.
    fireEvent.error(screen.getByRole('img'))
    expect(screen.getByText(i18n.t('ai.geo.image.unavailable'))).toBeInTheDocument()
    expect(apiStream).toHaveBeenCalledTimes(1)
  })

  it('fragt bei einer Ebene ohne abrufbares Bild gar nicht erst', () => {
    // Stände von vor 09/2026 kennen `kind` nicht.
    const { kind: _kind, ...alt } = KARTENBILD
    render(<Regionalbild layer={alt} location="Berlin" />)

    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByText(i18n.t('ai.geo.image.unavailable'))).toBeInTheDocument()
    expect(apiStream).not.toHaveBeenCalled()
  })

  it('holt das Bild in der Desktop-App mit Token als Blob und gibt es wieder frei', async () => {
    // Ein <img> trägt dort kein Bearer-Token.
    ;(window as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ = {}
    const erzeugen = vi.fn(() => 'blob:msm/regionalbild')
    const freigeben = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: erzeugen, revokeObjectURL: freigeben }))
    // jsdoms Blob kann nicht streamen — die Antwort genügt als Attrappe.
    apiStream.mockResolvedValue({ ok: true, status: 200, blob: async () => new Blob([new Uint8Array([0xff, 0xd8, 0xff])]) })

    const { unmount } = render(<Regionalbild layer={KARTENBILD} location="Berlin" />)

    await waitFor(() => expect(screen.getByRole('img')).toHaveAttribute('src', 'blob:msm/regionalbild'))
    expect(apiStream).toHaveBeenCalledWith(
      '/ai/geo/image?kind=map&bbox=13.0883,52.3382,13.7611,52.6755',
      expect.objectContaining({ method: 'GET', headers: { Accept: 'image/*' } }),
    )
    unmount()
    expect(freigeben).toHaveBeenCalledWith('blob:msm/regionalbild')
  })

  it('zeigt in der Desktop-App den Fehler, wenn das Panel kein Bild liefert', async () => {
    ;(window as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ = {}
    apiStream.mockResolvedValue(new Response('{"detail":"AI_GEO_IMAGE_UNAVAILABLE"}', { status: 502 }))

    render(<Regionalbild layer={SZENE} location="Berlin" />)

    expect(await screen.findByText(i18n.t('ai.geo.image.unavailable'))).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
})
