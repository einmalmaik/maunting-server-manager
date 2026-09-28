/**
 * Der Schwarm — die Sprachfigur von MSM.
 *
 * Neuntausend Lichtpunkte, die eine Form annehmen: in Ruhe ein Sternenglobus,
 * beim Arbeiten das MSM-Logo (der Ring mit seinen vier Knoten und die zwei
 * Bahnen), bei einer Regionalanalyse die Erde — gedreht zu dem Ort, über den
 * gerade gesprochen wird. Hört Singra zu, laufen Wellen vom Rand zur Mitte und
 * Punkte fliegen herein; spricht sie, laufen die Wellen im Takt der Stimme
 * nach außen und Punkte fliegen hinaus. Denken zieht den Globus zu einem
 * Wirbel auf, eine Störung zerfasert ihn, das Ende der Sitzung legt ihn als
 * Scheibe ab.
 *
 * Der Aufrufer gibt nur einen Zustand, eine Pegelquelle und optional einen
 * Ort. Wie oft gezeichnet wird, in welcher Auflösung, mit wie vielen Punkten
 * und ob mit WebGL oder Canvas 2D, entscheidet diese Datei — auch, dass
 * reduzierte Bewegung aus dem Betriebssystem gilt und ein verlorener
 * GPU-Kontext wiederkommt.
 *
 * Dieses Modul wird nachgeladen (`import('@maunting/design-dna/swarm')`), nie
 * im Startpaket: der Start der App wartet auf keinen Shader.
 */
import { CanvasRenderer } from './canvas.js'
import { GlRenderer } from './gl.js'
import { Choreography, STATES, simulatedSpeech } from './motion.js'

export { STATES as SWARM_STATES, simulatedSpeech }

const STEP = 1 / 60
/** So oft sieht ein ruhender Schwarm nach, ob jemand spricht … */
const LEVEL_WATCH_MS = 100
/** … und ab diesem Pegel wacht er auf — wie `Envelope.silent()` zählt. */
const LEVEL_WAKE = 0.005
/** Über diesem Wert (90. Perzentil der Bildabstände) wird die Auflösung gesenkt. */
const SLOW_FRAME_MS = 22
const QUALITY_MIN = 0.5

function reducedMotionQuery() {
  try {
    return window.matchMedia?.('(prefers-reduced-motion: reduce)') ?? null
  } catch {
    return null
  }
}

class Swarm {
  constructor(host, options) {
    this.host = host
    this.preference = options.renderer ?? 'auto'
    this.maxPixelRatio = options.maxPixelRatio ?? 2
    this.input = {
      state: options.state ?? 'ready',
      level: typeof options.level === 'function' ? options.level : () => 0,
      earth: options.earth ?? null,
    }
    this.pulsePending = false
    this.quality = 1
    this.intervals = []
    this.frames = 0
    this.lastProbe = 0
    this.lastTime = 0
    this.visible = true
    this.running = false
    this.handle = 0
    this.levelWatch = 0
    this.failures = 0
    this.losses = 0
    this.view = { width: 0, height: 0, pixelRatio: 1, detail: 1 }

    const query = options.reducedMotion === undefined || options.reducedMotion === 'auto' ? reducedMotionQuery() : null
    this.choreography = new Choreography({
      reducedMotion: query ? query.matches : options.reducedMotion === true,
    })
    if (query) {
      this.onReducedMotion = (event) => {
        this.choreography.setReducedMotion(event.matches)
        this.kick()
      }
      query.addEventListener?.('change', this.onReducedMotion)
      this.query = query
    }

    this.mount(this.preference)

    if (typeof ResizeObserver === 'function') {
      this.resizeObserver = new ResizeObserver(() => {
        this.resize()
        this.kick()
      })
      this.resizeObserver.observe(host)
    } else {
      this.onWindowResize = () => {
        this.resize()
        this.kick()
      }
      window.addEventListener('resize', this.onWindowResize)
    }
    // Außerhalb des Bildes wird nicht gezeichnet: ein Schwarm, den niemand
    // sieht, soll auch keinen Akku kosten.
    if (typeof IntersectionObserver === 'function') {
      this.intersectionObserver = new IntersectionObserver((entries) => {
        this.visible = entries.some((entry) => entry.isIntersecting)
        if (this.visible) this.kick()
      })
      this.intersectionObserver.observe(host)
    }
    this.resize()
    if (options.autoplay !== false) this.start()
  }

