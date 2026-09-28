/**
 * Das Bild der Region im Informationspanel. Mit MapTiler ist die Karte die
 * einzige Bildfläche; ohne MapTiler steht das Bild der Region an ihrer Stelle
 * — früher stand dort nur „Karte nicht verfügbar", obwohl das Kartenbild da war.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { useEffect } from 'react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AiRegionalAnalysis } from '@/api/ai'
import i18n from '@/i18n'

const karte = vi.hoisted(() => ({ eingerichtet: true }))

vi.mock('./MapTilerDetailMap', () => ({
  MapTilerDetailMap: ({ onUnavailable }: { onUnavailable?: () => void }) => {
    useEffect(() => {
      if (!karte.eingerichtet) onUnavailable?.()
    }, [onUnavailable])
    return <div data-testid="maptiler" />
  },
}))

import { RegionalInfoPanel } from './RegionalInfoPanel'

const BERLIN: AiRegionalAnalysis = {
  status: 'success',
  location: 'Berlin, Deutschland',
  country: 'Deutschland',
  coordinates: { latitude: 52.52, longitude: 13.405, bbox: [13.0883, 52.3382, 13.7611, 52.6755] },
  satellite: {
    available: true,
    scenes: [],
    layers: {
      latest_imagery: {
        id: 'latest_imagery',
        kind: 'map',
        name: 'Kartenbild',
        url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export?bbox=1',
        bbox: [13.0883, 52.3382, 13.7611, 52.6755],
        resolution: 'anbieterabhängig',
        mission: 'ArcGIS World Imagery',
        description: 'Mosaik aus vielen Aufnahmen, kein einzelner Überflug und ohne Aufnahmezeitpunkt.',
        attribution: 'Esri, Vantor, Earthstar Geographics, and the GIS User Community',
      },
    },
  },
}

const bildName = () => i18n.t('ai.geo.image.alt', { location: 'Berlin, Deutschland' })

describe('RegionalInfoPanel — Bild der Region', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('de')
  })

  beforeEach(() => {
    karte.eingerichtet = true
  })

  it('zeigt ohne MapTiler das Kartenbild in der Übersicht und im Satellitenbereich', () => {
    karte.eingerichtet = false
    render(<RegionalInfoPanel data={BERLIN} onClose={vi.fn()} />)

    expect(screen.getByRole('img', { name: bildName() })).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('ai.geo.mapUnavailableTitle'))).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: /Satellit/i }))
    expect(screen.getByRole('img', { name: bildName() })).toBeInTheDocument()
    // Ein Kartenbild ist keine Szene — weder im Kopf noch im Link.
    expect(screen.getByText(i18n.t('ai.geo.imageHeading'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('ai.geo.mosaicBadge'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('ai.geo.scenes'))).not.toBeInTheDocument()
    // Das Bild holt das Panel; kein Link schickt den Browser selbst zu Esri.
    expect(document.querySelector('a[href*="arcgisonline"]')).toBeNull()
    expect(screen.queryByText(i18n.t('ai.geo.sceneAvailableWithoutMap'))).not.toBeInTheDocument()
  })

  it('lässt mit MapTiler die Karte die einzige Bildfläche sein', () => {
    render(<RegionalInfoPanel data={BERLIN} onClose={vi.fn()} />)

    expect(screen.getByTestId('maptiler')).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: bildName() })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: /Satellit/i }))
    expect(screen.getByTestId('maptiler')).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: bildName() })).not.toBeInTheDocument()
  })

  it('zeigt bei einer Sentinel-Szene den Szenenbereich', () => {
    karte.eingerichtet = false
    const szene: AiRegionalAnalysis = {
      ...BERLIN,
      satellite: {
        available: true,
        scenes: [{
          id: 'S2B_MSIL2A_20260912T101559',
          mission: 'Sentinel-2 L2A',
          datetime: '2026-09-12T10:15:59Z',
          cloud_cover_percent: 8.2,
          preview_url: 'https://datahub.creodias.eu/odata/v1/Assets(1)/$value',
        }],
        layers: {
          latest_imagery: {
            id: 'latest_imagery',
            kind: 'scene',
            name: 'Satellitenszene',
            url: 'https://datahub.creodias.eu/odata/v1/Assets(1)/$value',
            scene_id: 'S2B_MSIL2A_20260912T101559',
            mission: 'Sentinel-2 L2A',
            captured_at: '2026-09-12T10:15:59Z',
            cloud_cover_percent: 8.2,
            attribution: 'Copernicus Sentinel data 2026',
          },
        },
      },
    }
    render(<RegionalInfoPanel data={szene} onClose={vi.fn()} />)

    fireEvent.click(screen.getByRole('tab', { name: /Satellit/i }))
    expect(screen.getByText(i18n.t('ai.geo.scenes'))).toBeInTheDocument()
    expect(screen.getByRole('img', { name: bildName() })).toHaveAttribute(
      'src', '/api/ai/geo/image?kind=scene&scene_id=S2B_MSIL2A_20260912T101559',
    )
    // Auch zur Szene kein Link zum Anbieter: Die Adresse aus dem Katalog
    // lädt bei CREODIAS nur dieselbe Vorschau herunter, die hier schon steht.
    expect(document.querySelector('a[href*="creodias"]')).toBeNull()
    expect(document.querySelector('a[href*="copernicus"]')).toBeNull()
  })
})
