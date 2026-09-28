/**
 * Der Schwarm ohne WebGL.
 *
 * Für Geräte, auf denen WebGL fehlt oder nur in Software liefe (Remote-Desktop,
 * gesperrte Grafiktreiber, manche Android-WebViews). Dieselbe Pose, dieselbe
 * Rechnung je Punkt (`placeParticle`) — nur mit 2400 statt 9000 Punkten, die
 * dafür etwas größer und heller sind. Wer zwischen beiden wechselt, erkennt
 * denselben Schwarm wieder.
 *
 * Teuer ist in Canvas 2D nicht das Rechnen, sondern jeder Zeichenaufruf.
 * Deshalb landen die Punkte in wenigen Eimern gleicher Farbe und Helligkeit,
 * und jeder Eimer wird als ein Pfad gefüllt. Nur Sterne und die Zielmarke
 * bekommen einen eigenen Leuchtfleck.
 */
import { camera, edgeFade, radiusPx } from './motion.js'
import { POINT_COVER, frameUniforms, highlights, particles, placeParticle, pointSizeCss } from './particles.js'

/** So viele Punkte rechnet die CPU je Bild. */
export const CANVAS_POINTS = 2400
/** Weniger Punkte, dafür größer und heller: die Kugel soll gleich dicht wirken. */
const SIZE_BOOST = 1.35
const LIGHT_BOOST = 1.6
const LEVELS = 8
/** Ab diesem Größenfaktor ist ein Punkt ein Stern und leuchtet einzeln. */
const STAR_SIZE = 1.9

const clamp = (x, lo, hi) => (x < lo ? lo : x > hi ? hi : x)

function rgb(color) {
  return `rgb(${Math.round(clamp(color[0], 0, 1) * 255)}, ${Math.round(clamp(color[1], 0, 1) * 255)}, ${Math.round(clamp(color[2], 0, 1) * 255)})`
}

function lerp3(a, b, t) {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]
}

export class CanvasRenderer {
  static create(canvas) {
    let context = null
    try {
      context = canvas.getContext('2d')
    } catch {
      context = null
    }
    return context ? new CanvasRenderer(canvas, context) : null
  }

  constructor(canvas, context) {
    this.kind = 'canvas'
    this.canvas = canvas
    this.ctx = context
    this.data = particles()
    this.place = new Float32Array(7)
    this.sprites = new Map()
    // Zwei Farbfamilien (Grundton, Eis) × acht Helligkeiten.
    this.buckets = Array.from({ length: LEVELS * 2 }, () => [])
    this.stars = []
  }

