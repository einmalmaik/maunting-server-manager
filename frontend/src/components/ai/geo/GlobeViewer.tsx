import { useCallback, useEffect, useRef, useState } from 'react'
import { Clock, Cloud, Compass, MapPin, Minus, Plus, RotateCcw, Satellite, Target } from 'lucide-react'
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

interface TexturePoint {
  lat: number
  lon: number
  tone: number
  light: boolean
}

interface Star {
  x: number
  y: number
  size: number
  alpha: number
  phase: number
}

function seededRandom(seed: number) {
  let state = seed >>> 0
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0
    return state / 0x1_0000_0000
  }
}

function containsCoordinate(lat: number, lon: number, polygon: [number, number][]) {
  let inside = false
  for (let index = 0, previous = polygon.length - 1; index < polygon.length; previous = index++) {
    const [latA, lonA] = polygon[index]
    const [latB, lonB] = polygon[previous]
    const intersects = (latA > lat) !== (latB > lat) &&
      lon < ((lonB - lonA) * (lat - latA)) / (latB - latA) + lonA
    if (intersects) inside = !inside
  }
  return inside
}

// Einmal vorbereitete Punkte geben den Landmassen Tiefe, ohne Bilddateien
// herunterzuladen oder je Animationsbild Zufall/Allokationen zu erzeugen.
const LAND_TEXTURE: TexturePoint[] = (() => {
  const random = seededRandom(0x4d534d)
  const points: TexturePoint[] = []
  while (points.length < 420) {
    const lat = random() * 150 - 75
    const lon = random() * 360 - 180
    if (CONTINENTS.some((polygon) => containsCoordinate(lat, lon, polygon))) {
      points.push({ lat, lon, tone: random(), light: random() > 0.76 })
    }
  }
  return points
})()

const STAR_FIELD: Star[] = (() => {
  const random = seededRandom(0x45525448)
  return Array.from({ length: 180 }, () => ({
    x: random(), y: random(), size: 0.35 + random() * 1.45,
    alpha: 0.2 + random() * 0.68, phase: random() * Math.PI * 2,
  }))
})()

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

/**
 * Zeichnet die NASA-Blue-Marble-Karte als perspektivisch korrekte Kugel.
 * Die Textur ist equirektangular (WGS84): Jeder Bildschirm-Pixel wird über
 * die inverse Kamerarotation auf seinen echten Breiten- und Längengrad
 * zurückgeführt. Der Ortsmarker verwendet dieselbe Projektion.
 */