  /** Legt eine frische Leinwand an und holt den passenden Zeichner. */
  mount(preference) {
    this.renderer?.release()
    this.canvas?.remove()
    const canvas = document.createElement('canvas')
    canvas.setAttribute('aria-hidden', 'true')
    canvas.style.display = 'block'
    canvas.style.width = '100%'
    canvas.style.height = '100%'
    canvas.style.pointerEvents = 'none'
    this.host.appendChild(canvas)
    this.canvas = canvas

    let renderer = null
    if (preference !== 'canvas') {
      renderer = GlRenderer.create(canvas, { allowSoftware: preference === 'webgl' })
      if (renderer) {
        canvas.addEventListener('webglcontextlost', this.onContextLost)
        canvas.addEventListener('webglcontextrestored', this.onContextRestored)
      } else {
        // Ein Kontext, der einmal WebGL war, wird nie mehr 2D. Deshalb
        // bekommt der Ersatz eine eigene Leinwand.
        canvas.remove()
        this.canvas = null
        return this.mount('canvas')
      }
    } else {
      renderer = CanvasRenderer.create(canvas)
    }
    this.renderer = renderer
    this.mark()
    this.resize()
    return renderer
  }

  /**
   * Schreibt an die Bühne, womit gezeichnet wird und wie schnell — lesbar in
   * den Entwicklerwerkzeugen jedes Fensters, auch in der Desktop-App, in der
   * es kein Protokoll gibt: `data-swarm-renderer`, `data-swarm-fps`,
   * `data-swarm-quality`.
   */
  mark() {
    const data = this.host.dataset
    if (!data) return
    data.swarmRenderer = this.rendererKind
    if (this.fps) data.swarmFps = String(Math.round(this.fps))
    data.swarmQuality = this.quality.toFixed(2)
  }

  onContextLost = (event) => {
    event.preventDefault()
    this.losses += 1
  }

  onContextRestored = () => {
    if (this.losses > 2 || !this.rebuild()) {
      // Ein Treiber, der den Kontext dreimal verliert, verliert ihn wieder.
      this.mount('canvas')
    }
    this.kick()
  }

  rebuild() {
    try {
      this.renderer.build()
      return true
    } catch {
      return false
    }
  }

  get rendererKind() {
    return this.renderer ? this.renderer.kind : 'none'
  }

  resize() {
    if (!this.canvas) return
    const rect = this.host.getBoundingClientRect()
    const dpr = Math.min(this.maxPixelRatio, window.devicePixelRatio || 1)
    const pixelRatio = Math.max(0.5, dpr * this.quality)
    const width = Math.max(0, Math.round(rect.width * pixelRatio))
    const height = Math.max(0, Math.round(rect.height * pixelRatio))
    if (this.canvas.width !== width) this.canvas.width = width
    if (this.canvas.height !== height) this.canvas.height = height
    // Wer die Auflösung nicht hält, bekommt auch weniger Punkte: der
    // Vertex-Shader kostet je Punkt, gleich wie groß die Leinwand ist.
    this.view = { width, height, pixelRatio, detail: Math.min(1, 0.2 + this.quality) }
  }

  update(input) {
    if (!input) return
    if (typeof input.state === 'string') this.input.state = input.state
    if (typeof input.level === 'function') this.input.level = input.level
    if ('earth' in input) this.input.earth = input.earth ?? null
    this.kick()
  }

  pulse() {
    this.pulsePending = true
    this.kick()
  }

  sampleLevel() {
    try {
      const value = Number(this.input.level())
      return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0
    } catch {
      return 0
    }
  }

  step(dt) {
    const pose = this.choreography.step(
      {
        state: this.input.state,
        level: this.sampleLevel(),
        earth: this.input.earth,
        pulse: this.pulsePending,
      },
      dt,
    )
    this.pulsePending = false
    return pose
  }

  draw(pose) {
    if (!this.renderer || this.view.width < 2 || this.view.height < 2) return
    if (this.renderer.gl?.isContextLost()) return
    try {
      this.renderer.draw(pose, this.view)
      this.failures = 0
    } catch (error) {
      this.failures += 1
      if (this.failures > 3) {
        // Ein Zeichner, der viermal hintereinander wirft, bekommt keine
        // fünfte Gelegenheit. Der einfachere übernimmt, und wenn auch der
        // scheitert, bleibt die Fläche leer — der Zustandstext daneben trägt.
        if (this.renderer.kind === 'webgl') this.mount('canvas')
        else this.renderer = null
        this.mark()
        this.failures = 0
      }
      if (typeof console !== 'undefined') console.warn('Schwarm: Zeichnen fehlgeschlagen', error)
    }
  }

  /** Spult den Schwarm in festen Schritten vor und zeichnet einmal. Für Tests und Bildschirmfotos. */
  advance(seconds) {
    const steps = Math.max(1, Math.round(seconds / STEP))
    let pose = null
    for (let i = 0; i < steps; i += 1) pose = this.step(STEP)
    this.draw(pose)
    return pose
  }

