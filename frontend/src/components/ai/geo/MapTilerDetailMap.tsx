import { useEffect, useRef, useState } from 'react'
import type { Map as MapLibreMap, Marker as MapLibreMarker } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { useTranslation } from 'react-i18next'

import { aiApi } from '@/api/ai'

type CameraMode = 'overview' | 'focus' | 'detail'
type CameraAction = 'zoom_in' | 'zoom_out' | 'overview' | 'focus_location'

interface MapTilerDetailMapProps {
  latitude: number
  longitude: number
  locationName: string
  onUnavailable: () => void
  onReady?: () => void
  globe?: boolean
  zoom?: number
  cameraMode?: CameraMode
  cameraAction?: CameraAction
  cameraCommandId?: string
}

const MAX_AI_ZOOM = 18
const MAX_GLOBE_FOCUS_ZOOM = 10
const DETAIL_ZOOM = 13
const LANDMARK_ZOOM = 16
const CAMERA_DURATION_MS = 700
// Faster zoom rates for smoother experience
const TRACKPAD_ZOOM_RATE = 0.2 // approx 1/5
const WHEEL_ZOOM_RATE = 0.15 // approx 1/6.7

function isManualMapEvent(value: unknown): boolean {
  return typeof value === 'object' && value !== null && 'originalEvent' in value && Boolean(
    (value as { originalEvent?: unknown }).originalEvent,
  )
}

/**
 * Optionale, hochaufgeloeste Detailkarte. Sie wird nur geladen, nachdem der
 * Betreiber einen origin-beschraenkten MapTiler-Browser-Key eingerichtet hat.
 */
