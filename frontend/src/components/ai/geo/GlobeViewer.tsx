import { useEffect, useRef, useState } from 'react'
import { CircleDashed, Compass, MapPin, Satellite } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { AiRegionalAnalysis } from '@/api/ai'
import { MapTilerDetailMap } from './MapTilerDetailMap'

interface GlobeViewerProps {
  latitude?: number | null
  longitude?: number | null
  locationName?: string | null
  bbox?: [number, number, number, number] | null
  data?: AiRegionalAnalysis | null
  className?: string
}

interface Star {
  x: number
  y: number
  size: number
  opacity: number
}

function createStars(): Star[] {
  let seed = 0x4d534d
  const random = () => {
    seed = (seed * 1664525 + 1013904223) >>> 0
    return seed / 0x1_0000_0000
  }

  return Array.from({ length: 140 }, () => ({
    x: random(),
    y: random(),
    size: 0.5 + random() * 1.4,
    opacity: 0.18 + random() * 0.55,
  }))
}

const STARS = createStars()

function coordinateLabel(latitude: number, longitude: number) {
  return `${Math.abs(latitude).toFixed(4)}° ${latitude >= 0 ? 'N' : 'S'}, ${Math.abs(longitude).toFixed(4)}° ${longitude >= 0 ? 'E' : 'W'}`
}

/**
 * Gemeinsamer Weltraum-Hintergrund fuer die regionale Analyse. Die Karte
 * selbst wird ausschliesslich durch MapTiler/MapLibre gezeichnet.
 */
interface Sight {
  latitude: number
  longitude: number
  name: string
  summary?: string
  commandId: string
}

