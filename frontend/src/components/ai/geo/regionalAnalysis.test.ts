import { describe, expect, it } from 'vitest'

import { normalizeRegionalAnalysis } from './regionalAnalysis'

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
})