export function MapTilerDetailMap({
  latitude,
  longitude,
  locationName,
  onUnavailable,
  onReady,
  globe = false,
  zoom = 8,
  cameraMode = 'focus',
  cameraAction,
  cameraCommandId,
}: MapTilerDetailMapProps) {
  const { t } = useTranslation()
  const elementRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const markerRef = useRef<MapLibreMarker | null>(null)
  const lastTargetRef = useRef<string | null>(null)
  const lastCommandRef = useRef<string | null>(null)
  const manualCameraControlRef = useRef(false)
  const latestViewRef = useRef({ latitude, longitude, zoom })
  const onUnavailableRef = useRef(onUnavailable)
  const onReadyRef = useRef(onReady)
  const [ready, setReady] = useState(false)

  latestViewRef.current = { latitude, longitude, zoom }

  useEffect(() => {
    onUnavailableRef.current = onUnavailable
    onReadyRef.current = onReady
  }, [onReady, onUnavailable])

  // Die MapLibre-Instanz gehört zum sichtbaren Kartenfeld, nicht zu einem Ort.
  // Ziel- und Zoomwechsel werden darunter auf derselben Instanz ausgeführt.
  useEffect(() => {
    let disposed = false
    let unavailableReported = false
    const unavailable = () => {
      if (disposed || unavailableReported) return
      unavailableReported = true
      onUnavailableRef.current()
    }
    const initialise = async () => {
      const initial = latestViewRef.current
      if (!Number.isFinite(initial.latitude) || !Number.isFinite(initial.longitude)) {
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
      const view = latestViewRef.current
      const map = new MapLibre({
        container: elementRef.current,
        style: config.style_url,
        center: [view.longitude, view.latitude],
        zoom: globe ? 0.65 : view.zoom,
        maxZoom: MAX_AI_ZOOM,
        renderWorldCopies: false,
        cooperativeGestures: false,
      })
      // MapLibre ist bereits interaktiv. Die explizite Aktivierung schützt
      // diesen Vertrag vor abweichenden Style-/Runtime-Vorgaben; höhere Raten
      // verhindern Dutzende Mausradschritte nach einem Detailflug.
      map.dragPan.enable()
      map.dragRotate.enable()
      map.scrollZoom.enable()
      map.scrollZoom.setZoomRate(TRACKPAD_ZOOM_RATE)
      map.scrollZoom.setWheelZoomRate(WHEEL_ZOOM_RATE)
      map.touchZoomRotate.enable()
      mapRef.current = map
      markerRef.current = new Marker({ color: '#38bdf8' })
        .setLngLat([view.longitude, view.latitude])
        .addTo(map)
      let styleReady = false
      map.once('style.load', () => {
        if (disposed) return
        try {
          map.setProjection(globe ? { type: 'globe' } : { type: 'mercator' })
          if (globe) {
            const backgroundLayer = map.getStyle().layers?.find((layer) => layer.type === 'background')
            if (backgroundLayer) map.setPaintProperty(backgroundLayer.id, 'background-opacity', 0)
            map.getCanvas().style.backgroundColor = 'transparent'
            map.getCanvas().style.filter = 'drop-shadow(0 0 10px rgba(56, 189, 248, 0.68)) drop-shadow(0 0 24px rgba(14, 165, 233, 0.3))'
          }
        } catch {
          // Die optionale Kartenveredelung darf die Echtzeitansicht nicht
          // gefährden. Die Karte bleibt mit ihrer Standardprojektion sichtbar.
        }
        styleReady = true
        setReady(true)
        onReadyRef.current?.()
      })
      const markManualCameraControl = (event: unknown) => {
        if (!isManualMapEvent(event)) return
        manualCameraControlRef.current = true
        map.stop()
      }
      map.on('dragstart', markManualCameraControl)
      map.on('dragend', () => {
        // User finished dragging; allow AI updates again.
        manualCameraControlRef.current = false
      })
      map.on('zoomstart', markManualCameraControl)
      map.on('zoomend', () => {
        manualCameraControlRef.current = false
      })
      map.on('rotatestart', markManualCameraControl)
      map.on('rotateend', () => {
        manualCameraControlRef.current = false
      })
      map.on('error', () => {
        if (!disposed && !styleReady) unavailable()
      })
    }
    setReady(false)
    void initialise().catch(unavailable)
    return () => {
      disposed = true
      markerRef.current = null
      mapRef.current?.remove()
      mapRef.current = null
      lastTargetRef.current = null
      lastCommandRef.current = null
      manualCameraControlRef.current = false
    }
  }, [globe])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const target = `${longitude}:${latitude}`
    const command = cameraCommandId
      ? `id:${cameraCommandId}`
      : `legacy:${target}:${cameraAction ?? cameraMode}`
    if (lastCommandRef.current === command) return

    const sameTarget = lastTargetRef.current === target
    // Ein verspätetes, allgemeines Fokus-Ergebnis darf eine manuell bewegte
    // Karte nicht zurücksetzen. Eine klare Steueraktion oder ein anderer Ort
    // bleibt dagegen ein neuer, ausdrücklich sichtbarer Auftrag.
    if (manualCameraControlRef.current && sameTarget && !cameraAction) return
    if (!sameTarget) manualCameraControlRef.current = false
    const currentZoom = map.getZoom()
    const focusZoom = globe ? Math.max(5, Math.min(MAX_GLOBE_FOCUS_ZOOM, zoom)) : zoom
    let nextZoom = focusZoom
    if (cameraAction === 'focus_location') {
      nextZoom = Math.min(MAX_AI_ZOOM, Math.max(LANDMARK_ZOOM, currentZoom))
    } else if (cameraAction === 'zoom_in' || cameraMode === 'detail') {
      nextZoom = Math.min(MAX_AI_ZOOM, Math.max(DETAIL_ZOOM, currentZoom + 4))
    } else if (cameraAction === 'zoom_out') {
      nextZoom = Math.max(1.2, currentZoom - 4)
    } else if (cameraAction === 'overview' || cameraMode === 'overview') {
      nextZoom = 1.2
    } else if (sameTarget) {
      nextZoom = Math.max(currentZoom, focusZoom)
    }

    markerRef.current?.setLngLat([longitude, latitude])
    lastTargetRef.current = target
    lastCommandRef.current = command
    if (sameTarget && Math.abs(nextZoom - currentZoom) < 0.01) return
    map.stop()
    map.flyTo({
      center: [longitude, latitude],
      zoom: nextZoom,
      duration: CAMERA_DURATION_MS,
    })
  }, [cameraAction, cameraCommandId, cameraMode, globe, latitude, longitude, ready, zoom])

  return <div className="absolute inset-0 z-[5] bg-transparent" aria-label={`Interaktive Karte für ${locationName}`}>
    <div ref={elementRef} className="h-full w-full cursor-grab active:cursor-grabbing" />
    {!ready && <div className="pointer-events-none absolute inset-0 grid place-items-center bg-surface-container-lowest/70 text-sm text-on-surface-variant">{t('ai.geo.mapLoading', 'Karte wird geladen')}</div>}
  </div>
}
