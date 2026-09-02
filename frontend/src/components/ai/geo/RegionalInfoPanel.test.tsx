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
    traffic: {
      status: 'available',
      current_speed_kmh: 28,
      free_flow_speed_kmh: 50,
      current_travel_time_seconds: 180,
      free_flow_travel_time_seconds: 120,
      confidence: 89,
      road_closure: true,
    },
    public_posts: {
      status: 'available',
      untrusted: true,
      reddit: [{ title: 'Baustelle am Ring', snippet: 'Eine Ausfahrt ist gesperrt.', url: 'https://example.invalid/reddit' }],
      bluesky: [{ author: '@verkehr.example', text: 'Stockender Verkehr auf dem Ring.', url: 'https://example.invalid/bluesky' }],
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
    const satelliteTab = screen.getByRole('tab', { name: /Satellit/i })
    fireEvent.click(satelliteTab)
    expect(screen.getByText(/4.2%/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: /Soziale Medien/i }))
    expect(screen.getByText('Baustelle am Ring')).toBeInTheDocument()
    expect(screen.getByText('@verkehr.example')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('ai.geo.publicPostsNotice'))).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: i18n.t('ai.geo.openPublicPost') })).toHaveLength(2)
    screen.getAllByRole('link', { name: i18n.t('ai.geo.openPublicPost') }).forEach((link) => {
      expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    })

    fireEvent.click(screen.getByRole('tab', { name: /Verkehr/i }))
    expect(screen.getByText('28')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('ai.geo.roadClosure'))).toBeInTheDocument()

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

    fireEvent.click(screen.getByRole('tab', { name: /Nachrichten/i }))
    expect(screen.getByText('Wichtige Meldung')).toBeInTheDocument()
    expect(screen.getByText('Lage geprüft.')).toBeInTheDocument()
    expect(screen.queryByText(/<strong>|<em>/)).not.toBeInTheDocument()
  })

  it('zeigt die Beschreibung aus regionalen Suchtreffern', () => {
    render(
      <RegionalInfoPanel
        data={{
          ...mockData,
          news: [{
            title: 'Meldung aus Moskau',
            description: 'Dieser Kurztext stammt aus dem regionalen Suchtreffer.',
            url: 'https://example.test/moskau',
          }],
        }}
        onClose={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('tab', { name: /Nachrichten/i }))
    expect(screen.getByText('Dieser Kurztext stammt aus dem regionalen Suchtreffer.')).toBeInTheDocument()
  })

  it('stellt die Bereiche als zugängliche Tab-Navigation bereit', () => {
    render(<RegionalInfoPanel data={mockData} onClose={vi.fn()} />)

    const tablist = screen.getByRole('tablist', { name: i18n.t('ai.geo.tabsLabel') })
    expect(tablist).toBeInTheDocument()

    const overview = screen.getByRole('tab', { name: /Übersicht/i })
    const satellite = screen.getByRole('tab', { name: /Satellit/i })
    expect(overview).toHaveAttribute('aria-selected', 'true')

    overview.focus()
    fireEvent.keyDown(overview, { key: 'ArrowRight' })
    expect(satellite).toHaveAttribute('aria-selected', 'true')
    expect(satellite).toHaveFocus()
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', satellite.id)
  })

  it('unterscheidet nicht eingerichtete von derzeit nicht verfügbaren Verkehrsdaten', () => {
    const onClose = vi.fn()
    const { rerender } = render(<RegionalInfoPanel data={{ ...mockData, traffic: { status: 'not_configured' } }} onClose={onClose} />)

    fireEvent.click(screen.getByRole('tab', { name: /Verkehr/i }))
    expect(screen.getByText(i18n.t('ai.geo.trafficNotConfiguredTitle'))).toBeInTheDocument()

    rerender(<RegionalInfoPanel data={{ ...mockData, traffic: { status: 'unavailable' } }} onClose={onClose} />)
    expect(screen.getByText(i18n.t('ai.geo.trafficUnavailableTitle'))).toBeInTheDocument()
  })
})