  start() {
    if (this.running || typeof requestAnimationFrame !== 'function') return
    this.running = true
    this.lastTime = 0
    this.handle = requestAnimationFrame(this.frame)
  }

  /** Nach einer Änderung sofort wieder zeichnen, falls die Schleife wartet. */
  kick() {
    if (this.running && !this.handle) this.handle = requestAnimationFrame(this.frame)
  }

  frame = (now) => {
    this.handle = 0
    if (!this.running) return
    const dt = this.lastTime ? Math.min(0.05, (now - this.lastTime) / 1000) : STEP
    const interval = this.lastTime ? now - this.lastTime : 0
    this.lastTime = now
    if (!this.visible) {
      // Keine neue Anforderung: der IntersectionObserver weckt die Schleife.
      this.lastTime = 0
      return
    }
    this.frames += 1
    this.adapt(interval, now)

    this.draw(this.step(dt))
    // In Ruhe (eingeschwungen, stumm, ohne Bewegung) ändert sich nichts
    // mehr: die Schleife schläft, bis `update()` oder `pulse()` sie weckt.
    if (this.choreography.resting()) {
      this.lastTime = 0
      // Bei reduzierter Bewegung ruht der Schwarm auch mitten im Gespräch.
      // Den Pegel liest dann niemand für ihn — ein Blick alle 100 ms weckt
      // ihn, sobald jemand spricht.
      if (this.choreography.mixer.target !== 'off') this.watchLevel()
      return
    }
    this.handle = requestAnimationFrame(this.frame)
  }

  watchLevel() {
    clearTimeout(this.levelWatch)
    this.levelWatch = setTimeout(() => {
      this.levelWatch = 0
      if (!this.running || this.handle) return
      if (this.sampleLevel() >= LEVEL_WAKE) this.kick()
      else this.watchLevel()
    }, LEVEL_WATCH_MS)
  }

  /**
   * Die Auflösung folgt dem Gerät. Hält es 45 Bilder je Sekunde nicht, wird
   * die Leinwand gröber und der Schwarm dünner — weiche Punkte verzeihen das,
   * ein Ruckeln sieht man sofort. Nach einer halben Minute wird vorsichtig
   * wieder feiner versucht.
   */
  adapt(interval, now) {
    if (this.frames < 30 || interval <= 0 || interval > 250) return
    this.intervals.push(interval)
    if (this.intervals.length < 60) return
    const sorted = [...this.intervals].sort((a, b) => a - b)
    const p90 = sorted[Math.floor(sorted.length * 0.9)]
    this.intervals.length = 0
    if (p90 > SLOW_FRAME_MS && this.quality > QUALITY_MIN) {
      this.quality = Math.max(QUALITY_MIN, this.quality * 0.8)
      this.lastProbe = now
      this.resize()
    } else if (this.quality < 1 && p90 < 18 && now - this.lastProbe > 30_000) {
      this.quality = Math.min(1, this.quality * 1.15)
      this.lastProbe = now
      this.resize()
    }
    this.fps = 1000 / (sorted.reduce((sum, value) => sum + value, 0) / sorted.length)
    this.mark()
  }

  stats() {
    return {
      renderer: this.rendererKind,
      fps: this.fps ?? null,
      quality: this.quality,
      detail: this.view.detail,
      width: this.view.width,
      height: this.view.height,
      pixelRatio: this.view.pixelRatio,
      reducedMotion: this.choreography.reduced,
    }
  }

  destroy() {
    this.running = false
    if (this.handle) cancelAnimationFrame(this.handle)
    this.handle = 0
    clearTimeout(this.levelWatch)
    this.levelWatch = 0
    this.resizeObserver?.disconnect()
    this.intersectionObserver?.disconnect()
    if (this.onWindowResize) window.removeEventListener('resize', this.onWindowResize)
    this.query?.removeEventListener?.('change', this.onReducedMotion)
    this.renderer?.release()
    this.renderer = null
    this.canvas?.remove()
    this.canvas = null
    const data = this.host.dataset
    if (data) {
      delete data.swarmRenderer
      delete data.swarmFps
      delete data.swarmQuality
    }
  }
}

/**
 * Setzt einen Schwarm in `host`. Die Größe bestimmt `host` per CSS; die
 * Leinwand füllt ihn aus und folgt jeder Größenänderung.
 */
export function createSwarm(host, options = {}) {
  const swarm = new Swarm(host, options)
  return {
    get renderer() {
      return swarm.rendererKind
    },
    update: (input) => swarm.update(input),
    pulse: () => swarm.pulse(),
    advance: (seconds) => {
      swarm.advance(seconds)
    },
    stats: () => swarm.stats(),
    destroy: () => swarm.destroy(),
  }
}
