import { useEffect, useRef, useState } from 'react'
import { Clock, Cloud, Compass, MapPin, Minus, Plus, RotateCcw, Satellite, Target } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { AiRegionalAnalysis } from '@/api/ai'

interface GlobeViewerProps {
  latitude?: number | null
  longitude?: number | null
  locationName?: string | null
  bbox?: [number, number, number, number] | null
  data?: AiRegionalAnalysis | null
  className?: string
}

// Hochauflösende Kontur-Vektoren für alle Kontinente der Erde [lat, lon]
const CONTINENTS: [number, number][][] = [
  // Nordamerika
  [
    [70, -160], [72, -130], [60, -140], [55, -130], [50, -125], [38, -123], [32, -117],
    [23, -110], [19, -104], [15, -93], [14, -87], [9, -79], [12, -75], [18, -88],
    [22, -97], [29, -95], [29, -89], [25, -80], [31, -81], [35, -75], [41, -70],
    [45, -65], [47, -53], [52, -56], [58, -62], [62, -75], [64, -85], [70, -95],
    [75, -100], [72, -125], [71, -156], [65, -168], [60, -165], [70, -160],
  ],
  // Südamerika
  [
    [12, -72], [10, -62], [5, -52], [-2, -44], [-8, -35], [-18, -39], [-23, -42],
    [-34, -53], [-42, -63], [-54, -68], [-55, -71], [-50, -75], [-40, -73], [-30, -71],
    [-18, -70], [-14, -76], [-5, -81], [1, -79], [8, -77], [12, -72],
  ],
  // Europa & Skandinavien
  [
    [36, -6], [37, -9], [43, -9], [44, -1], [48, -4], [50, 1], [53, 5], [54, 8],
    [58, 8], [62, 5], [69, 15], [71, 28], [68, 40], [60, 30], [55, 21], [54, 14],
    [46, 13], [44, 8], [41, 0], [36, -6],
  ],
  // Großbritannien & Irland
  [
    [50, -5], [54, -3], [58, -5], [58, -3], [55, -1], [51, 1], [50, -5],
  ],
  // Afrika
  [
    [35, -6], [37, 10], [32, 25], [31, 32], [28, 34], [22, 37], [12, 44], [11, 51],
    [2, 45], [-5, 40], [-11, 40], [-26, 33], [-34, 26], [-34, 18], [-28, 16], [-15, 12],
    [-5, 12], [4, 9], [5, 1], [5, -4], [6, -11], [12, -16], [16, -17], [21, -17],
    [28, -13], [35, -6],
  ],
  // Asien & Eurasien
  [
    [75, 40], [77, 80], [76, 100], [70, 130], [72, 145], [66, 170], [60, 165],
    [55, 155], [45, 136], [40, 128], [37, 126], [30, 122], [22, 114], [21, 108],
    [10, 107], [1, 104], [8, 98], [16, 96], [22, 89], [17, 82], [8, 77],
    [20, 72], [25, 62], [27, 51], [15, 53], [12, 44], [22, 38], [30, 35],
    [36, 36], [41, 28], [45, 36], [50, 50], [55, 60], [60, 60], [68, 65], [75, 40],
  ],
  // Australien
  [
    [-11, 142], [-15, 145], [-24, 153], [-33, 152], [-38, 147], [-38, 140],
    [-35, 136], [-32, 132], [-34, 115], [-22, 114], [-18, 122], [-14, 129],
    [-12, 136], [-11, 142],
  ],
  // Japan
  [
    [31, 130], [35, 135], [40, 140], [45, 145], [43, 141], [35, 133], [31, 130],
  ],
  // Madagaskar
  [
    [-12, 49], [-16, 50], [-25, 47], [-25, 44], [-16, 44], [-12, 49],
  ],
  // Grönland
  [
    [76, -70], [83, -30], [80, -15], [70, -22], [60, -43], [65, -53], [76, -70],
  ],
  // Neuseeland
  [
    [-35, 173], [-38, 178], [-41, 175], [-46, 168], [-44, 170], [-41, 172], [-35, 173],
  ],
]

interface ProjectedPoint {
  rotX: number
  rotY: number
  rotZ: number
  sx: number
  sy: number
}

