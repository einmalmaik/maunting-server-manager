import { describe, expect, it } from 'vitest'

import { applyGeoCameraCommand, normalizeRegionalAnalysis } from './regionalAnalysis'

describe('normalizeRegionalAnalysis', () => {
  it('überführt eine alte Koordinatenform in den stabilen Kartenvertrag', () => {
    expect(normalizeRegionalAnalysis({
      region: 'Berlin',
      coordinates: { lat: 52.52, lon: 13.405 },
    })).toMatchObject({
      status: 'success',
      location: 'Berlin',
      coordinates: { latitude: 52.52, longitude: 13.405 },
    })
  })

  it('lehnt fehlende oder unzulässige Koordinaten ab', () => {
    expect(normalizeRegionalAnalysis({ coordinates: { latitude: 52.52 } })).toBeNull()
    expect(normalizeRegionalAnalysis({ coordinates: { latitude: 99, longitude: 13.405 } })).toBeNull()
  })

  it('übernimmt nur vollständige optionale Wetter- und Satellitendaten', () => {
    const analysis = normalizeRegionalAnalysis({
      location: 'Berlin',
      coordinates: { latitude: 52.52, longitude: 13.405 },
      weather: { temperature: 20 },
      satellite: { available: true, scenes: [{ id: 'unvollständig' }] },
    })

    expect(analysis?.weather).toBeUndefined()
    expect(analysis?.satellite?.scenes).toEqual([])
  })

  it('übernimmt nur den definierten Verkehrs- und öffentlichen Beitragsvertrag', () => {
    const analysis = normalizeRegionalAnalysis({
      location: 'Berlin',
      coordinates: { latitude: 52.52, longitude: 13.405 },
      traffic: {
        status: 'available',
        current_speed_kmh: 32.5,
        road_closure: false,
      },
      public_posts: {
        status: 'available',
        untrusted: true,
        reddit: [{ title: 'Baustelle', snippet: 'Abfahrt gesperrt', url: 'https://example.invalid/reddit' }],
        bluesky: [{ author: '@verkehr.example', text: 'Stau am Ring', url: 'javascript:alert(1)' }],
      },
    })

    expect(analysis?.traffic).toEqual({
      status: 'available',
      current_speed_kmh: 32.5,
      free_flow_speed_kmh: undefined,
      current_travel_time_seconds: undefined,
      free_flow_travel_time_seconds: undefined,
      confidence: undefined,
      road_closure: false,
    })
    expect(analysis?.public_posts).toEqual({
      status: 'available',
      untrusted: true,
      reddit: [{ title: 'Baustelle', snippet: 'Abfahrt gesperrt', url: 'https://example.invalid/reddit' }],
      bluesky: [],
    })
  })

  it('erhält den Kurztext regionaler Nachrichten aus dem Websuchvertrag', () => {
    const analysis = normalizeRegionalAnalysis({
      location: 'Moskau',
      coordinates: { latitude: 55.7558, longitude: 37.6173 },
      news: [{ title: 'Lagebericht', url: 'https://example.invalid', snippet: 'Der Kurztext bleibt sichtbar.' }],
    })

    expect(analysis?.news?.[0]?.snippet).toBe('Der Kurztext bleibt sichtbar.')
  })

  it('wendet einen einmaligen Kamerabefehl an, ohne die Regionaldaten zu ersetzen', () => {
    const analysis = normalizeRegionalAnalysis({
      location: 'Moskau',
      coordinates: { latitude: 55.7558, longitude: 37.6173 },
      weather: {
        temperature_celsius: 20,
        apparent_temperature_celsius: 20,
        humidity_percent: 55,
        precipitation_mm: 0,
        wind_speed_kmh: 4,
        condition: 'partly_cloudy',
      },
    })
    const changed = applyGeoCameraCommand(analysis, { action: 'zoom_in', command_id: 'camera-1' })

    expect(changed?.weather?.temperature_celsius).toBe(20)
    expect(changed?.camera).toEqual({ mode: 'focus', action: 'zoom_in', command_id: 'camera-1' })
    expect(applyGeoCameraCommand(analysis, { action: 'zoom_in' })).toBe(analysis)
  })

  it('fokussiert eine Sehenswürdigkeit, ohne Wetter und Lagebild neu zu laden', () => {
    const analysis = normalizeRegionalAnalysis({
      location: 'Peking',
      country: 'China',
      coordinates: { latitude: 39.9042, longitude: 116.4074 },
      weather: {
        temperature_celsius: 26,
        apparent_temperature_celsius: 27,
        humidity_percent: 52,
        precipitation_mm: 0,
        wind_speed_kmh: 8,
        condition: 'clear',
      },
      news: [{ title: 'Lagebericht', url: 'https://example.invalid/news', snippet: 'Bleibt erhalten.' }],
    })

    const changed = applyGeoCameraCommand(analysis, {
      action: 'focus_location',
      command_id: 'landmark-1',
      location: 'Verbotene Stadt, Peking',
      country: 'China',
      coordinates: { latitude: 39.9163, longitude: 116.3972 },
    })

    expect(changed).toMatchObject({
      location: 'Verbotene Stadt, Peking',
      country: 'China',
      coordinates: { latitude: 39.9163, longitude: 116.3972 },
      camera: { mode: 'detail', action: 'focus_location', command_id: 'landmark-1' },
      sights: [
        {
          latitude: 39.9163,
          longitude: 116.3972,
          name: 'Verbotene Stadt, Peking',
          commandId: 'landmark-1',
        },
      ],
    })
    expect(changed?.weather).toBe(analysis?.weather)
    expect(changed?.news).toBe(analysis?.news)

    const secondSight = applyGeoCameraCommand(changed, {
      action: 'focus_location',
      command_id: 'landmark-2',
      location: 'Sommerpalast, Peking',
      country: 'China',
      coordinates: { latitude: 39.9998, longitude: 116.2755 },
    })

    expect(secondSight?.sights).toHaveLength(2)
    expect(secondSight?.sights?.[1]).toMatchObject({
      name: 'Sommerpalast, Peking',
      commandId: 'landmark-2',
    })
  })
})
