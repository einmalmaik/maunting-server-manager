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
})
