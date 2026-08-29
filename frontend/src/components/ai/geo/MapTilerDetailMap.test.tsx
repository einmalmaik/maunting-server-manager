import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const mapHarness = vi.hoisted(() => ({
  configs: [] as Array<{ center: [number, number]; zoom: number }>,
  flyTos: [] as Array<{ center: [number, number]; zoom: number; duration: number }>,
  markerTargets: [] as Array<[number, number]>,
  dragPanEnables: 0,
  scrollZoomEnables: 0,
  touchZoomEnables: 0,
  dragRotateEnables: 0,
  trackpadZoomRates: [] as number[],
  wheelZoomRates: [] as number[],
  stops: 0,
  handlers: new Map<string, (event: unknown) => void>(),
  getMapTilerMapConfig: vi.fn(),
}))

vi.mock('@/api/ai', () => ({
  aiApi: { getMapTilerMapConfig: mapHarness.getMapTilerMapConfig },
}))

vi.mock('maplibre-gl', () => {
  class Map {
    private zoom: number
    dragPan = { enable: () => { mapHarness.dragPanEnables += 1 } }
    scrollZoom = {
      enable: () => { mapHarness.scrollZoomEnables += 1 },
      setZoomRate: (rate: number) => { mapHarness.trackpadZoomRates.push(rate) },
      setWheelZoomRate: (rate: number) => { mapHarness.wheelZoomRates.push(rate) },
    }
    touchZoomRotate = { enable: () => { mapHarness.touchZoomEnables += 1 } }
    dragRotate = { enable: () => { mapHarness.dragRotateEnables += 1 } }

    constructor(config: { center: [number, number]; zoom: number }) {
      mapHarness.configs.push(config)
      this.zoom = config.zoom
    }

    once(_event: string, callback: () => void) { callback() }
    on(event: string, callback: (value: unknown) => void) { mapHarness.handlers.set(event, callback) }
    setProjection() {}
    getStyle() { return { layers: [] } }
    getCanvas() { return { style: {} } }
    getZoom() { return this.zoom }
    flyTo(options: { center: [number, number]; zoom: number; duration: number }) {
      this.zoom = options.zoom
      mapHarness.flyTos.push(options)
    }
    stop() { mapHarness.stops += 1 }
    remove() {}
  }

  class Marker {
    setLngLat(target: [number, number]) {
      mapHarness.markerTargets.push(target)
      return this
    }
    addTo() { return this }
  }

  return { Map, Marker }
})

import { MapTilerDetailMap } from './MapTilerDetailMap'

