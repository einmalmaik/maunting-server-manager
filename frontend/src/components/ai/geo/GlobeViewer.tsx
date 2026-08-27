import { useEffect, useRef, useState } from 'react'
import { Clock, Cloud, Compass, MapPin, Minus, Plus, RotateCcw, Satellite } from 'lucide-react'
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

/**
 * 3D-Globus-Komponente mit flüssiger Rotationsanimation, Zielort-Fokussierung
 * und unterer Live-Metriken-Leiste im Kommandozentren-Stil.
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
  const isDraggingRef = useRef(false)
  const lastMouseRef = useRef({ x: 0, y: 0 })
  const animFrameRef = useRef<number | null>(null)

  // Wenn Zielkoordinaten übergeben werden: flüssige Kamerafahrt (flyTo)
  useEffect(() => {
    if (typeof effLat === 'number' && typeof effLon === 'number') {
      // Umrechnung Lat/Lon in Rotationswinkel der Kugel
      const targetY = -((effLon + 90) * Math.PI) / 180
      const targetX = (effLat * Math.PI) / 180
      targetRotRef.current = { x: targetX, y: targetY }
      setAutoRotate(false)

      // Adaptiver Zoom basierend auf der Gebietsgröße (bbox)
      if (effBbox && effBbox.length === 4) {
        const dLon = Math.abs(effBbox[2] - effBbox[0])
        const dLat = Math.abs(effBbox[3] - effBbox[1])
        const maxSpan = Math.max(dLon, dLat)
        if (maxSpan > 25) setZoom(0.9)
        else if (maxSpan > 5) setZoom(1.1)
        else setZoom(1.35)
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

        rotRef.current.x += dx * 0.06
        rotRef.current.y += dy * 0.06

        if (Math.abs(dx) < 0.002 && Math.abs(dy) < 0.002) {
          rotRef.current = targetRotRef.current
          targetRotRef.current = null
        }
      } else if (autoRotate && !isDraggingRef.current) {
        rotRef.current.y += 0.003
      }

      pulse = (pulse + 0.05) % (Math.PI * 2)

      // 1. Atmosphärischer Glüheffekt (Glow)
      const glowGrad = ctx.createRadialGradient(cx, cy, radius * 0.85, cx, cy, radius * 1.3)
      glowGrad.addColorStop(0, 'rgba(56, 189, 248, 0.28)')
      glowGrad.addColorStop(0.5, 'rgba(14, 165, 233, 0.14)')
      glowGrad.addColorStop(1, 'rgba(14, 165, 233, 0)')

      ctx.fillStyle = glowGrad
      ctx.beginPath()
      ctx.arc(cx, cy, radius * 1.3, 0, Math.PI * 2)
      ctx.fill()

      // 2. Erdkörper (Tiefen-Verlauf)
      const earthGrad = ctx.createRadialGradient(
        cx - radius * 0.35,
        cy - radius * 0.35,
        radius * 0.1,
        cx,
        cy,
        radius,
      )
      earthGrad.addColorStop(0, '#0f172a')
      earthGrad.addColorStop(0.7, '#090d16')
      earthGrad.addColorStop(1, '#020617')

      ctx.save()
      ctx.beginPath()
      ctx.arc(cx, cy, radius, 0, Math.PI * 2)
      ctx.fillStyle = earthGrad
      ctx.fill()
      ctx.clip()

      // 3. Längen- und Breitengrade (3D Drahtgitter)
      const rx = rotRef.current.x
      const ry = rotRef.current.y

      ctx.strokeStyle = 'rgba(56, 189, 248, 0.18)'
      ctx.lineWidth = 1

      // Breitengrade
      for (let latDeg = -80; latDeg <= 80; latDeg += 20) {
        const phi = (latDeg * Math.PI) / 180
        ctx.beginPath()
        let first = true
        for (let lonDeg = -180; lonDeg <= 180; lonDeg += 5) {
          const theta = (lonDeg * Math.PI) / 180 + ry
          const cosPhi = Math.cos(phi)
          const sinPhi = Math.sin(phi)

          const px = Math.cos(theta) * cosPhi
          const py = sinPhi
          const pz = Math.sin(theta) * cosPhi

          const rotY = py * Math.cos(rx) - pz * Math.sin(rx)
          const rotZ = py * Math.sin(rx) + pz * Math.cos(rx)

          if (rotZ > 0) {
            const screenX = cx + px * radius
            const screenY = cy - rotY * radius
            if (first) {
              ctx.moveTo(screenX, screenY)
              first = false
            } else {
              ctx.lineTo(screenX, screenY)
            }
          } else {
            first = true
          }
        }
        ctx.stroke()
      }

      // Längengrade
      for (let lonDeg = -180; lonDeg < 180; lonDeg += 30) {
        const theta = (lonDeg * Math.PI) / 180 + ry
        ctx.beginPath()
        let first = true
        for (let latDeg = -90; latDeg <= 90; latDeg += 5) {
          const phi = (latDeg * Math.PI) / 180
          const cosPhi = Math.cos(phi)
          const sinPhi = Math.sin(phi)

          const px = Math.cos(theta) * cosPhi
          const py = sinPhi
          const pz = Math.sin(theta) * cosPhi

          const rotY = py * Math.cos(rx) - pz * Math.sin(rx)
          const rotZ = py * Math.sin(rx) + pz * Math.cos(rx)

          if (rotZ > 0) {
            const screenX = cx + px * radius
            const screenY = cy - rotY * radius
            if (first) {
              ctx.moveTo(screenX, screenY)
              first = false
            } else {
              ctx.lineTo(screenX, screenY)
            }
          } else {
            first = true
          }
        }
        ctx.stroke()
      }

      // 4. Äquator hervorheben
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.4)'
      ctx.lineWidth = 1.5
      ctx.beginPath()
      let eqFirst = true
      for (let lonDeg = -180; lonDeg <= 180; lonDeg += 5) {
        const theta = (lonDeg * Math.PI) / 180 + ry
        const px = Math.cos(theta)
        const pz = Math.sin(theta)
        const rotY = -pz * Math.sin(rx)
        const rotZ = pz * Math.cos(rx)

        if (rotZ > 0) {
          const screenX = cx + px * radius
          const screenY = cy - rotY * radius
          if (eqFirst) {
            ctx.moveTo(screenX, screenY)
            eqFirst = false
          } else {
            ctx.lineTo(screenX, screenY)
          }
        } else {
          eqFirst = true
        }
      }
      ctx.stroke()

      // 5. Region / Bounding Box Umrandung (wenn bbox übergeben wurde)
      if (effBbox && effBbox.length === 4) {
        const [minLon, minLat, maxLon, maxLat] = effBbox
        const points = [
          [minLat, minLon],
          [minLat, maxLon],
          [maxLat, maxLon],
          [maxLat, minLon],
        ]
        ctx.beginPath()
        let visibleCount = 0
        points.forEach(([pLat, pLon], idx) => {
          const phi = (pLat * Math.PI) / 180
          const theta = ((pLon + 90) * Math.PI) / 180 + ry
          const cosPhi = Math.cos(phi)
          const sinPhi = Math.sin(phi)
          const px = Math.cos(theta) * cosPhi
          const py = sinPhi
          const pz = Math.sin(theta) * cosPhi
          const rotY = py * Math.cos(rx) - pz * Math.sin(rx)
          const rotZ = py * Math.sin(rx) + pz * Math.cos(rx)
          if (rotZ > -0.2) {
            visibleCount++
            const sx = cx + px * radius
            const sy = cy - rotY * radius
            if (idx === 0) ctx.moveTo(sx, sy)
            else ctx.lineTo(sx, sy)
          }
        })
        if (visibleCount >= 2) {
          ctx.closePath()
          ctx.strokeStyle = 'rgba(56, 189, 248, 0.65)'
          ctx.lineWidth = 1.5
          ctx.fillStyle = 'rgba(56, 189, 248, 0.12)'
          ctx.fill()
          ctx.stroke()
        }
      }

      // 6. Zielort-Marker & Radar-Pulsar
      if (typeof effLat === 'number' && typeof effLon === 'number') {
        const phi = (effLat * Math.PI) / 180
        const theta = ((effLon + 90) * Math.PI) / 180 + ry
        const cosPhi = Math.cos(phi)
        const sinPhi = Math.sin(phi)

        const px = Math.cos(theta) * cosPhi
        const py = sinPhi
        const pz = Math.sin(theta) * cosPhi

        const rotY = py * Math.cos(rx) - pz * Math.sin(rx)
        const rotZ = py * Math.sin(rx) + pz * Math.cos(rx)

        if (rotZ > -0.1) {
          const screenX = cx + px * radius
          const screenY = cy - rotY * radius

          // Pulsierender Radar-Ring
          const pulseRadius = 10 + Math.sin(pulse) * 8
          ctx.strokeStyle = 'rgba(56, 189, 248, 0.85)'
          ctx.lineWidth = 2
          ctx.beginPath()
          ctx.arc(screenX, screenY, pulseRadius, 0, Math.PI * 2)
          ctx.stroke()

          // Äußerer Zielring
          ctx.strokeStyle = 'rgba(56, 189, 248, 0.35)'
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
            ctx.fillStyle = 'rgba(15, 23, 42, 0.85)'
            ctx.strokeStyle = 'rgba(56, 189, 248, 0.4)'
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
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.4)'
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

  // Mausinteraktion (Drehen)
  const handleMouseDown = (e: React.MouseEvent) => {
    isDraggingRef.current = true
    setAutoRotate(false)
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
        aria-label={`3D Globus Ansicht ${effLocation ? `für ${effLocation}` : ''}`}
      />

      {/* Steuerungselemente */}
      <div className="absolute top-3 right-3 flex items-center gap-1 rounded-xl border border-outline-variant/40 bg-surface-container-low/80 p-1 backdrop-blur-md z-10">
        <button
          type="button"
          onClick={() => setZoom((z) => Math.min(2.5, z + 0.2))}
          className="rounded-lg p-1.5 text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface transition-colors"
          title={t('ai.geo.zoomIn', 'Vergrößern')}
          aria-label={t('ai.geo.zoomIn', 'Vergrößern')}
        >
          <Plus className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={() => setZoom((z) => Math.max(0.6, z - 0.2))}
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
            setAutoRotate(true)
          }}
          className="rounded-lg p-1.5 text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface transition-colors"
          title={t('ai.geo.resetView', 'Ansicht zurücksetzen')}
          aria-label={t('ai.geo.resetView', 'Ansicht zurücksetzen')}
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