function projectPoint(
  lat: number,
  lon: number,
  rx: number,
  ry: number,
  cx: number,
  cy: number,
  radius: number,
): ProjectedPoint {
  const phi = (lat * Math.PI) / 180
  const theta = ((lon + 90) * Math.PI) / 180 + ry
  const cosPhi = Math.cos(phi)
  const sinPhi = Math.sin(phi)

  const px = Math.cos(theta) * cosPhi
  const py = sinPhi
  const pz = Math.sin(theta) * cosPhi

  const rotX = px
  const rotY = py * Math.cos(rx) - pz * Math.sin(rx)
  const rotZ = py * Math.sin(rx) + pz * Math.cos(rx)

  const sx = cx + rotX * radius
  const sy = cy - rotY * radius

  return { rotX, rotY, rotZ, sx, sy }
}

function drawClippedLineSegment(
  ctx: CanvasRenderingContext2D,
  p1: ProjectedPoint,
  p2: ProjectedPoint,
  cx: number,
  cy: number,
  radius: number,
) {
  // 1. Beide Punkte auf der Vorderseite (voll sichtbar)
  if (p1.rotZ > 0 && p2.rotZ > 0) {
    ctx.moveTo(p1.sx, p1.sy)
    ctx.lineTo(p2.sx, p2.sy)
    return
  }
  // 2. p1 vorne, p2 hinten (Linie tritt am Horizont aus)
  if (p1.rotZ > 0 && p2.rotZ <= 0) {
    const t = p1.rotZ / (p1.rotZ - p2.rotZ)
    let rotXt = p1.rotX + t * (p2.rotX - p1.rotX)
    let rotYt = p1.rotY + t * (p2.rotY - p1.rotY)
    const len = Math.hypot(rotXt, rotYt)
    if (len > 0) {
      rotXt /= len
      rotYt /= len
    }
    const sxt = cx + rotXt * radius
    const syt = cy - rotYt * radius
    ctx.moveTo(p1.sx, p1.sy)
    ctx.lineTo(sxt, syt)
    return
  }
  // 3. p1 hinten, p2 vorne (Linie tritt am Horizont ein)
  if (p1.rotZ <= 0 && p2.rotZ > 0) {
    const t = -p1.rotZ / (p2.rotZ - p1.rotZ)
    let rotXt = p1.rotX + t * (p2.rotX - p1.rotX)
    let rotYt = p1.rotY + t * (p2.rotY - p1.rotY)
    const len = Math.hypot(rotXt, rotYt)
    if (len > 0) {
      rotXt /= len
      rotYt /= len
    }
    const sxt = cx + rotXt * radius
    const syt = cy - rotYt * radius
    ctx.moveTo(sxt, syt)
    ctx.lineTo(p2.sx, p2.sy)
    return
  }
  // 4. Beide Punkte auf der Rückseite -> wird nicht gezeichnet
}

/**
 * 3D-Globus-Komponente mit echten Kontinent-Vektoren, flüssiger Rotationsanimation,
 * Zielort-Fokussierung, Mausrad-Zoom und unterer Live-Metriken-Leiste.
 */
