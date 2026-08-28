import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const mapHarness = vi.hoisted(() => ({
  configs: [] as Array<{ center: [number, number] }>,
  getMapTilerMapConfig: vi.fn(),
}))

vi.mock('@/api/ai', () => ({
  aiApi: { getMapTilerMapConfig: mapHarness.getMapTilerMapConfig },
}))

vi.mock('maplibre-gl', () => {
  class Map {
    constructor(config: { center: [number, number] }) {
      mapHarness.configs.push(config)
    }

    once(_event: string, callback: () => void) { callback() }
    on() {}
    setProjection() {}
    getStyle() { return { layers: [] } }
    getCanvas() { return { style: {} } }
    flyTo() {}
    remove() {}
  }

  class Marker {
    setLngLat() { return this }
    addTo() { return this }
  }

  return { Map, Marker }
})

import { MapTilerDetailMap } from './MapTilerDetailMap'

describe('MapTilerDetailMap', () => {
  afterEach(() => {
    cleanup()
    mapHarness.configs.length = 0
    vi.clearAllMocks()
  })

  it('zentriert die Karte auf den geocodierten Zielpunkt, nicht auf eine Sentinel-BBox', async () => {
    mapHarness.getMapTilerMapConfig.mockResolvedValue({
      configured: true,
      style_url: 'https://maps.example.test/style.json',
    })

    const onReady = vi.fn()
    render(
      <MapTilerDetailMap
        latitude={52.52}
        longitude={13.405}
        locationName="Berlin"
        onUnavailable={vi.fn()}
        onReady={onReady}
      />,
    )

    await waitFor(() => expect(onReady).toHaveBeenCalledOnce())
    expect(mapHarness.configs[0]?.center).toEqual([13.405, 52.52])
  })

  it('meldet eine nicht konfigurierte Detailkarte genau einmal und lässt den Fallback zu', async () => {
    mapHarness.getMapTilerMapConfig.mockResolvedValue({ configured: false, style_url: null })
    const onUnavailable = vi.fn()

    render(
      <MapTilerDetailMap
        latitude={52.52}
        longitude={13.405}
        locationName="Berlin"
        onUnavailable={onUnavailable}
      />,
    )

    await waitFor(() => expect(onUnavailable).toHaveBeenCalledOnce())
    expect(mapHarness.configs).toHaveLength(0)
  })

  it('behält die Karte beim Wechsel des Kameramodus für dasselbe Ziel bei', async () => {
    mapHarness.getMapTilerMapConfig.mockResolvedValue({
      configured: true,
      style_url: 'https://maps.example.test/style.json',
    })

    const { rerender } = render(
      <MapTilerDetailMap
        latitude={52.52}
        longitude={13.405}
        locationName="Berlin"
        globe
        cameraMode="focus"
        onUnavailable={vi.fn()}
      />,
    )

    await waitFor(() => expect(mapHarness.configs).toHaveLength(1))
    rerender(
      <MapTilerDetailMap
        latitude={52.52}
        longitude={13.405}
        locationName="Berlin"
        globe
        cameraMode="detail"
        onUnavailable={vi.fn()}
      />,
    )

    expect(mapHarness.configs).toHaveLength(1)
  })
})
