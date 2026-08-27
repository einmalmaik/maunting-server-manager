import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { AiRegionalAnalysis } from '@/api/ai'
import i18n from '@/i18n'
import { RegionalInfoPanel } from './RegionalInfoPanel'

describe('RegionalInfoPanel', () => {
  const mockData: AiRegionalAnalysis = {
    status: 'success',
    location: 'Berlin',
    country: 'Deutschland',
    coordinates: {
      latitude: 52.52,
      longitude: 13.405,
      bbox: [13.0883, 52.3382, 13.7611, 52.6755],
    },
    weather: {
      temperature_celsius: 18.5,
      apparent_temperature_celsius: 17.8,
      condition: 'Teilweise bewölkt',
      humidity_percent: 65,
      wind_speed_kmh: 12.4,
      precipitation_mm: 0.0,
    },
    satellite: {
      available: true,
      scenes: [
        {
          id: 'S2A_MSIL2A_20260825',
          mission: 'Sentinel-2 L2A',
          datetime: '2026-08-25T10:30:00Z',
          cloud_cover_percent: 4.2,
          preview_url: 'https://browser.dataspace.copernicus.eu/preview.jpg',
        },
      ],
    },
  }

  it('rendert Ort, Koordinaten und Wetterdaten', async () => {
    await i18n.changeLanguage('de')
    const onClose = vi.fn()

    render(<RegionalInfoPanel data={mockData} onClose={onClose} />)

    expect(screen.getByText('Berlin')).toBeInTheDocument()
    expect(screen.getByText('Deutschland')).toBeInTheDocument()
    expect(screen.getByText(/52.5200° N, 13.4050° E/)).toBeInTheDocument()
    expect(screen.getByText('19°C')).toBeInTheDocument()
    expect(screen.getByText('Teilweise bewölkt')).toBeInTheDocument()
    expect(screen.getAllByText(/Sentinel-2 L2A/).length).toBeGreaterThan(0)

    // Tab-Wechsel zu Satellit testen
    const satelliteTab = screen.getByRole('button', { name: /Satellit/i })
    fireEvent.click(satelliteTab)
    expect(screen.getByText(/4.2%/)).toBeInTheDocument()

    const closeBtn = screen.getByRole('button', { name: i18n.t('ai.geo.close') })
    fireEvent.click(closeBtn)
    expect(onClose).toHaveBeenCalled()
  })
})