function drawEarthTexture(
  ctx: CanvasRenderingContext2D,
  texture: ImageData,
  target: HTMLCanvasElement,
  rx: number,
  ry: number,
  cx: number,
  cy: number,
  radius: number,
) {
  const diameter = Math.max(192, Math.min(384, Math.round(radius * 1.55)))
  if (target.width !== diameter || target.height !== diameter) {
    target.width = diameter
    target.height = diameter
  }

  const output = new ImageData(diameter, diameter)
  const source = texture.data
  const pixels = output.data
  const cosRx = Math.cos(rx)
  const sinRx = Math.sin(rx)
  const radiusSq = (diameter / 2) ** 2
  const half = diameter / 2

  for (let y = 0; y < diameter; y += 1) {
    const screenY = (half - y) / half
    for (let x = 0; x < diameter; x += 1) {
      const screenX = (x - half) / half
      const flatDistance = screenX * screenX + screenY * screenY
      if (flatDistance > 1) continue

      // Vorderseite der Einheitskugel; anschließend inverse X-Rotation.
      const frontZ = Math.sqrt(Math.max(0, 1 - flatDistance))
      const py = screenY * cosRx + frontZ * sinRx
      const pz = -screenY * sinRx + frontZ * cosRx
      const theta = Math.atan2(pz, screenX)
      const latitude = Math.asin(Math.max(-1, Math.min(1, py)))
      const longitude = theta - ry - Math.PI / 2
      const sourceX = Math.max(0, Math.min(texture.width - 1, Math.floor((((longitude * 180) / Math.PI + 180) % 360 + 360) % 360 / 360 * texture.width)))
      const sourceY = Math.max(0, Math.min(texture.height - 1, Math.floor((0.5 - latitude / Math.PI) * texture.height)))
      const sourceOffset = (sourceY * texture.width + sourceX) * 4
      const targetOffset = (y * diameter + x) * 4

      // Sanftes Tageslicht und Randabschattung geben Tiefe, ohne Geografie zu verfälschen.
      const light = 0.48 + 0.52 * Math.max(0, screenX * -0.5 + screenY * 0.2 + frontZ * 0.55)
      pixels[targetOffset] = Math.round(source[sourceOffset] * light)
      pixels[targetOffset + 1] = Math.round(source[sourceOffset + 1] * light)
      pixels[targetOffset + 2] = Math.round(source[sourceOffset + 2] * light + 12 * (1 - light))
      pixels[targetOffset + 3] = Math.round(255 * Math.min(1, (radiusSq - ((x - half) ** 2 + (y - half) ** 2)) / (diameter * 0.8) + 0.92))
    }
  }

  const targetContext = target.getContext('2d')
  if (!targetContext) return
  targetContext.putImageData(output, 0, 0)
  ctx.drawImage(target, cx - radius, cy - radius, radius * 2, radius * 2)
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
// Bekannte Schnell-Koordinaten für spekulative Drehung schon vor Abschluss des vollständigen Geocodings
const CITY_PRESETS: Record<string, { lat: number; lon: number; bbox?: [number, number, number, number] }> = {
  berlin: { lat: 52.52, lon: 13.405, bbox: [13.0883, 52.3382, 13.7611, 52.6755] },
  hamburg: { lat: 53.5511, lon: 9.9937 },
  muenchen: { lat: 48.1351, lon: 11.582 },
  münchen: { lat: 48.1351, lon: 11.582 },
  munich: { lat: 48.1351, lon: 11.582 },
  koeln: { lat: 50.9375, lon: 6.9603 },
  köln: { lat: 50.9375, lon: 6.9603 },
  cologne: { lat: 50.9375, lon: 6.9603 },
  frankfurt: { lat: 50.1109, lon: 8.6821 },
  stuttgart: { lat: 48.7758, lon: 9.1829 },
  duesseldorf: { lat: 51.2277, lon: 6.7735 },
  düsseldorf: { lat: 51.2277, lon: 6.7735 },
  paris: { lat: 48.8566, lon: 2.3522, bbox: [2.2241, 48.8155, 2.4699, 48.9021] },
  london: { lat: 51.5074, lon: -0.1278, bbox: [-0.5103, 51.2867, 0.334, 51.6918] },
  tokio: { lat: 35.6762, lon: 139.6503, bbox: [138.9427, 35.5288, 139.9213, 35.8984] },
  tokyo: { lat: 35.6762, lon: 139.6503, bbox: [138.9427, 35.5288, 139.9213, 35.8984] },
  washington: { lat: 38.8951, lon: -77.0364, bbox: [-77.1197, 38.7916, -76.9093, 38.9955] },
  'new york': { lat: 40.7128, lon: -74.006 },
  madrid: { lat: 40.4168, lon: -3.7038 },
  rom: { lat: 41.9028, lon: 12.4964 },
  rome: { lat: 41.9028, lon: 12.4964 },
  wien: { lat: 48.2082, lon: 16.3738 },
  vienna: { lat: 48.2082, lon: 16.3738 },
  zuerich: { lat: 47.3769, lon: 8.5417 },
  zürich: { lat: 47.3769, lon: 8.5417 },
  zurich: { lat: 47.3769, lon: 8.5417 },
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
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const earthTextureRef = useRef<ImageData | null>(null)
  const earthTextureCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const lastTextureRenderRef = useRef(0)
  const [textureVersion, setTextureVersion] = useState(0)
  const [zoom, setZoom] = useState(1.0)
  const [mapTilerUnavailable, setMapTilerUnavailable] = useState(false)
  const [autoRotate, setAutoRotate] = useState(true)
  const handleMapTilerUnavailable = useCallback(() => setMapTilerUnavailable(true), [])

  // Lokale, öffentliche NASA-Blue-Marble-Textur: kein Laufzeit-Netzwerkaufruf
  // und keine Standortdaten verlassen dafür den Browser.
  useEffect(() => {
    const image = new Image()
    image.onload = () => {
      const textureCanvas = document.createElement('canvas')
      textureCanvas.width = image.naturalWidth
      textureCanvas.height = image.naturalHeight
      const textureContext = textureCanvas.getContext('2d', { willReadFrequently: true })
      if (!textureContext) return

      textureContext.drawImage(image, 0, 0)
      earthTextureRef.current = textureContext.getImageData(0, 0, textureCanvas.width, textureCanvas.height)
      earthTextureCanvasRef.current = document.createElement('canvas')
      setTextureVersion((version) => version + 1)
    }
    image.src = '/earth/blue-marble.jpg'

    return () => {
      image.onload = null
    }
  }, [])

  // Reacts Wheel-Handler aktualisiert den Zoom. Der native, nicht-passive
  // Listener stellt zusätzlich sicher, dass Browser den Wheel-Impuls nicht an
  // den Seiten-Scroller weiterreichen.
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const preventPageScroll = (event: WheelEvent) => event.preventDefault()
    canvas.addEventListener('wheel', preventPageScroll, { passive: false })
    return () => canvas.removeEventListener('wheel', preventPageScroll)
  }, [])

  const effLocation = locationName ?? data?.location
  const preset = effLocation ? CITY_PRESETS[effLocation.toLowerCase().trim()] : undefined
  const effLat = latitude ?? data?.coordinates?.latitude ?? preset?.lat
  const effLon = longitude ?? data?.coordinates?.longitude ?? preset?.lon
  const effBbox = bbox ?? data?.coordinates?.bbox ?? preset?.bbox
  const mapCenter = effBbox && effBbox.length === 4
    ? { latitude: (effBbox[1] + effBbox[3]) / 2, longitude: (effBbox[0] + effBbox[2]) / 2 }
    : null

  useEffect(() => {
    setMapTilerUnavailable(false)
  }, [effLat, effLon])

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
      else if (maxSpan > 5) setZoom(1.35)
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
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ?? false

    const render = () => {
      const width = (canvas.width = canvas.parentElement?.clientWidth || 400)
      const height = (canvas.height = canvas.parentElement?.clientHeight || 400)
      const radius = Math.min(width, height) * 0.38 * zoom
      const cx = width / 2
      const cy = height / 2

      // Hintergrund
      ctx.clearRect(0, 0, width, height)

      const space = ctx.createRadialGradient(width * 0.48, height * 0.42, 0, width * 0.5, height * 0.5, Math.max(width, height) * 0.78)
      space.addColorStop(0, '#081628')
      space.addColorStop(0.55, '#030916')
      space.addColorStop(1, '#01040a')
      ctx.fillStyle = space
      ctx.fillRect(0, 0, width, height)

      // Der Sternenhimmel wird einmal deterministisch vorbereitet und nur
      // gezeichnet. Das hält die Animation auch auf Mobilgeräten leicht.
      for (const star of STAR_FIELD) {
        const twinkle = reducedMotion ? 0.72 : 0.72 + Math.sin(pulse * 0.35 + star.phase) * 0.18
        ctx.fillStyle = `rgba(186, 220, 255, ${star.alpha * twinkle})`
        ctx.fillRect(star.x * width, star.y * height, star.size, star.size)
      }

      // Eine entfernte, unscharfe Planetenscheibe und eine seitliche Sonne
      // geben Tiefe, ohne das eigentliche Lagebild zu überladen.
      const distantPlanet = ctx.createRadialGradient(width * 0.84, height * 0.18, 0, width * 0.84, height * 0.18, Math.min(width, height) * 0.11)
      distantPlanet.addColorStop(0, 'rgba(147, 197, 253, 0.38)')
      distantPlanet.addColorStop(0.7, 'rgba(49, 83, 132, 0.16)')
      distantPlanet.addColorStop(1, 'rgba(15, 23, 42, 0)')
      ctx.fillStyle = distantPlanet
      ctx.beginPath()
      ctx.arc(width * 0.84, height * 0.18, Math.min(width, height) * 0.11, 0, Math.PI * 2)
      ctx.fill()

      const sunlight = ctx.createRadialGradient(width * 0.04, height * 0.14, 0, width * 0.04, height * 0.14, Math.min(width, height) * 0.55)
      sunlight.addColorStop(0, 'rgba(255, 241, 190, 0.23)')
      sunlight.addColorStop(0.22, 'rgba(253, 186, 116, 0.08)')
      sunlight.addColorStop(1, 'rgba(251, 191, 36, 0)')
      ctx.fillStyle = sunlight
      ctx.fillRect(0, 0, width, height)

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
      } else if (autoRotate && !isDraggingRef.current && !reducedMotion) {
        rotRef.current.y += 0.002
      }

      if (!reducedMotion) pulse = (pulse + 0.05) % (Math.PI * 2)

      // 1. Atmosphärischer Glüheffekt (Atmospheric Halo Glow)
      const glowGrad = ctx.createRadialGradient(cx, cy, radius * 0.82, cx, cy, radius * 1.35)
      glowGrad.addColorStop(0, 'rgba(56, 189, 248, 0.32)')
      glowGrad.addColorStop(0.4, 'rgba(14, 165, 233, 0.16)')
      glowGrad.addColorStop(1, 'rgba(14, 165, 233, 0)')

      ctx.fillStyle = glowGrad
      ctx.beginPath()
      ctx.arc(cx, cy, radius * 1.35, 0, Math.PI * 2)
      ctx.fill()

      // 2. Erdkörper (Tiefen-Verlauf mit Ozeanblau und sonniger Kante)
      const earthGrad = ctx.createRadialGradient(
        cx - radius * 0.35,
        cy - radius * 0.35,
        radius * 0.08,
        cx,
        cy,
        radius,
      )
      earthGrad.addColorStop(0, '#245777')
      earthGrad.addColorStop(0.36, '#123b5c')
      earthGrad.addColorStop(0.7, '#08243f')
      earthGrad.addColorStop(0.9, '#030d1b')
      earthGrad.addColorStop(1, '#02060e')

      ctx.save()
      ctx.beginPath()
      ctx.arc(cx, cy, radius, 0, Math.PI * 2)
      ctx.fillStyle = earthGrad
      ctx.fill()
      ctx.clip()

      const rx = rotRef.current.x
      const ry = rotRef.current.y

      const earthTexture = earthTextureRef.current
      const earthTextureCanvas = earthTextureCanvasRef.current
      if (earthTexture && earthTextureCanvas) {
        const now = performance.now()
        if (now - lastTextureRenderRef.current >= 33 || earthTextureCanvas.width === 0) {
          drawEarthTexture(ctx, earthTexture, earthTextureCanvas, rx, ry, cx, cy, radius)
          lastTextureRenderRef.current = now
        } else {
          ctx.drawImage(earthTextureCanvas, cx - radius, cy - radius, radius * 2, radius * 2)
        }
      }

      // Feine Strömungsbänder lassen den Ozean nach Oberfläche aussehen, nicht
      // nach einer flachen blauen Scheibe.
      if (!earthTexture) {
        ctx.strokeStyle = 'rgba(125, 211, 252, 0.055)'
        ctx.lineWidth = 0.65
        for (let band = -0.8; band <= 0.8; band += 0.12) {
          ctx.beginPath()
          for (let x = -radius; x <= radius; x += 7) {
            const y = band * radius + Math.sin(x * 0.055 + pulse) * radius * 0.012
            if (x === -radius) ctx.moveTo(cx + x, cy + y)
            else ctx.lineTo(cx + x, cy + y)
          }
          ctx.stroke()
        }
      }

      // Vorbereitete Landtextur: Gelände-, Vegetations- und Nachtlichtpunkte
      // liegen exakt auf der rotierenden Kugel und werden am Horizont gekappt.
      if (!earthTexture) {
        for (const point of LAND_TEXTURE) {
          const projected = projectPoint(point.lat, point.lon, rx, ry, cx, cy, radius)
          if (projected.rotZ <= 0) continue
          const day = Math.max(0, projected.rotX * -0.55 + projected.rotY * 0.2 + 0.6)
          const size = Math.max(0.7, 1.35 * projected.rotZ)
          ctx.fillStyle = point.light && day < 0.55
            ? `rgba(255, 204, 112, ${(0.25 + (0.55 - day) * 0.45) * projected.rotZ})`
            : `rgba(73, 128, 93, ${(0.22 + point.tone * 0.18) * projected.rotZ})`
          ctx.fillRect(projected.sx, projected.sy, size, size)
        }
      }

      // 3. Küstenlinien über der Textur für gut lesbare Kontinente
      ctx.strokeStyle = earthTexture ? 'rgba(205, 238, 255, 0.22)' : 'rgba(168, 230, 194, 0.72)'
      ctx.lineWidth = 1.15
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
  }, [zoom, autoRotate, effLat, effLon, effLocation, effBbox, textureVersion])

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
    e.stopPropagation()
    setZoom((z) => Math.max(0.4, Math.min(3.5, z - e.deltaY * 0.0015)))
  }

  const handleTouchPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (e.pointerType === 'mouse') return
    e.preventDefault()
    isDraggingRef.current = true
    setAutoRotate(false)
    targetRotRef.current = null
    lastMouseRef.current = { x: e.clientX, y: e.clientY }
    e.currentTarget.setPointerCapture(e.pointerId)
  }

  const handleTouchPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (e.pointerType === 'mouse' || !isDraggingRef.current) return
    e.preventDefault()
    const dx = e.clientX - lastMouseRef.current.x
    const dy = e.clientY - lastMouseRef.current.y
    rotRef.current.y += dx * 0.008
    rotRef.current.x = Math.max(-1.4, Math.min(1.4, rotRef.current.x + dy * 0.008))
    lastMouseRef.current = { x: e.clientX, y: e.clientY }
  }

  const handleTouchPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (e.pointerType === 'mouse') return
    isDraggingRef.current = false
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
  }

  const rawDt = satellite?.scenes?.[0]?.datetime
  // Der Einstieg bleibt bewusst in der Kugelprojektion. Erst die gleiche
  // Wheel-/Pinch-Geste wie beim alten Globus führt in die Detailkarten.
  const mapTilerGlobeZoom = effBbox && effBbox.length === 4 && Math.max(Math.abs(effBbox[2] - effBbox[0]), Math.abs(effBbox[3] - effBbox[1])) > 25
    ? 1.2
    : 2.2
  let aktualitaetText = t('ai.geo.captureTimeUnknown', 'Aufnahmezeit unbekannt')
  if (rawDt) {
    const d = new Date(rawDt)
    if (!isNaN(d.getTime())) {
      aktualitaetText = d.toLocaleDateString(undefined, { day: '2-digit', month: '2-digit' })
    }
  }

  const satelliteMission = satellite?.scenes?.[0]?.mission || 'Sentinel-2'

  return (
    <div
      className={`relative flex h-full w-full min-h-[320px] flex-col items-center justify-center overflow-hidden overscroll-contain rounded-2xl bg-surface-container-lowest border border-outline-variant/30 ${className}`}
      onWheelCapture={(e) => {
        e.preventDefault()
      }}
    >
      <canvas
        ref={canvasRef}
        className="h-full w-full touch-none cursor-grab active:cursor-grabbing"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onWheel={handleWheel}
        onPointerDown={handleTouchPointerDown}
        onPointerMove={handleTouchPointerMove}
        onPointerUp={handleTouchPointerUp}
        onPointerCancel={handleTouchPointerUp}
        aria-label={`3D Globus Ansicht ${effLocation ? `für ${effLocation}` : ''}`}
      />

      {!mapTilerUnavailable && typeof effLat === 'number' && typeof effLon === 'number' && (
        <MapTilerDetailMap
          latitude={effLat}
          longitude={effLon}
          centerLatitude={mapCenter?.latitude}
          centerLongitude={mapCenter?.longitude}
          locationName={effLocation || 'Region'}
          globe
          zoom={mapTilerGlobeZoom}
          cameraMode={data?.camera?.mode}
          onUnavailable={handleMapTilerUnavailable}
        />
      )}

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