  draw(pose, view) {
    const { ctx, data, place } = this
    const { width, height, pixelRatio } = view
    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.globalCompositeOperation = 'source-over'
    ctx.globalAlpha = 1
    ctx.clearRect(0, 0, width, height)

    const pv = camera(width / height)
    const R = radiusPx(pv, 1, height)
    const u = frameUniforms(pose)
    const count = Math.round(CANVAS_POINTS * clamp(view.detail ?? 1, 0.3, 1))
    const size = pointSizeCss(R / pixelRatio) * pixelRatio * SIZE_BOOST

    ctx.globalCompositeOperation = 'lighter'
    const [cx0, cy0] = this.project(pv, 0, 0, 0, width, height)
    this.glowDot(cx0, cy0, R * 1.45, pose.color, 0.1 + 0.22 * pose.inner)

    for (const bucket of this.buckets) bucket.length = 0
    this.stars.length = 0
    for (let k = 0; k < count; k += 1) {
      placeParticle(data, k, u, place)
      const x = place[0]
      const y = place[1]
      const z = place[2]
      const w = pv[3] * x + pv[7] * y + pv[11] * z + pv[15]
      if (w <= 0.1) continue
      const sx = ((pv[0] * x + pv[4] * y + pv[8] * z + pv[12]) / w * 0.5 + 0.5) * width
      const sy = (0.5 - ((pv[1] * x + pv[5] * y + pv[9] * z + pv[13]) / w) * 0.5) * height
      const perspective = clamp(4.4 / w, 0.6, 2.2)
      const light = (place[3] + place[6] * 2.5) * LIGHT_BOOST
      // Wie im Shader: zum Rand hin verblassen, nie an ihm abbrechen.
      const alpha = (1 - Math.exp(-light)) * edgeFade((sx / width) * 2 - 1, (sy / height) * 2 - 1)
      if (alpha < 0.02) continue
      const s = size * place[5] * perspective
      if (place[5] >= STAR_SIZE) {
        this.stars.push(sx, sy, s, place[4], alpha)
        continue
      }
      const family = place[4] >= 0.35 ? 1 : 0
      const level = Math.min(LEVELS - 1, Math.floor(alpha * LEVELS))
      this.buckets[family * LEVELS + level].push(sx - s / 2, sy - s / 2, s)
    }

    // Die Punkte mischen wie im Shader: Licht c, Deckung a = c · POINT_COVER,
    // vormultipliziert. In Canvas 2D ist das `source-over` mit der Farbe c / a
    // — über Schwarz leuchten sie wie bisher, über einem hellen Fenster hinter
    // dem Overlay bleiben sie so sichtbar wie in WebGL.
    ctx.globalCompositeOperation = 'source-over'
    const tones = [lerp3(pose.color, pose.ice, 0.12), lerp3(pose.color, pose.ice, 0.6)]
    for (let family = 0; family < 2; family += 1) {
      const tone = tones[family]
      const peak = Math.max(tone[0], tone[1], tone[2], 1e-3)
      for (let level = 0; level < LEVELS; level += 1) {
        const rects = this.buckets[family * LEVELS + level]
        if (rects.length === 0) continue
        const light = (level + 0.5) / LEVELS
        const cover = Math.min(1, peak * light * POINT_COVER)
        ctx.globalAlpha = cover
        ctx.fillStyle = rgb([tone[0] * light / cover, tone[1] * light / cover, tone[2] * light / cover])
        ctx.beginPath()
        for (let i = 0; i < rects.length; i += 3) ctx.rect(rects[i], rects[i + 1], rects[i + 2], rects[i + 2])
        ctx.fill()
      }
    }
    ctx.globalAlpha = 1

    // Sterne sind im Shader Punkte wie alle anderen und mischen deshalb auch
    // hier so: sonst verschwände über einem hellen Fenster gerade das Land
    // der Erde, dessen Punkte groß genug für Sterne sind.
    for (let i = 0; i < this.stars.length; i += 5) {
      const color = lerp3(pose.color, pose.ice, this.stars[i + 3])
      const light = this.stars[i + 4] * 0.85
      const cover = Math.min(1, Math.max(color[0], color[1], color[2], 1e-3) * light * POINT_COVER)
      const shade = [color[0] * light / cover, color[1] * light / cover, color[2] * light / cover]
      this.glowDot(this.stars[i], this.stars[i + 1], this.stars[i + 2] * 0.9, shade, cover)
    }
    ctx.globalCompositeOperation = 'lighter'

    for (const light of highlights(pose)) {
      const [sx, sy] = this.project(pv, ...light.position, width, height)
      this.glowDot(sx, sy, R * light.size, light.color, light.strength)
    }
    ctx.globalCompositeOperation = 'source-over'
  }

  project(pv, x, y, z, width, height) {
    const w = pv[3] * x + pv[7] * y + pv[11] * z + pv[15]
    return [
      ((pv[0] * x + pv[4] * y + pv[8] * z + pv[12]) / w * 0.5 + 0.5) * width,
      (0.5 - ((pv[1] * x + pv[5] * y + pv[9] * z + pv[13]) / w) * 0.5) * height,
    ]
  }

  /**
   * Ein Leuchtfleck als vorgezeichnetes Bild. Ein radialer Verlauf je Fleck
   * kostete ohne Grafikkarte mehr als der ganze Rest des Bildes — und genau
   * dort läuft dieser Zeichner. Die Farbe wird auf 32 Stufen je Kanal
   * gerundet; eine Zustandsüberblendung braucht so eine Handvoll Bilder.
   */
  glowDot(x, y, radius, color, strength) {
    if (strength <= 0 || radius <= 0) return
    const { ctx } = this
    ctx.globalAlpha = Math.min(1, strength)
    ctx.drawImage(this.sprite(color), x - radius, y - radius, radius * 2, radius * 2)
    ctx.globalAlpha = 1
  }

  sprite(color) {
    const key = color.map((c) => Math.round(clamp(c, 0, 1) * 31)).join(',')
    let image = this.sprites.get(key)
    if (!image) {
      image = document.createElement('canvas')
      image.width = 64
      image.height = 64
      const pen = image.getContext('2d')
      const gradient = pen.createRadialGradient(32, 32, 0, 32, 32, 32)
      const [r, g, b] = color.map((c) => Math.round(clamp(c, 0, 1) * 255))
      gradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, 1)`)
      gradient.addColorStop(0.3, `rgba(${r}, ${g}, ${b}, 0.4)`)
      gradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`)
      pen.fillStyle = gradient
      pen.fillRect(0, 0, 64, 64)
      if (this.sprites.size > 48) this.sprites.clear()
      this.sprites.set(key, image)
    }
    return image
  }

  release() {}
}
