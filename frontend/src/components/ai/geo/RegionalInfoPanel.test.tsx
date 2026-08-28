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

    fireEvent.click(screen.getByRole('button', { name: /Soziale Medien/i }))
    expect(screen.getByText(i18n.t('ai.geo.socialUnavailableTitle'))).toBeInTheDocument()
    expect(screen.queryByText(/Normal \/ Stabil/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Verkehr/i }))
    expect(screen.getByText(i18n.t('ai.geo.trafficUnavailableTitle'))).toBeInTheDocument()

    const closeBtn = screen.getByRole('button', { name: i18n.t('ai.geo.close') })
    fireEvent.click(closeBtn)
    expect(onClose).toHaveBeenCalled()
  })

  it('wechselt von der Ladeansicht zu Daten ohne Hook-Reihenfolgefehler', () => {
    const onClose = vi.fn()
    const { rerender } = render(<RegionalInfoPanel data={null} loading onClose={onClose} />)

    expect(screen.getByLabelText(i18n.t('ai.geo.panelTitle', 'Regionale Analyse'))).toBeInTheDocument()

    rerender(<RegionalInfoPanel data={mockData} loading={false} onClose={onClose} />)

    expect(screen.getByText('Berlin')).toBeInTheDocument()
  })

  it('bleibt bei unvollständigen Echtzeitdaten im sicheren Ladezustand', () => {
    const incompleteData = { location: 'Berlin', coordinates: { latitude: 52.52 } } as unknown as AiRegionalAnalysis

    render(<RegionalInfoPanel data={incompleteData} loading onClose={vi.fn()} />)

    expect(screen.getByLabelText(i18n.t('ai.geo.panelTitle', 'Regionale Analyse'))).toBeInTheDocument()
    expect(screen.queryByText('Berlin')).not.toBeInTheDocument()
  })

  it('zeigt Nachrichten als bereinigten Text statt XML-Markup', () => {
    render(
      <RegionalInfoPanel
        data={mockData}
        onClose={vi.fn()}
        news={[{
          id: 'news-1',
          title: '<strong>Wichtige Meldung</strong>',
          snippet: 'Lage <em>geprüft</em>.',
          source: 'Regional <b>News</b>',
          timeAgo: 'vor 10 Min.',
          category: 'Lokal',
          url: 'https://example.test/news-1',
        }]}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /Nachrichten/i }))
    expect(screen.getByText('Wichtige Meldung')).toBeInTheDocument()
    expect(screen.getByText('Lage geprüft.')).toBeInTheDocument()
    expect(screen.queryByText(/<strong>|<em>/)).not.toBeInTheDocument()
  })
})