export function GlobeViewer({
  latitude,
  longitude,
  locationName,
  bbox,
  data,
  className = '',
}: GlobeViewerProps) {
  const { t } = useTranslation()
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [zoom, setZoom] = useState(1.0)
  const [autoRotate, setAutoRotate] = useState(true)

  const effLat = latitude ?? data?.coordinates?.latitude
  const effLon = longitude ?? data?.coordinates?.longitude
  const effLocation = locationName ?? data?.location
  const effBbox = bbox ?? data?.coordinates?.bbox
  const weather = data?.weather
  const satellite = data?.satellite

  // Aktuelle Rotationswinkel (Radiant)
  const rotRef = useRef({ x: 0.3, y: -0.8 })
  const targetRotRef = useRef<{ x: number; y: number } | null>(null)
  const lastTargetCoordRef = useRef<{ lat: number; lon: number } | null>(null)
  const isDraggingRef = useRef(false)
  const lastMouseRef = useRef({ x: 0, y: 0 })
  const animFrameRef = useRef<number | null>(null)

  // Zentrierungs-Funktion für Zielort
  const flyToTarget = (lat: number, lon: number, customBbox?: [number, number, number, number] | null) => {
    const targetY = -((lon + 90) * Math.PI) / 180
    const targetX = (lat * Math.PI) / 180
    targetRotRef.current = { x: targetX, y: targetY }
    setAutoRotate(false)

    if (customBbox && customBbox.length === 4) {
      const dLon = Math.abs(customBbox[2] - customBbox[0])
      const dLat = Math.abs(customBbox[3] - customBbox[1])
      const maxSpan = Math.max(dLon, dLat)
      if (maxSpan > 25) setZoom(0.9)
      else if (maxSpan > 5) setZoom(1.15)
      else setZoom(1.4)
    }
  }

  // Wenn NEUE Zielkoordinaten übergeben werden: flüssige Kamerafahrt (flyTo)
  // Primitives-Vergleich entkoppelt von re-renderenden Array-Referenzen (effBbox)
  useEffect(() => {
    if (typeof effLat === 'number' && typeof effLon === 'number') {
      const isNewLocation =
        lastTargetCoordRef.current?.lat !== effLat ||
        lastTargetCoordRef.current?.lon !== effLon

      if (isNewLocation) {
        lastTargetCoordRef.current = { lat: effLat, lon: effLon }
        flyToTarget(effLat, effLon, effBbox)
      }
    }
  }, [effLat, effLon, effBbox])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let pulse = 0

    const render = () => {
      const width = (canvas.width = canvas.parentElement?.clientWidth || 400)
      const height = (canvas.height = canvas.parentElement?.clientHeight || 400)
      const radius = Math.min(width, height) * 0.38 * zoom
      const cx = width / 2
      const cy = height / 2

      // Hintergrund
      ctx.clearRect(0, 0, width, height)

      // Sanfte Annäherung an Zielwinkel (Kameraflug)
      if (targetRotRef.current) {
        const dx = targetRotRef.current.x - rotRef.current.x
        let dy = targetRotRef.current.y - rotRef.current.y
        // Kürzesten Kreisweg wählen
        while (dy > Math.PI) dy -= Math.PI * 2
        while (dy < -Math.PI) dy += Math.PI * 2

        rotRef.current.x += dx * 0.07
        rotRef.current.y += dy * 0.07

        if (Math.abs(dx) < 0.001 && Math.abs(dy) < 0.001) {
          rotRef.current = targetRotRef.current
          targetRotRef.current = null
        }
      } else if (autoRotate && !isDraggingRef.current) {
        rotRef.current.y += 0.002
      }

      pulse = (pulse + 0.05) % (Math.PI * 2)

      // 1. Atmosphärischer Glüheffekt (Atmospheric Halo Glow)
      const glowGrad = ctx.createRadialGradient(cx, cy, radius * 0.82, cx, cy, radius * 1.35)
      glowGrad.addColorStop(0, 'rgba(56, 189, 248, 0.32)')
      glowGrad.addColorStop(0.4, 'rgba(14, 165, 233, 0.16)')
      glowGrad.addColorStop(1, 'rgba(14, 165, 233, 0)')

      ctx.fillStyle = glowGrad
      ctx.beginPath()
      ctx.arc(cx, cy, radius * 1.35, 0, Math.PI * 2)
      ctx.fill()

      // 2. Erdkörper (Tiefen-Verlauf mit Ozeanblau)
      const earthGrad = ctx.createRadialGradient(
        cx - radius * 0.35,
        cy - radius * 0.35,
        radius * 0.08,
        cx,
        cy,
        radius,
      )
      earthGrad.addColorStop(0, '#0f2744')
      earthGrad.addColorStop(0.5, '#0b1b30')
      earthGrad.addColorStop(0.85, '#050d18')
      earthGrad.addColorStop(1, '#02060e')

      ctx.save()
      ctx.beginPath()
      ctx.arc(cx, cy, radius, 0, Math.PI * 2)
      ctx.fillStyle = earthGrad
      ctx.fill()
      ctx.clip()

      const rx = rotRef.current.x
      const ry = rotRef.current.y

      // 3. Echte Kontinent-Landmassen & Küstenlinien mit sauberem Horizont-Clipping
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.65)'
      ctx.lineWidth = 1.4
      ctx.beginPath()

      CONTINENTS.forEach((poly) => {
        const len = poly.length
        for (let i = 0; i < len; i++) {
          const [latA, lonA] = poly[i]
          const [latB, lonB] = poly[(i + 1) % len]

          // Subdivide larger segments to follow sphere curvature smoothly
          const dLat = latB - latA
          const dLon = lonB - lonA
          const steps = Math.max(1, Math.min(8, Math.ceil(Math.hypot(dLat, dLon) / 5)))

          for (let s = 0; s < steps; s++) {
            const curLatA = latA + (dLat * s) / steps
            const curLonA = lonA + (dLon * s) / steps
            const curLatB = latA + (dLat * (s + 1)) / steps
            const curLonB = lonA + (dLon * (s + 1)) / steps

            const ptA = projectPoint(curLatA, curLonA, rx, ry, cx, cy, radius)
            const ptB = projectPoint(curLatB, curLonB, rx, ry, cx, cy, radius)

            drawClippedLineSegment(ctx, ptA, ptB, cx, cy, radius)
          }
        }
      })
      ctx.stroke()

      // 4. Längen- und Breitengrade (3D Drahtgitter ohne Clipping-Glitches)
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.12)'
      ctx.lineWidth = 0.8
      ctx.beginPath()

      // Breitengrade (Parallelen)
      for (let latDeg = -80; latDeg <= 80; latDeg += 20) {
        for (let lonDeg = -180; lonDeg < 180; lonDeg += 4) {
          const pt1 = projectPoint(latDeg, lonDeg, rx, ry, cx, cy, radius)
          const pt2 = projectPoint(latDeg, lonDeg + 4, rx, ry, cx, cy, radius)
          drawClippedLineSegment(ctx, pt1, pt2, cx, cy, radius)
        }
      }

      // Längengrade (Meridiane)
      for (let lonDeg = -180; lonDeg < 180; lonDeg += 30) {
        for (let latDeg = -90; latDeg < 90; latDeg += 4) {
          const pt1 = projectPoint(latDeg, lonDeg, rx, ry, cx, cy, radius)
          const pt2 = projectPoint(latDeg + 4, lonDeg, rx, ry, cx, cy, radius)
          drawClippedLineSegment(ctx, pt1, pt2, cx, cy, radius)
        }
      }
      ctx.stroke()

      // Äquatorlinie
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.35)'
      ctx.lineWidth = 1.2
      ctx.beginPath()
      for (let lonDeg = -180; lonDeg < 180; lonDeg += 4) {
        const pt1 = projectPoint(0, lonDeg, rx, ry, cx, cy, radius)
        const pt2 = projectPoint(0, lonDeg + 4, rx, ry, cx, cy, radius)
        drawClippedLineSegment(ctx, pt1, pt2, cx, cy, radius)
      }
      ctx.stroke()

      // 5. Region / Bounding Box Umrandung (wenn bbox übergeben wurde)
      if (effBbox && effBbox.length === 4) {
        const [minLon, minLat, maxLon, maxLat] = effBbox
        const boxEdges: [number, number, number, number][] = [
          [minLat, minLon, minLat, maxLon],
          [minLat, maxLon, maxLat, maxLon],
          [maxLat, maxLon, maxLat, minLon],
          [maxLat, minLon, minLat, minLon],
        ]

        ctx.strokeStyle = 'rgba(56, 189, 248, 0.85)'
        ctx.lineWidth = 1.6
        ctx.beginPath()

        boxEdges.forEach(([bLatA, bLonA, bLatB, bLonB]) => {
          const dLat = bLatB - bLatA
          const dLon = bLonB - bLonA
          const steps = 6
          for (let s = 0; s < steps; s++) {
            const curLatA = bLatA + (dLat * s) / steps
            const curLonA = bLonA + (dLon * s) / steps
            const curLatB = bLatA + (dLat * (s + 1)) / steps
            const curLonB = bLonA + (dLon * (s + 1)) / steps

            const ptA = projectPoint(curLatA, curLonA, rx, ry, cx, cy, radius)
            const ptB = projectPoint(curLatB, curLonB, rx, ry, cx, cy, radius)

            drawClippedLineSegment(ctx, ptA, ptB, cx, cy, radius)
          }
        })
        ctx.stroke()
      }

      // 6. Zielort-Marker & Radar-Pulsar
      if (typeof effLat === 'number' && typeof effLon === 'number') {
        const pt = projectPoint(effLat, effLon, rx, ry, cx, cy, radius)

        if (pt.rotZ > 0) {
          const screenX = pt.sx
          const screenY = pt.sy

          // Pulsierender Radar-Ring
          const pulseRadius = 10 + Math.sin(pulse) * 8
          ctx.strokeStyle = 'rgba(56, 189, 248, 0.9)'
          ctx.lineWidth = 2
          ctx.beginPath()
          ctx.arc(screenX, screenY, pulseRadius, 0, Math.PI * 2)
          ctx.stroke()

          // Äußerer Zielring
          ctx.strokeStyle = 'rgba(56, 189, 248, 0.4)'
          ctx.lineWidth = 1
          ctx.beginPath()
          ctx.arc(screenX, screenY, 22, 0, Math.PI * 2)
          ctx.stroke()

          // Zentraler Pin
          ctx.fillStyle = '#38bdf8'
          ctx.beginPath()
          ctx.arc(screenX, screenY, 4.5, 0, Math.PI * 2)
          ctx.fill()

          // Beschriftungs-Badge
          if (effLocation) {
            ctx.font = '600 12px Inter, sans-serif'
            const textWidth = ctx.measureText(effLocation).width
            const badgeX = screenX + 14
            const badgeY = screenY - 14

            // Badge Hintergrund
            ctx.fillStyle = 'rgba(15, 23, 42, 0.9)'
            ctx.strokeStyle = 'rgba(56, 189, 248, 0.45)'
            ctx.lineWidth = 1
            ctx.beginPath()
            ctx.roundRect(badgeX, badgeY - 12, textWidth + 14, 22, 6)
            ctx.fill()
            ctx.stroke()

            // Badge Text
            ctx.fillStyle = '#f8fafc'
            ctx.fillText(effLocation, badgeX + 7, badgeY + 3)
          }
        }
      }

      ctx.restore()

      // Kugel-Rand-Lichtbogen
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.45)'
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.arc(cx, cy, radius, 0, Math.PI * 2)
      ctx.stroke()

      animFrameRef.current = requestAnimationFrame(render)
    }

    render()

    return () => {
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current)
    }
  }, [zoom, autoRotate, effLat, effLon, effLocation, effBbox])

  // Mausinteraktion (Freies Drehen ohne Zurückspringen)
  const handleMouseDown = (e: React.MouseEvent) => {
    isDraggingRef.current = true
    setAutoRotate(false)
    targetRotRef.current = null // Automatisches Zurückspringen stoppen
    lastMouseRef.current = { x: e.clientX, y: e.clientY }
  }

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDraggingRef.current) return
    const dx = e.clientX - lastMouseRef.current.x
    const dy = e.clientY - lastMouseRef.current.y
    rotRef.current.y += dx * 0.008
    rotRef.current.x = Math.max(-1.4, Math.min(1.4, rotRef.current.x + dy * 0.008))
    lastMouseRef.current = { x: e.clientX, y: e.clientY }
  }

  const handleMouseUp = () => {
    isDraggingRef.current = false
  }

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    setZoom((z) => Math.max(0.4, Math.min(3.5, z - e.deltaY * 0.0015)))
  }

  const aktualitaetText = satellite?.scenes?.[0]?.datetime
    ? new Date(satellite.scenes[0].datetime).toLocaleDateString(undefined, { day: '2-digit', month: '2-digit' })
    : t('ai.geo.current', 'Aktuell')

  const satelliteMission = satellite?.scenes?.[0]?.mission || 'Sentinel-2'

  return (
    <div
      className={`relative flex h-full w-full min-h-[320px] flex-col items-center justify-center overflow-hidden rounded-2xl bg-surface-container-lowest border border-outline-variant/30 ${className}`}
    >
      <canvas
        ref={canvasRef}
        className="h-full w-full cursor-grab active:cursor-grabbing"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onWheel={handleWheel}
        aria-label={`3D Globus Ansicht ${effLocation ? `für ${effLocation}` : ''}`}
      />

      {/* Steuerungselemente (Zoom, Zentrieren, Rotation) */}
      <div className="absolute top-3 right-3 flex items-center gap-1 rounded-xl border border-outline-variant/40 bg-surface-container-low/80 p-1 backdrop-blur-md z-10">
        {typeof effLat === 'number' && typeof effLon === 'number' && (
          <button
            type="button"
            onClick={() => flyToTarget(effLat, effLon, effBbox)}
            className="rounded-lg p-1.5 text-primary hover:bg-primary/10 transition-colors"
            title={t('ai.geo.recenter', 'Auf Zielort zentrieren')}
            aria-label={t('ai.geo.recenter', 'Auf Zielort zentrieren')}
          >
            <Target className="h-4 w-4" />
          </button>
        )}
        <button
          type="button"
          onClick={() => setZoom((z) => Math.min(3.0, z + 0.2))}
          className="rounded-lg p-1.5 text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface transition-colors"
          title={t('ai.geo.zoomIn', 'Vergrößern')}
          aria-label={t('ai.geo.zoomIn', 'Vergrößern')}
        >
          <Plus className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={() => setZoom((z) => Math.max(0.4, z - 0.2))}
          className="rounded-lg p-1.5 text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface transition-colors"
          title={t('ai.geo.zoomOut', 'Verkleinern')}
          aria-label={t('ai.geo.zoomOut', 'Verkleinern')}
        >
          <Minus className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={() => {
            setZoom(1.0)
            targetRotRef.current = null
            setAutoRotate((r) => !r)
          }}
          className={`rounded-lg p-1.5 transition-colors ${autoRotate ? 'text-primary bg-primary/10' : 'text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface'}`}
          title={t('ai.geo.resetView', 'Rotation umschalten')}
          aria-label={t('ai.geo.resetView', 'Rotation umschalten')}
        >
          <RotateCcw className="h-4 w-4" />
        </button>
      </div>

      {/* Standort-Badge oben links */}
      {effLocation && (
        <div className="absolute top-3 left-3 flex items-center gap-2 rounded-xl border border-primary/30 bg-surface-container-low/85 px-3 py-1.5 text-xs font-medium text-primary backdrop-blur-md z-10">
          <Compass className="h-3.5 w-3.5 shrink-0" />
          <span className="font-semibold">{effLocation}</span>
          {typeof effLat === 'number' && typeof effLon === 'number' && (
            <span className="text-on-surface-variant text-[11px]">
              ({Math.abs(effLat).toFixed(2)}°{effLat >= 0 ? 'N' : 'S'}, {Math.abs(effLon).toFixed(2)}°{effLon >= 0 ? 'E' : 'W'})
            </span>
          )}
        </div>
      )}

      {/* Untere Metriken-Leiste (Bottom Metrics Bar) */}
      <div className="absolute bottom-3 left-3 right-3 sm:right-auto flex flex-wrap items-center gap-2.5 sm:gap-4 rounded-xl border border-outline-variant/30 bg-surface-container-lowest/85 px-3.5 py-2 text-xs backdrop-blur-md text-on-surface-variant z-10">
        {typeof effLat === 'number' && typeof effLon === 'number' && (
          <div className="flex items-center gap-1.5">
            <MapPin className="h-3.5 w-3.5 text-primary shrink-0" aria-hidden="true" />
            <span className="font-medium text-on-surface">
              {Math.abs(effLat).toFixed(2)}°{effLat >= 0 ? 'N' : 'S'}, {Math.abs(effLon).toFixed(2)}°{effLon >= 0 ? 'E' : 'W'}
            </span>
          </div>
        )}
        <div className="flex items-center gap-1.5 border-l border-outline-variant/30 pl-2.5 sm:pl-3">
          <Clock className="h-3.5 w-3.5 text-sky-400 shrink-0" aria-hidden="true" />
          <span>{aktualitaetText}</span>
        </div>
        {weather && (
          <div className="flex items-center gap-1.5 border-l border-outline-variant/30 pl-2.5 sm:pl-3">
            <Cloud className="h-3.5 w-3.5 text-amber-400 shrink-0" aria-hidden="true" />
            <span>{Math.round(weather.temperature_celsius)}°C, {weather.condition}</span>
          </div>
        )}
        <div className="flex items-center gap-1.5 border-l border-outline-variant/30 pl-2.5 sm:pl-3 hidden md:flex">
          <Satellite className="h-3.5 w-3.5 text-teal-400 shrink-0" aria-hidden="true" />
          <span>{satelliteMission}</span>
        </div>
      </div>
    </div>
  )
}
