import { useEffect, useRef, useState } from 'react'
import type { Map as MapLibreMap } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

import { aiApi } from '@/api/ai'

interface MapTilerDetailMapProps {
  latitude: number
  longitude: number
  locationName: string
  onUnavailable: () => void
  globe?: boolean
  zoom?: number
}

/**
 * Optionale, hochaufgeloeste Detailkarte. Sie wird nur geladen, nachdem der
 * Betreiber einen origin-beschraenkten MapTiler-Browser-Key eingerichtet hat.
 */
export function MapTilerDetailMap({ latitude, longitude, locationName, onUnavailable, globe = false, zoom = 8 }: MapTilerDetailMapProps) {
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
        center: [longitude, latitude],
        zoom,
        maxZoom: 18,
        renderWorldCopies: false,
        cooperativeGestures: true,
      })
      mapRef.current = map
      map.setProjection(globe ? { type: 'globe' } : { type: 'mercator' })
      new Marker({ color: '#38bdf8' }).setLngLat([longitude, latitude]).addTo(map)
      map.once('load', () => { if (!disposed) setReady(true) })
      map.on('error', () => { if (!disposed) onUnavailable() })
    }
    void initialise().catch(() => { if (!disposed) onUnavailable() })
    return () => { disposed = true; mapRef.current?.remove(); mapRef.current = null }
  }, [globe, latitude, longitude, onUnavailable, zoom])

  return <div className="absolute inset-0 z-[5] bg-surface-container-lowest" aria-label={`Hochauflösende Karte für ${locationName}`}>
    <div ref={elementRef} className="h-full w-full" />
    {!ready && <div className="pointer-events-none absolute inset-0 grid place-items-center bg-surface-container-lowest/70 text-sm text-on-surface-variant">Karte wird geladen</div>}
  </div>
}
