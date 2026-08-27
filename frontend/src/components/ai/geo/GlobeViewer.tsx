import { useEffect, useRef, useState } from 'react'
import { Compass, Minus, Plus, RotateCcw } from 'lucide-react'

interface GlobeViewerProps {
  latitude?: number | null
  longitude?: number | null
  locationName?: string | null
  bbox?: [number, number, number, number] | null
  className?: string
}

/**
 * 3D-Globus-Komponente mit flüssiger Rotationsanimation und Zielort-Fokussierung.
 *
 * Zeichnet eine interaktive 3D-Erde mit atmosphärischem Glühen, Kontinental-Raster,
 * Koordinatengitter und Zielort-Pulsar.
 */
export function GlobeViewer({
  latitude,
  longitude,
  locationName,
  bbox,
  className = '',
}: GlobeViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [zoom, setZoom] = useState(1.0)
  const [autoRotate, setAutoRotate] = useState(true)

  // Aktuelle Rotationswinkel (Radiant)
  const rotRef = useRef({ x: 0.3, y: -0.8 })
  const targetRotRef = useRef<{ x: number; y: number } | null>(null)
  const isDraggingRef = useRef(false)
  const lastMouseRef = useRef({ x: 0, y: 0 })
  const animFrameRef = useRef<number | null>(null)

  // Wenn Zielkoordinaten übergeben werden: flüssige Kamerafahrt (flyTo)
  useEffect(() => {
    if (typeof latitude === 'number' && typeof longitude === 'number') {
      // Umrechnung Lat/Lon in Rotationswinkel der Kugel
      const targetY = -((longitude + 90) * Math.PI) / 180
      const targetX = (latitude * Math.PI) / 180
      targetRotRef.current = { x: targetX, y: targetY }
      setAutoRotate(false)

      // Adaptiver Zoom basierend auf der Gebietsgröße (bbox)
      if (bbox && bbox.length === 4) {
        const dLon = Math.abs(bbox[2] - bbox[0])
        const dLat = Math.abs(bbox[3] - bbox[1])
        const maxSpan = Math.max(dLon, dLat)
        if (maxSpan > 25) setZoom(0.9)
        else if (maxSpan > 5) setZoom(1.1)
        else setZoom(1.35)
      }
    }
  }, [latitude, longitude, bbox])

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
      const glowGrad = ctx.createRadialGradient(cx, cy, radius * 0.85, cx, cy, radius * 1.25)
      glowGrad.addColorStop(0, 'rgba(56, 189, 248, 0.25)')
      glowGrad.addColorStop(0.5, 'rgba(14, 165, 233, 0.12)')
      glowGrad.addColorStop(1, 'rgba(14, 165, 233, 0)')
      ctx.fillStyle = glowGrad
      ctx.beginPath()
      ctx.arc(cx, cy, radius * 1.25, 0, Math.PI * 2)
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

          // 3D Kugelkoordinaten
          const px = Math.cos(theta) * cosPhi
          const py = sinPhi
          const pz = Math.sin(theta) * cosPhi

          // Drehung um X-Achse (Latitude-Tilt)
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
      if (bbox && bbox.length === 4) {
        const [minLon, minLat, maxLon, maxLat] = bbox
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
      if (typeof latitude === 'number' && typeof longitude === 'number') {
        const phi = (latitude * Math.PI) / 180
        const theta = ((longitude + 90) * Math.PI) / 180 + ry
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
          const pulseRadius = 8 + Math.sin(pulse) * 6
          ctx.strokeStyle = 'rgba(239, 68, 68, 0.75)'
          ctx.lineWidth = 2
          ctx.beginPath()
          ctx.arc(screenX, screenY, pulseRadius, 0, Math.PI * 2)
          ctx.stroke()

          // Zentraler Pin
          ctx.fillStyle = '#ef4444'
          ctx.beginPath()
          ctx.arc(screenX, screenY, 4, 0, Math.PI * 2)
          ctx.fill()

          // Beschriftung
          if (locationName) {
            ctx.font = '600 12px Inter, sans-serif'
            ctx.fillStyle = '#ffffff'
            ctx.shadowColor = 'rgba(0,0,0,0.8)'
            ctx.shadowBlur = 4
            ctx.fillText(locationName, screenX + 12, screenY + 4)
            ctx.shadowBlur = 0
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
  }, [zoom, autoRotate, latitude, longitude, locationName])

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

  return (
    <div
      className={`relative flex h-full w-full min-h-[300px] flex-col items-center justify-center overflow-hidden rounded-2xl bg-surface-container-lowest border border-outline-variant/30 ${className}`}
    >
      <canvas
        ref={canvasRef}
        className="h-full w-full cursor-grab active:cursor-grabbing"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        aria-label={`3D Globus Ansicht ${locationName ? `für ${locationName}` : ''}`}
      />

      {/* Steuerungselemente */}
      <div className="absolute bottom-3 right-3 flex items-center gap-1.5 rounded-xl border border-outline-variant/40 bg-surface-container-low/80 p-1 backdrop-blur-md">
        <button
          type="button"
          onClick={() => setZoom((z) => Math.min(2.5, z + 0.2))}
          className="rounded-lg p-1.5 text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface transition-colors"
          title="Vergrößern"
          aria-label="Vergrößern"
        >
          <Plus className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={() => setZoom((z) => Math.max(0.6, z - 0.2))}
          className="rounded-lg p-1.5 text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface transition-colors"
          title="Verkleinern"
          aria-label="Verkleinern"
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
          title="Ansicht zurücksetzen"
          aria-label="Ansicht zurücksetzen"
        >
          <RotateCcw className="h-4 w-4" />
        </button>
      </div>

      {locationName && (
        <div className="absolute top-3 left-3 flex items-center gap-2 rounded-xl border border-primary/30 bg-surface-container-low/80 px-3 py-1.5 text-xs font-medium text-primary backdrop-blur-md">
          <Compass className="h-3.5 w-3.5" />
          <span>{locationName}</span>
          {typeof latitude === 'number' && typeof longitude === 'number' && (
            <span className="text-on-surface-variant">
              ({latitude.toFixed(2)}°, {longitude.toFixed(2)}°)
            </span>
          )}
        </div>
      )}
    </div>
  )
}
