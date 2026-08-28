import { useEffect, useRef, useState } from 'react'
import type { Map as MapLibreMap } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { useTranslation } from 'react-i18next'

import { aiApi } from '@/api/ai'

interface MapTilerDetailMapProps {
  latitude: number
  longitude: number
  locationName: string
  onUnavailable: () => void
  onReady?: () => void
  globe?: boolean
  zoom?: number
  cameraMode?: 'overview' | 'focus' | 'detail'
}

/**
 * Optionale, hochaufgeloeste Detailkarte. Sie wird nur geladen, nachdem der
 * Betreiber einen origin-beschraenkten MapTiler-Browser-Key eingerichtet hat.
 */
export function MapTilerDetailMap({ latitude, longitude, locationName, onUnavailable, onReady, globe = false, zoom = 8, cameraMode = 'focus' }: MapTilerDetailMapProps) {
  const { t } = useTranslation()
  const elementRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const manuallyControlledTargetRef = useRef<string | null>(null)
  const onUnavailableRef = useRef(onUnavailable)
  const onReadyRef = useRef(onReady)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    onUnavailableRef.current = onUnavailable
    onReadyRef.current = onReady
  }, [onReady, onUnavailable])

  useEffect(() => {
    const target = `${longitude}:${latitude}`
    // Nur ein neuer, serverseitig bestätigter Zielpunkt darf wieder eine
    // Kamerafahrt auslösen. Änderungen am Kameramodus bleiben für denselben
    // Zielpunkt folgenlos, sobald der Nutzer die Karte gesteuert hat.
    if (manuallyControlledTargetRef.current !== target) {
      manuallyControlledTargetRef.current = null
    }
    let disposed = false
    let unavailableReported = false
    const unavailable = () => {
      if (disposed || unavailableReported) return
      unavailableReported = true
      onUnavailableRef.current()
    }
    const initialise = async () => {
      if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
        unavailable()
        return
      }
      const config = await aiApi.getMapTilerMapConfig()
      if (disposed) return
      if (!config.configured || !config.style_url || !elementRef.current) {
        unavailable()
        return
      }
      const { Map: MapLibre, Marker } = await import('maplibre-gl')
      if (disposed || !elementRef.current) return
      const map = new MapLibre({
        container: elementRef.current,
        style: config.style_url,
        // Die Kamera folgt ausschließlich dem geocodierten Zielpunkt. Die
        // Sentinel-Bounding-Box beschreibt nur den Analysebereich und darf
        // niemals als abweichende Kartenkoordinate verwendet werden.
        center: [longitude, latitude],
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
        try {
          map.setProjection(globe ? { type: 'globe' } : { type: 'mercator' })
          if (globe) {
            const backgroundLayer = map.getStyle().layers?.find((layer) => layer.type === 'background')
            if (backgroundLayer) map.setPaintProperty(backgroundLayer.id, 'background-opacity', 0)
            map.getCanvas().style.backgroundColor = 'transparent'
            // Die Kartendaten bleiben unverändert; der Schatten folgt allein
            // der transparenten Kugelkontur und ergänzt den gemeinsamen
            // Weltraum-Hintergrund um das bisherige atmosphärische Leuchten.
            map.getCanvas().style.filter = 'drop-shadow(0 0 10px rgba(56, 189, 248, 0.68)) drop-shadow(0 0 24px rgba(14, 165, 233, 0.3))'
            map.flyTo({
              center: [longitude, latitude],
              zoom: cameraMode === 'overview' ? 1.2 : cameraMode === 'detail' ? 7 : Math.max(2.2, Math.min(4.5, zoom)),
              duration: 1800,
              essential: true,
            })
          }
        } catch {
          // Die optionale Kartenveredelung darf die Echtzeitansicht nicht
          // gefährden. Die Karte bleibt mit ihrer Standardprojektion sichtbar.
        }
        styleReady = true
        setReady(true)
        onReadyRef.current?.()
      })
      const markManualCameraControl = () => {
        manuallyControlledTargetRef.current = target
      }
      map.on('dragstart', markManualCameraControl)
      map.on('zoomstart', markManualCameraControl)
      map.on('rotatestart', markManualCameraControl)
      // Ein einzelner fehlender Bildtile darf nicht den kompletten Globus
      // abschalten. Nur ein Fehler vor dem geladenen Stil bedeutet, dass die
      // optionale Karte wirklich nicht verfügbar ist.
      map.on('error', () => {
        if (!disposed && !styleReady) unavailable()
      })
    }
    void initialise().catch(unavailable)
    return () => { disposed = true; mapRef.current?.remove(); mapRef.current = null }
  }, [globe, latitude, longitude])

  // In der Globusansicht bleibt der Container transparent: darunter zeichnet
  // GlobeViewer ausschließlich den gemeinsamen Weltraum mit Sternen und Sonne.
  return <div className="absolute inset-0 z-[5] bg-transparent" aria-label={`Interaktive Karte für ${locationName}`}>
    <div ref={elementRef} className="h-full w-full" />
    {!ready && <div className="pointer-events-none absolute inset-0 grid place-items-center bg-surface-container-lowest/70 text-sm text-on-surface-variant">{t('ai.geo.mapLoading', 'Karte wird geladen')}</div>}
  </div>
}