export function GlobeViewer({
  latitude,
  longitude,
  locationName,
  bbox,
  data,
  className = '',
}: GlobeViewerProps) {
  const { t } = useTranslation()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [mapUnavailable, setMapUnavailable] = useState(false)
  const [mapReady, setMapReady] = useState(false)
  const [sights, setSights] = useState<Sight[]>([])
  const lastMainRef = useRef<string | null>(null)

  const resolvedLatitude = latitude ?? data?.coordinates?.latitude
  const resolvedLongitude = longitude ?? data?.coordinates?.longitude
  const resolvedLocation = locationName ?? data?.location ?? t('ai.geo.region', 'Region')
  const hasCoordinates = Number.isFinite(resolvedLatitude) && Number.isFinite(resolvedLongitude)
  const scene = data?.satellite?.scenes?.[0]

  useEffect(() => {
    if (!data?.coordinates) return
    const key = `${data.location}:${data.coordinates.latitude}:${data.coordinates.longitude}`
    if (data.camera?.action === 'focus_location' && data.camera?.command_id) {
      const name = data.location ?? resolvedLocation
      setSights((prev) => {
        if (prev.some((s) => s.commandId === data.camera!.command_id)) return prev
        const summary = (data as unknown as { _aiSummary?: string })._aiSummary
        return [...prev, { latitude: data.coordinates!.latitude, longitude: data.coordinates!.longitude, name, summary, commandId: data.camera!.command_id! }]
      })
    } else if (lastMainRef.current && lastMainRef.current !== key && !data.camera?.command_id) {
      setSights([])
    }
    lastMainRef.current = key
  }, [data, resolvedLocation])

  useEffect(() => {
    setMapUnavailable(false)
  }, [resolvedLatitude, resolvedLongitude])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const context = canvas.getContext('2d')
    if (!context) return

    const draw = () => {
      const width = canvas.parentElement?.clientWidth ?? 0
      const height = canvas.parentElement?.clientHeight ?? 0
      if (!width || !height) return
      const pixelRatio = window.devicePixelRatio || 1
      canvas.width = Math.round(width * pixelRatio)
      canvas.height = Math.round(height * pixelRatio)
      context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0)
      context.clearRect(0, 0, width, height)

      const background = context.createRadialGradient(width * 0.52, height * 0.42, 0, width * 0.5, height * 0.5, Math.max(width, height) * 0.76)
      background.addColorStop(0, '#0a1c31')
      background.addColorStop(0.58, '#040b17')
      background.addColorStop(1, '#010409')
      context.fillStyle = background
      context.fillRect(0, 0, width, height)

      for (const star of STARS) {
        context.fillStyle = `rgba(190, 221, 255, ${star.opacity})`
        context.fillRect(star.x * width, star.y * height, star.size, star.size)
      }
    }

    draw()
    const observer = new ResizeObserver(draw)
    observer.observe(canvas.parentElement ?? canvas)
    return () => observer.disconnect()
  }, [])

  // Der Globus darf den Ort nicht nur markieren. Nach einer Ortsanalyse soll
  // die Umgebung bereits erkennbar sein, ohne gleich in die Straßenansicht
  // einer ausdrücklich angeforderten Detailkamera zu wechseln.
  const detailZoom = bbox && Math.max(Math.abs(bbox[2] - bbox[0]), Math.abs(bbox[3] - bbox[1])) > 25 ? 5 : 10

  return (
    <section className={`relative h-full min-h-[320px] overflow-hidden rounded-2xl border border-outline-variant/30 bg-surface-container-lowest ${className}`} aria-label={t('ai.geo.globeTitle', 'Regionale Karte')}>
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" aria-hidden="true" />

      {hasCoordinates && !mapUnavailable && (
        <MapTilerDetailMap
          latitude={resolvedLatitude as number}
          longitude={resolvedLongitude as number}
          locationName={resolvedLocation}
          globe
          zoom={detailZoom}
          cameraMode={data?.camera?.mode}
          cameraAction={data?.camera?.action}
          cameraCommandId={data?.camera?.command_id}
          sights={sights}
          onUnavailable={() => setMapUnavailable(true)}
          onReady={() => setMapReady(true)}
        />
      )}

      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-start justify-between gap-3 p-3">
        <div className="max-w-[75%] rounded-xl border border-primary/30 bg-surface-container-low/90 px-3 py-2 shadow-sm backdrop-blur-md">
          <div className="flex items-center gap-2 text-xs font-semibold text-primary">
            <MapPin className="h-3.5 w-3.5" aria-hidden="true" />
            <span className="truncate">{resolvedLocation}</span>
          </div>
          {hasCoordinates && <p className="mt-1 text-[11px] text-on-surface-variant">{coordinateLabel(resolvedLatitude as number, resolvedLongitude as number)}</p>}
        </div>
        <div className="rounded-xl border border-outline-variant/30 bg-surface-container-low/90 px-2.5 py-2 text-[11px] text-on-surface-variant shadow-sm backdrop-blur-md">
          <span className="flex items-center gap-1.5">
            <Compass className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
            {mapReady ? t('ai.geo.mapInteractive', 'Karte aktiv') : t('ai.geo.mapLoading', 'Karte wird geladen')}
          </span>
        </div>
      </div>

      {!hasCoordinates && (
        <MapStatus icon={CircleDashed} title={t('ai.geo.coordinatesMissingTitle', 'Keine bestätigten Koordinaten')} body={t('ai.geo.coordinatesMissingBody', 'Die Karte wird angezeigt, sobald der Dienst einen Ort bestätigt hat.')} />
      )}
      {mapUnavailable && (
        <MapStatus icon={Satellite} title={t('ai.geo.mapUnavailableTitle', 'Karte nicht verfügbar')} body={t('ai.geo.mapUnavailableBody', 'MapTiler ist für diese Instanz nicht eingerichtet oder derzeit nicht erreichbar.')} />
      )}

      {scene && (
        <div className="pointer-events-none absolute bottom-3 left-3 z-10 rounded-xl border border-outline-variant/30 bg-surface-container-low/90 px-3 py-2 text-[11px] shadow-sm backdrop-blur-md">
          <div className="flex items-center gap-1.5 font-medium text-on-surface">
            <Satellite className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
            {scene.mission}
          </div>
          <p className="mt-0.5 text-on-surface-variant">{t('ai.geo.sceneMetadata', 'Szenenmetadaten verfügbar')}</p>
        </div>
      )}
    </section>
  )
}

function MapStatus({ icon: Icon, title, body }: { icon: typeof CircleDashed; title: string; body: string }) {
  return (
    <div className="absolute inset-0 z-20 grid place-items-center p-6">
      <div className="max-w-sm rounded-2xl border border-outline-variant/30 bg-surface-container-low/95 p-5 text-center shadow-lg backdrop-blur-md">
        <Icon className="mx-auto h-6 w-6 text-primary" aria-hidden="true" />
        <h2 className="mt-3 text-sm font-semibold text-on-surface">{title}</h2>
        <p className="mt-1 text-xs leading-relaxed text-on-surface-variant">{body}</p>
      </div>
    </div>
  )
}