describe('MapTilerDetailMap', () => {
  afterEach(() => {
    cleanup()
    mapHarness.configs.length = 0
    mapHarness.flyTos.length = 0
    mapHarness.markerTargets.length = 0
    mapHarness.dragPanEnables = 0
    mapHarness.scrollZoomEnables = 0
    mapHarness.touchZoomEnables = 0
    mapHarness.dragRotateEnables = 0
    mapHarness.trackpadZoomRates.length = 0
    mapHarness.wheelZoomRates.length = 0
    mapHarness.stops = 0
    mapHarness.handlers.clear()
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
    expect(mapHarness.dragPanEnables).toBe(1)
    expect(mapHarness.scrollZoomEnables).toBe(1)
    expect(mapHarness.touchZoomEnables).toBe(1)
    expect(mapHarness.dragRotateEnables).toBe(1)
    expect(mapHarness.trackpadZoomRates).toEqual([1 / 50])
    expect(mapHarness.wheelZoomRates).toEqual([1 / 120])
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

  it('zoomt für dasselbe Ziel deutlich näher, ohne den Globus neu aufzubauen', async () => {
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
        cameraCommandId="focus-1"
        onUnavailable={vi.fn()}
      />,
    )

    await waitFor(() => expect(mapHarness.configs).toHaveLength(1))
    await waitFor(() => expect(mapHarness.flyTos).toHaveLength(1))
    rerender(
      <MapTilerDetailMap
        latitude={52.52}
        longitude={13.405}
        locationName="Berlin"
        globe
        cameraMode="detail"
        cameraCommandId="detail-1"
        onUnavailable={vi.fn()}
      />,
    )

    expect(mapHarness.configs).toHaveLength(1)
    await waitFor(() => expect(mapHarness.flyTos[mapHarness.flyTos.length - 1]).toMatchObject({
      center: [13.405, 52.52],
      zoom: 13,
      duration: 700,
    }))
  })

  it('fokussiert einen neu gewählten Ort zunächst auf eine sichtbare Umgebung', async () => {
    mapHarness.getMapTilerMapConfig.mockResolvedValue({
      configured: true,
      style_url: 'https://maps.example.test/style.json',
    })

    render(
      <MapTilerDetailMap
        latitude={52.52}
        longitude={13.405}
        locationName="Berlin"
        globe
        cameraCommandId="berlin-focus"
        onUnavailable={vi.fn()}
      />,
    )

    await waitFor(() => expect(mapHarness.flyTos[0]).toMatchObject({ zoom: 6 }))
  })

  it('führt wiederholte Zoom-Befehle relativ bis zum maximalen Detailzoom aus', async () => {
    mapHarness.getMapTilerMapConfig.mockResolvedValue({
      configured: true,
      style_url: 'https://maps.example.test/style.json',
    })

    const { rerender } = render(
      <MapTilerDetailMap
        latitude={55.7558}
        longitude={37.6173}
        locationName="Moskau"
        globe
        cameraAction="zoom_in"
        cameraCommandId="zoom-1"
        onUnavailable={vi.fn()}
      />,
    )

    await waitFor(() => expect(mapHarness.flyTos[mapHarness.flyTos.length - 1]?.zoom).toBe(13))
    rerender(
      <MapTilerDetailMap
        latitude={55.7558}
        longitude={37.6173}
        locationName="Moskau"
        globe
        cameraAction="zoom_in"
        cameraCommandId="zoom-2"
        onUnavailable={vi.fn()}
      />,
    )
    await waitFor(() => expect(mapHarness.flyTos[mapHarness.flyTos.length - 1]?.zoom).toBe(17))
    rerender(
      <MapTilerDetailMap
        latitude={55.7558}
        longitude={37.6173}
        locationName="Moskau"
        globe
        cameraAction="zoom_in"
        cameraCommandId="zoom-3"
        onUnavailable={vi.fn()}
      />,
    )

    await waitFor(() => expect(mapHarness.flyTos[mapHarness.flyTos.length - 1]?.zoom).toBe(18))
    expect(mapHarness.configs).toHaveLength(1)
  })

  it('fliegt auf ein neues Ziel, ohne die Karte auf Weltzoom neu zu erstellen', async () => {
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
        cameraCommandId="berlin"
        onUnavailable={vi.fn()}
      />,
    )
    await waitFor(() => expect(mapHarness.flyTos).toHaveLength(1))

    rerender(
      <MapTilerDetailMap
        latitude={55.7558}
        longitude={37.6173}
        locationName="Moskau"
        globe
        cameraCommandId="moskau"
        onUnavailable={vi.fn()}
      />,
    )

    await waitFor(() => expect(mapHarness.flyTos[mapHarness.flyTos.length - 1]?.center).toEqual([37.6173, 55.7558]))
    expect(mapHarness.configs).toHaveLength(1)
  })

  it('fokussiert eine Sehenswürdigkeit direkt auf Straßenebene', async () => {
    mapHarness.getMapTilerMapConfig.mockResolvedValue({
      configured: true,
      style_url: 'https://maps.example.test/style.json',
    })

    render(
      <MapTilerDetailMap
        latitude={39.9163}
        longitude={116.3972}
        locationName="Verbotene Stadt, Peking"
        globe
        cameraMode="detail"
        cameraAction="focus_location"
        cameraCommandId="landmark-1"
        onUnavailable={vi.fn()}
      />,
    )

    await waitFor(() => expect(mapHarness.flyTos[mapHarness.flyTos.length - 1]).toMatchObject({
      center: [116.3972, 39.9163],
      zoom: 16,
    }))
  })

  it('lässt eine manuell bewegte Karte nicht durch einen verspäteten Fokus zurückspringen', async () => {
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
        cameraCommandId="focus-1"
        onUnavailable={vi.fn()}
      />,
    )
    await waitFor(() => expect(mapHarness.flyTos).toHaveLength(1))

    mapHarness.handlers.get('dragstart')?.({ originalEvent: new MouseEvent('mousedown') })
    expect(mapHarness.stops).toBeGreaterThan(0)

    rerender(
      <MapTilerDetailMap
        latitude={52.52}
        longitude={13.405}
        locationName="Berlin"
        globe
        cameraCommandId="focus-2"
        onUnavailable={vi.fn()}
      />,
    )

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(mapHarness.flyTos).toHaveLength(1)
  })
})
