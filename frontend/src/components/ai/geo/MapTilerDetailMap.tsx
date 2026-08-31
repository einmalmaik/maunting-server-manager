import { useEffect, useRef, useState } from 'react'
import type { Map as MapLibreMap, Marker as MapLibreMarker } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { Minus, Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi } from '@/api/ai'

type CameraMode = 'overview' | 'focus' | 'detail'
type CameraAction = 'zoom_in' | 'zoom_out' | 'overview' | 'focus_location'

interface Sight {
  latitude: number
  longitude: number
  name: string
  summary?: string
  commandId: string
}

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
  sights?: Sight[]
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
const SIGHT_DWELL_MS = 10_000

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
  sights,
}: MapTilerDetailMapProps) {
  const { t } = useTranslation()
  const elementRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const markerRef = useRef<MapLibreMarker | null>(null)
  const markersRef = useRef<MapLibreMarker[]>([])
  const lastTargetRef = useRef<string | null>(null)
  const lastCommandRef = useRef<string | null>(null)
  const manualCameraControlRef = useRef(false)
  const dwellUntilRef = useRef<number>(0)
  const tourTimerRef = useRef<number | null>(null)
  const processedSightCommandsRef = useRef<Set<string>>(new Set())
  const pendingQueueRef = useRef<Array<{ latitude: number; longitude: number; locationName: string; command: string; target: string; zoom: number }>>([])
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
        // Do not call map.stop() here. MapLibre owns the active drag gesture;
        // stopping the map from dragstart aborts the gesture and makes panning
        // advance in tiny frame-sized steps instead of following the pointer.
        manualCameraControlRef.current = true
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
      map.on('error', (event) => {
        const err = (event as { error?: { status?: number; message?: string } })?.error
        const status = err?.status
        const msg = String(err?.message || '')
        if (
          status === 401 ||
          status === 403 ||
          msg.includes('401') ||
          msg.includes('403') ||
          msg.includes('Forbidden') ||
          msg.includes('Unauthorized')
        ) {
          if (!disposed && !styleReady) unavailable()
        }
      })
    }
    setReady(false)
    void initialise().catch(unavailable)
    return () => {
      disposed = true
      if (tourTimerRef.current) {
        window.clearTimeout(tourTimerRef.current)
        tourTimerRef.current = null
      }
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
    void (async () => {
      for (const m of markersRef.current) m.remove()
      markersRef.current = []
      if (!sights || sights.length === 0) return
      const { Marker, Popup } = await import('maplibre-gl')
      for (const s of sights) {
        const el = document.createElement('div')
        el.setAttribute('role', 'button')
        el.setAttribute('aria-label', s.name)
        // Kein el.title, um den nativen Browser-Tooltip zu verhindern
        el.className = 'msm-sight-marker'
        el.style.cssText =
          'width:18px;height:18px;border-radius:50%;background:#38bdf8;border:2px solid #ffffff;box-shadow:0 0 10px rgba(56,189,248,0.8),0 2px 8px rgba(0,0,0,0.4);cursor:pointer;transition:transform 0.2s cubic-bezier(0.16, 1, 0.3, 1),box-shadow 0.2s ease;'

        el.addEventListener('mouseenter', () => {
          el.style.transform = 'scale(1.3)'
          el.style.boxShadow = '0 0 18px rgba(56,189,248,1), 0 4px 12px rgba(0,0,0,0.5)'
        })
        el.addEventListener('mouseleave', () => {
          el.style.transform = 'scale(1)'
          el.style.boxShadow = '0 0 10px rgba(56,189,248,0.8),0 2px 8px rgba(0,0,0,0.4)'
        })

        const popupHtml = `<div class="pointer-events-none rounded-xl border border-outline-variant/60 bg-surface-container-high/95 px-3.5 py-2 text-xs font-medium leading-tight text-on-surface shadow-2xl backdrop-blur-md"><div class="flex items-center gap-1.5 font-semibold text-primary"><span class="inline-block h-2 w-2 shrink-0 rounded-full bg-primary animate-pulse"></span><span>${escapeHtml(s.name)}</span></div>${s.summary ? `<div class="mt-1 text-[11px] font-normal text-on-surface-variant leading-snug">${escapeHtml(s.summary)}</div>` : ''}</div>`
        const popup = new Popup({ offset: 14, closeButton: false, className: 'msm-sight-popup', maxWidth: '280px' }).setHTML(popupHtml)
        const m = new Marker({ element: el }).setLngLat([s.longitude, s.latitude]).addTo(map)
        el.addEventListener('mouseenter', () => m.setPopup(popup).addTo(map))
        el.addEventListener('mouseleave', () => m.getPopup()?.remove())
        el.addEventListener('click', () => m.togglePopup())
        markersRef.current.push(m)
      }
    })()
  }, [sights, ready])

  function escapeHtml(value: string): string {
    return value.split('&').join('&amp;').split('<').join('&lt;').split('>').join('&gt;').split('"').join('&quot;')
  }

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return

    const processQueue = () => {
      const currentMap = mapRef.current
      if (!currentMap) return
      const p = pendingQueueRef.current.shift()
      if (!p) return

      if (lastCommandRef.current !== p.command) {
        lastCommandRef.current = p.command
        lastTargetRef.current = p.target
        manualCameraControlRef.current = false
        markerRef.current?.setLngLat([p.longitude, p.latitude])
        const cz = currentMap.getZoom()
        const nz = Math.min(MAX_AI_ZOOM, Math.max(LANDMARK_ZOOM, cz))
        currentMap.stop()
        currentMap.flyTo({ center: [p.longitude, p.latitude], zoom: nz, duration: CAMERA_DURATION_MS })
        dwellUntilRef.current = Date.now() + SIGHT_DWELL_MS
      }

      if (pendingQueueRef.current.length > 0) {
        if (tourTimerRef.current) window.clearTimeout(tourTimerRef.current)
        tourTimerRef.current = window.setTimeout(processQueue, SIGHT_DWELL_MS)
      }
    }

    // Falls sights übergeben wurden, alle neuen Stationen in die Tour einreihen
    if (sights && sights.length > 0) {
      let addedToTour = false
      for (const s of sights) {
        if (!processedSightCommandsRef.current.has(s.commandId)) {
          processedSightCommandsRef.current.add(s.commandId)
          const sightCmd = `id:${s.commandId}`
          const sightTarget = `${s.longitude}:${s.latitude}`
          if (sightCmd !== lastCommandRef.current) {
            pendingQueueRef.current.push({
              latitude: s.latitude,
              longitude: s.longitude,
              locationName: s.name,
              command: sightCmd,
              target: sightTarget,
              zoom: LANDMARK_ZOOM,
            })
            addedToTour = true
          }
        }
      }

      if (addedToTour && pendingQueueRef.current.length > 0 && Date.now() >= dwellUntilRef.current) {
        processQueue()
        return
      }
    }

    if (!sights || sights.length === 0) {
      if (processedSightCommandsRef.current.size > 0) {
        processedSightCommandsRef.current.clear()
        pendingQueueRef.current = []
        if (tourTimerRef.current) {
          window.clearTimeout(tourTimerRef.current)
          tourTimerRef.current = null
        }
        dwellUntilRef.current = 0
      }
    }

    const target = `${longitude}:${latitude}`
    const command = cameraCommandId
      ? `id:${cameraCommandId}`
      : `legacy:${target}:${cameraAction ?? cameraMode}`
    if (lastCommandRef.current === command) return

    const sameTarget = lastTargetRef.current === target
    if (manualCameraControlRef.current && sameTarget && !cameraAction) return
    if (!sameTarget) {
      manualCameraControlRef.current = false
      const isInCurrentSights = sights && sights.some((s) => `${s.longitude}:${s.latitude}` === target)
      if (!isInCurrentSights && pendingQueueRef.current.length > 0) {
        pendingQueueRef.current = []
        if (tourTimerRef.current) {
          window.clearTimeout(tourTimerRef.current)
          tourTimerRef.current = null
        }
        dwellUntilRef.current = 0
      }
    }

    const now = Date.now()
    if (cameraAction === 'focus_location' && now < dwellUntilRef.current) {
      const delay = dwellUntilRef.current - now
      pendingQueueRef.current.push({ latitude, longitude, locationName, command, target, zoom })
      if (!tourTimerRef.current || pendingQueueRef.current.length === 1) {
        if (tourTimerRef.current) window.clearTimeout(tourTimerRef.current)
        tourTimerRef.current = window.setTimeout(processQueue, delay)
      }
      return
    }

    const currentZoom = map.getZoom()
    const focusZoom = globe ? Math.max(5, Math.min(MAX_GLOBE_FOCUS_ZOOM, zoom)) : zoom
    let nextZoom = focusZoom
    if (cameraAction === 'focus_location') {
      nextZoom = Math.min(MAX_AI_ZOOM, Math.max(LANDMARK_ZOOM, currentZoom))
      dwellUntilRef.current = Date.now() + SIGHT_DWELL_MS
    } else if (cameraAction === 'zoom_in' || cameraMode === 'detail') {
      nextZoom = Math.min(MAX_AI_ZOOM, Math.max(DETAIL_ZOOM, currentZoom + 4))
    } else if (cameraAction === 'zoom_out') {
      nextZoom = Math.max(1.2, currentZoom - 4)
    } else if (cameraAction === 'overview' || cameraMode === 'overview') {
      nextZoom = 1.2
      dwellUntilRef.current = 0
      pendingQueueRef.current = []
      if (tourTimerRef.current) {
        window.clearTimeout(tourTimerRef.current)
        tourTimerRef.current = null
      }
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
  }, [cameraAction, cameraCommandId, cameraMode, globe, latitude, longitude, ready, zoom, sights])

  return (
    <div className="absolute inset-0 z-[5] bg-transparent" aria-label={`Interaktive Karte für ${locationName}`}>
      <div ref={elementRef} className="h-full w-full cursor-grab active:cursor-grabbing" />
      {!ready && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center bg-surface-container-lowest/70 text-sm text-on-surface-variant">
          {t('ai.geo.mapLoading', 'Karte wird geladen')}
        </div>
      )}
      {ready && (
        <div className="absolute bottom-4 right-4 z-20 flex flex-col gap-1.5 pointer-events-auto">
          <button
            type="button"
            onClick={() => {
              if (mapRef.current) {
                mapRef.current.zoomIn({ duration: 300 })
              }
            }}
            aria-label={t('ai.geo.zoomIn', 'Vergrößern')}
            className="flex h-9 w-9 items-center justify-center rounded-xl border border-outline-variant/40 bg-surface-container-low/95 text-on-surface shadow-md backdrop-blur-md transition-colors hover:bg-surface-container-high active:scale-95"
          >
            <Plus className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => {
              if (mapRef.current) {
                mapRef.current.zoomOut({ duration: 300 })
              }
            }}
            aria-label={t('ai.geo.zoomOut', 'Verkleinern')}
            className="flex h-9 w-9 items-center justify-center rounded-xl border border-outline-variant/40 bg-surface-container-low/95 text-on-surface shadow-md backdrop-blur-md transition-colors hover:bg-surface-container-high active:scale-95"
          >
            <Minus className="h-4 w-4" />
          </button>
        </div>
      )}
    </div>
  )
}
