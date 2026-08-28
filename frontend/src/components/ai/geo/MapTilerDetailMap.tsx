import { useEffect, useRef, useState } from 'react'
import type { Map as MapLibreMap } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

import { aiApi } from '@/api/ai'

interface MapTilerDetailMapProps {
  latitude: number
  longitude: number
  centerLatitude?: number
  centerLongitude?: number
  locationName: string
  onUnavailable: () => void
  globe?: boolean
  zoom?: number
  cameraMode?: 'overview' | 'focus' | 'detail'
}

/**
 * Optionale, hochaufgeloeste Detailkarte. Sie wird nur geladen, nachdem der
 * Betreiber einen origin-beschraenkten MapTiler-Browser-Key eingerichtet hat.
 */
export function MapTilerDetailMap({ latitude, longitude, centerLatitude = latitude, centerLongitude = longitude, locationName, onUnavailable, globe = false, zoom = 8, cameraMode = 'focus' }: MapTilerDetailMapProps) {
  const elementRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let disposed = false
    const initialise = async () => {
      const config = await aiApi.getMapTilerMapConfig()
      if (disposed) return
      if (!config.configured || !config.style_url || !elementRef.current) {
        onUnavailable()
        return
      }
      const { Map: MapLibre, Marker } = await import('maplibre-gl')
      if (disposed || !elementRef.current) return
      const map = new MapLibre({
        container: elementRef.current,
        style: config.style_url,
        center: globe ? [0, 20] : [centerLongitude, centerLatitude],
        zoom: globe ? 0.65 : zoom,
        maxZoom: 18,
        renderWorldCopies: false,
        cooperativeGestures: false,
      })
      mapRef.current = map
      new Marker({ color: '#38bdf8' }).setLngLat([longitude, latitude]).addTo(map)
      let styleReady = false
      map.once('style.load', () => {
        if (disposed) return
        map.setProjection(globe ? { type: 'globe' } : { type: 'mercator' })
        if (globe) {
          const backgroundLayer = map.getStyle().layers?.find((layer) => layer.type === 'background')
          if (backgroundLayer) map.setPaintProperty(backgroundLayer.id, 'background-opacity', 0)
          map.getCanvas().style.backgroundColor = 'transparent'
          map.flyTo({
            center: [centerLongitude, centerLatitude],
            zoom: cameraMode === 'overview' ? 1.2 : cameraMode === 'detail' ? 7 : Math.max(2.2, Math.min(4.5, zoom)),
            duration: 1800,
            essential: true,
          })
        }
        styleReady = true
        setReady(true)
      })
      // Ein einzelner fehlender Bildtile darf nicht den kompletten Globus
      // abschalten. Nur ein Fehler vor dem geladenen Stil bedeutet, dass die
      // optionale Karte wirklich nicht verfügbar ist.
      map.on('error', () => {
        if (!disposed && !styleReady) onUnavailable()
      })
    }
    void initialise().catch(() => { if (!disposed) onUnavailable() })
    return () => { disposed = true; mapRef.current?.remove(); mapRef.current = null }
  }, [cameraMode, centerLatitude, centerLongitude, globe, latitude, longitude, onUnavailable, zoom])

  // In der Globusansicht bleibt der Container transparent: darunter zeichnet
  // GlobeViewer den einen gemeinsamen Canvas-Weltraum mit Sternen und Sonne.
  return <div className="absolute inset-0 z-[5] bg-transparent" aria-label={`Hochauflösende Karte für ${locationName}`}>
    <div ref={elementRef} className="h-full w-full" />
    {!ready && <div className="pointer-events-none absolute inset-0 grid place-items-center bg-surface-container-lowest/70 text-sm text-on-surface-variant">Karte wird geladen</div>}
  </div>
}
