/**
 * Die Bewegung des Schwarms — ohne ein einziges Pixel.
 *
 * Hier steht, was der Schwarm tut: welche Form gerade gilt, wie schnell er
 * sich dreht, wie weit der Wirbel beim Denken schon aufgezogen ist, wohin
 * sich die Erde wendet. Beide Zeichner (`gl.js` und `canvas.js`) bekommen
 * daraus je Bild eine Pose und rechnen nur noch aus, wo jeder Punkt steht
 * und wie hell er ist. So bewegt sich der Schwarm ohne WebGL genauso wie mit,
 * nur mit weniger Punkten.
 *
 * Nichts hier ist ein abgespielter Film. Zustände blenden über Gewichte
 * ineinander, die Erde dreht sich über Federn zum Ort, und die Wellen beim
 * Hören und Sprechen kommen aus dem echten Pegelverlauf der letzten 0,8
 * Sekunden. Wechselt der Zustand mitten in einer Geste, biegt die Form ab,
 * statt abzureißen.
 */
import { voiceStateTones, voiceTokens } from '../index.js'

export const STATES = Object.freeze([
  'off',
  'connecting',
  'ready',
  'listening',
  'thinking',
  'working',
  'speaking',
  'fault',
  'expired',
])

const TAU = Math.PI * 2

// ── Matrizen, spaltenweise wie WebGL sie will ─────────────────────────────

export const M = {
  identity: () => new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]),
  mul(a, b) {
    const o = new Float32Array(16)
    for (let i = 0; i < 4; i += 1) {
      for (let j = 0; j < 4; j += 1) {
        let s = 0
        for (let k = 0; k < 4; k += 1) s += a[k * 4 + j] * b[i * 4 + k]
        o[i * 4 + j] = s
      }
    }
    return o
  },
  perspective(fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2)
    const nf = 1 / (near - far)
    return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0])
  },
  translate: (x, y, z) => new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]),
  rotX(a) {
    const c = Math.cos(a)
    const s = Math.sin(a)
    return new Float32Array([1, 0, 0, 0, 0, c, s, 0, 0, -s, c, 0, 0, 0, 0, 1])
  },
  rotY(a) {
    const c = Math.cos(a)
    const s = Math.sin(a)
    return new Float32Array([c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1])
  },
  /** Das obere 3 × 3 einer mat4 (reine Drehung), spaltenweise. */
  mat3(m) {
    return new Float32Array([m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]])
  },
  /** mat3 mal Vektor, spaltenweise. */
  apply3(m, x, y, z) {
    return [
      m[0] * x + m[3] * y + m[6] * z,
      m[1] * x + m[4] * y + m[7] * z,
      m[2] * x + m[5] * y + m[8] * z,
    ]
  },
}

/** Abstand der Kamera zur Mitte, in Welteinheiten. Der Globus hat Radius 1. */
export const CAMERA_DISTANCE = 5

/**
 * So viel der halben kürzeren Seite füllt der Globus. Der Rest ist Luft für
 * alles, was über ihn hinausgeht: Flieger beim Hören und Sprechen, die Wolke
 * beim Verbinden, der Lichtring eines Werkzeugs. Was bis an den Rand kommt,
 * blendet dort aus (`EDGE_FADE`) — eine Kante, an der der Schwarm
 * abgeschnitten wird, gibt es nicht.
 */
export const GLOBE_FILL = 0.45

/** Ab diesem Anteil der halben Breite oder Höhe verblassen die Punkte zum Rand hin. */
export const EDGE_FADE = Object.freeze([0.72, 0.98])

/**
 * Projektion und Blick für ein Seitenverhältnis. Der Blickwinkel richtet sich
 * nach der kürzeren Seite: im Hochformat nach der Breite, sonst nach der
 * Höhe — so hat der Schwarm im schmalen Telefon denselben Rand wie im Panel.
 */
export function camera(aspect) {
  const focal = GLOBE_FILL * Math.min(1, aspect) * CAMERA_DISTANCE
  const fovy = 2 * Math.atan(1 / focal)
  return M.mul(M.perspective(fovy, aspect, 0.1, 30), M.translate(0, 0, -CAMERA_DISTANCE))
}

/** 1 in der Fläche, 0 am Rand, weich dazwischen — für einen Punkt in Normalkoordinaten (−1 … 1). */
export function edgeFade(x, y) {
  const m = Math.max(Math.abs(x), Math.abs(y))
  const t = Math.min(1, Math.max(0, (m - EDGE_FADE[0]) / (EDGE_FADE[1] - EDGE_FADE[0])))
  return 1 - t * t * (3 - 2 * t)
}

/** Wie viele Pixel ein Weltradius in der Mitte der Szene misst. */
export function radiusPx(pv, radius, heightPx) {
  return ((pv[5] * radius) / CAMERA_DISTANCE) * heightPx * 0.5
}

// ── Farben aus der Design-DNA ────────────────────────────────────────────

/** `"190 92% 62%"` → `[r, g, b]` zwischen 0 und 1. */
export function hslTriplet(text) {
  const [h, s, l] = String(text)
    .trim()
    .split(/\s+/)
    .map((teil) => parseFloat(teil))
  const sat = s / 100
  const light = l / 100
  const k = (n) => (n + h / 30) % 12
  const a = sat * Math.min(light, 1 - light)
  const f = (n) => light - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)))
  return [f(0), f(8), f(4)]
}

export const PALETTE = Object.freeze({
  glow: hslTriplet(voiceTokens.glow),
  ice: hslTriplet(voiceTokens.ice),
  think: hslTriplet(voiceTokens.think),
  fault: hslTriplet(voiceTokens.fault),
  rest: hslTriplet(voiceTokens.rest),
})

/**
 * Die Farbe je Zustand. Welcher Ton zu welchem Zustand gehört, steht in
 * `voiceStateTones` — dort liest auch die Zustandsmarke neben dem Schwarm.
 */
const STATE_COLORS = Object.freeze(
  Object.fromEntries(STATES.map((state) => [state, PALETTE[voiceStateTones[state]]])),
)

/** Gewichtete Mitte einer Kennzahl über alle Zustände. */
export function mix(weights, table) {
  let sum = 0
  let total = 0
  for (const state of STATES) {
    const w = weights[state] ?? 0
    if (w === 0 || table[state] === undefined) continue
    sum += table[state] * w
    total += w
  }
  return total > 1e-4 ? sum / total : 0
}

export function mixColor(weights, table, fallback) {
  let r = 0
  let g = 0
  let b = 0
  let total = 0
  for (const state of STATES) {
    const w = weights[state] ?? 0
    const color = table[state]
    if (w === 0 || !color) continue
    r += color[0] * w
    g += color[1] * w
    b += color[2] * w
    total += w
  }
  return total > 1e-4 ? [r / total, g / total, b / total] : fallback
}

const clamp01 = (x) => (x > 0 ? (x < 1 ? x : 1) : 0)

// ── Bausteine der Bewegung ───────────────────────────────────────────────

/**
 * Zustände überblenden statt schalten. Jeder Zustand hat ein Gewicht, das weich
 * gegen 0 oder 1 läuft — so wird aus dem Globus das Logo, ohne dass ein Punkt
 * springt. Die Gewichte ergeben zusammen immer 1.
 */
export class StateMixer {
  constructor(start = 'ready') {
    this.target = STATES.includes(start) ? start : 'ready'
    this.weights = {}
    for (const state of STATES) this.weights[state] = state === this.target ? 1 : 0
  }

  set(state) {
    if (STATES.includes(state)) this.target = state
  }

  step(dt, tau = 0.32) {
    const k = 1 - Math.exp(-dt / tau)
    for (const state of STATES) {
      const goal = state === this.target ? 1 : 0
      this.weights[state] += (goal - this.weights[state]) * k
    }
  }

  settled() {
    return STATES.every((state) => Math.abs(this.weights[state] - (state === this.target ? 1 : 0)) < 1e-3)
  }
}

/** So viele Stützstellen hat der Pegelverlauf, eine alle 25 ms. */
export const HISTORY_LENGTH = 32
const HISTORY_STEP = 0.025

/**
 * Die Hüllkurve des Pegels: schnell hoch, langsam runter — wie ein Ohr. Dazu
 * der Verlauf der letzten 0,8 Sekunden (`history[0]` ist der jüngste Wert):
 * aus ihm laufen die Wellen über den Globus. Ein Punkt am Rand zeigt, was vor
 * 0,4 Sekunden zu hören war, einer in der Mitte, was eben kam.
 */
export class Envelope {
  constructor() {
    this.value = 0
    this.history = new Float32Array(HISTORY_LENGTH)
    this.clock = 0
  }

  step(raw, dt) {
    const x = clamp01(Number.isFinite(raw) ? raw : 0)
    const a = x > this.value ? 1 - Math.exp(-dt / 0.045) : 1 - Math.exp(-dt / 0.16)
    this.value += (x - this.value) * a
    this.clock += dt
    while (this.clock >= HISTORY_STEP) {
      this.clock -= HISTORY_STEP
      this.history.copyWithin(1, 0)
      this.history[0] = this.value
    }
    return this
  }

  /** Stumm und ohne Nachhall — dann ändert sich an den Wellen nichts mehr. */
  silent() {
    return this.value < 0.005 && this.history.every((v) => v < 0.005)
  }
}

/** Winkeldifferenz modulo einer Periode. */
export function wrapDifference(target, current, period) {
  const d = target - current
  return period > 0 ? d - period * Math.round(d / period) : d
}

/** Eine kritisch gedämpfte Feder: läuft ihrem Ziel nach, ohne zu überschwingen. */
export class Spring {
  constructor(value = 0, stiffness = 16) {
    this.value = value
    this.velocity = 0
    this.stiffness = stiffness
    this.damping = 2 * Math.sqrt(stiffness)
  }

  step(target, dt, period = 0) {
    const d = wrapDifference(target, this.value, period)
    const accel = this.stiffness * d - this.damping * this.velocity
    this.velocity += accel * dt
    this.value += this.velocity * dt
    return this.value
  }

  snap(value) {
    this.value = value
    this.velocity = 0
  }

  /** Am Ziel und ohne Schwung — weniger als ein Hundertstelpixel am Globus. */
  settledAt(target, period = 0) {
    return Math.abs(wrapDifference(target, this.value, period)) < 1e-4 && Math.abs(this.velocity) < 1e-4
  }
}

// ── Zufall mit Saat ──────────────────────────────────────────────────────

/** Ein kleiner Zufallsgenerator mit Saat: dieselbe Saat, dieselbe Folge. */
export function mulberry32(seed) {
  let a = seed | 0
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/**
 * Sprache ohne Mikrofon: Phrasen aus Silben, Pausen dazwischen. Für das
 * Schaufenster, für Tests und für Bildschirmfotos. Bewusst keine Sinuswelle —
 * an der sähe jede Animation gut aus und bewiese nichts.
 */
export function simulatedSpeech(seed = 7) {
  const random = mulberry32(seed)
  const syllables = []
  let t = 0
  while (t < 900) {
    const count = 3 + Math.floor(random() * 10)
    const loudness = 0.55 + random() * 0.45
    for (let i = 0; i < count; i += 1) {
      const duration = 0.1 + random() * 0.17
      syllables.push({ start: t, duration, amp: loudness * (0.35 + random() * 0.65) })
      t += duration + 0.015 + random() * 0.07
    }
    t += 0.3 + random() * 0.9
  }
  return (seconds) => {
    const z = ((seconds % 900) + 900) % 900
    let lo = 0
    let hi = syllables.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (syllables[mid].start <= z) lo = mid
      else hi = mid - 1
    }
    let level = 0
    for (let i = Math.max(0, lo - 1); i <= Math.min(syllables.length - 1, lo + 1); i += 1) {
      const s = syllables[i]
      const u = (z - s.start) / s.duration
      if (u > 0 && u < 1) level += s.amp * Math.sin(Math.PI * u) ** 0.8
    }
    return Math.min(1, level)
  }
}

// ── Die Choreografie ─────────────────────────────────────────────────────

/** So lange läuft der Lichtring, den ein startendes Werkzeug auslöst. */
const PULSE_SECONDS = 1.1
/** Beim Arbeiten mit Erde rückt sie auf diesen Radius, der Logo-Ring liegt bei 1,08. */
export const EARTH_WORKING_RADIUS = 0.8

/**
 * Aus Zustand, Pegel und Zeit wird eine Pose. Die Zeit zählt hier selbst mit
 * (Summe der Schritte), nicht die Wanduhr: so rechnet `advance()` in Tests und
 * für Bildschirmfotos exakt dasselbe wie der laufende Betrieb.
 */
export class Choreography {
  constructor({ reducedMotion = false } = {}) {
    this.reduced = Boolean(reducedMotion)
    this.mixer = new StateMixer('ready')
    this.envelope = new Envelope()
    this.time = 0
    this.motionTime = 0
    this.spin = 0
    this.vortex = [0, 0, 0]
    this.pulse = 0
    this.earthWeight = 0
    this.earthTarget = null
    this.earthSettled = true
    this.latitude = new Spring(0, 5)
    this.longitude = new Spring(0, 5)
  }

  setReducedMotion(value) {
    this.reduced = Boolean(value)
  }

  /**
   * Ob sich nichts mehr bewegt, was ein neues Bild bräuchte. Die Zeit des
   * Schwarms steht, wenn er aus ist oder Bewegung reduziert sein soll; dann
   * ändert ein Bild nur noch, was gerade übergeht: Zustand, Pegel, Impuls und
   * die Erde. Eine Erde, die steht, ist kein Grund mehr zu zeichnen — nach
   * dem Ende einer Sitzung über einer Region lief der Schwarm sonst weiter,
   * sechzigmal je Sekunde dasselbe Bild.
   */
  resting() {
    return (
      (this.reduced || this.mixer.target === 'off') &&
      this.mixer.settled() &&
      this.envelope.silent() &&
      this.pulse === 0 &&
      this.earthSettled
    )
  }

  step(input, dtRaw) {
    const dt = Math.min(0.1, Math.max(0, dtRaw))
    this.time += dt
    const calm = this.reduced

    this.mixer.set(input.state)
    this.mixer.step(dt)
    const g = this.mixer.weights
    const envelope = this.envelope.step(input.level, dt)
    const level = envelope.value

    // Die Bewegungszeit steht bei reduzierter Bewegung, und sie läuft aus,
    // sobald „aus" überwiegt: ein ausgeschalteter Schwarm treibt nicht mehr.
    const activity = calm ? 0 : 1 - g.off
    this.motionTime += dt * activity
    const tm = this.motionTime

    if (input.pulse) this.pulse = 1
    this.pulse = Math.max(0, this.pulse - dt / PULSE_SECONDS)

    // ── Der Sternenglobus dreht sich; Denken und Arbeiten schneller ──
    this.spin += dt * activity * (0.12 + 0.3 * g.thinking + 0.2 * g.working - 0.08 * g.listening)
    const tilt = mix(g, {
      ready: -0.25,
      listening: -0.1,
      speaking: -0.18,
      thinking: -0.35,
      working: -0.2,
      fault: -0.25,
      expired: -0.35,
      off: -0.25,
      connecting: -0.25,
    })
    const view = M.mat3(M.mul(M.rotX(tilt), M.rotY(this.spin)))

    // ── Der Wirbel beim Denken ──
    // Er beginnt jedes Mal bei null: ein Globus, der ins Denken kommt, zieht
    // sich auf, statt mit einem Ruck in einen fertigen Wirbel zu springen.
    if (g.thinking < 0.002) {
      this.vortex = [0, 0, 0]
    } else if (!calm) {
      this.vortex[0] += dt * 0.5 * g.thinking
      this.vortex[1] += dt * 1.6 * g.thinking
      this.vortex[2] = Math.min(0.9, this.vortex[2] + dt * 0.9 * g.thinking)
    }

    // ── Die Erde ──
    const earth = input.earth
    const hasEarth = Boolean(earth && Number.isFinite(earth.latitude) && Number.isFinite(earth.longitude))
    let earthGoal = null
    if (hasEarth) {
      const fresh = !this.earthTarget || this.earthWeight < 0.02
      this.earthTarget = { latitude: earth.latitude, longitude: earth.longitude }
      const phi = (earth.latitude * Math.PI) / 180
      const lambda = (earth.longitude * Math.PI) / 180
      // Eine frisch erscheinende Erde steht schon richtig. Eine, die schon da
      // ist, dreht sich zum neuen Ort — das ist die Geste „ich schaue hin".
      if (fresh || calm) {
        this.latitude.snap(phi * 0.85)
        this.longitude.snap(lambda)
      }
      earthGoal = [phi * 0.85, lambda + (calm ? 0 : 0.08 * Math.sin(tm * 0.3))]
      this.latitude.step(earthGoal[0], dt)
      this.longitude.step(earthGoal[1], dt, TAU)
    }
    this.earthWeight += ((hasEarth ? 1 : 0) - this.earthWeight) * (1 - Math.exp(-dt / 0.45))
    if (!hasEarth && this.earthWeight < 0.005) this.earthWeight = 0
    if (hasEarth && this.earthWeight > 0.998) this.earthWeight = 1
    this.earthSettled =
      this.earthWeight === 0 ||
      (earthGoal !== null &&
        this.earthWeight === 1 &&
        this.latitude.settledAt(earthGoal[0]) &&
        this.longitude.settledAt(earthGoal[1], TAU))
    const e = this.earthWeight

    // ── Farbe und Helligkeit ──
    const color = mixColor(g, STATE_COLORS, PALETTE.glow)
    // Störung: die Helligkeit knickt ein bis zwei Mal je Sekunde weich ein.
    // Ein Flackern wäre deutlicher, läge aber über der Grenze von drei
    // Blitzen je Sekunde.
    const dip = calm ? 0 : g.fault * 0.35 * Math.max(0, Math.sin(tm * 2.1) * Math.sin(tm * 3.7 + 1.3)) ** 4
    // Das Licht in der Mitte gibt der Wolke Volumen; es folgt der Stimme.
    const inner =
      (mix(g, {
        ready: 0.3,
        listening: 0.3 + 0.8 * level,
        thinking: 0.5 + 0.08 * Math.sin(tm * 2),
        working: 0.55,
        speaking: 0.35 + 1.0 * level,
        fault: 0.3,
        expired: 0.08,
        off: 0.03,
        connecting: 0.2,
      }) +
        this.pulse * 0.5) *
      (1 - 0.6 * e)

    // Das Logo steht zur Kamera und wiegt sich nur leicht, damit die zwei
    // Bahnen als Raum lesbar werden.
    const logoTurn = M.mat3(M.mul(M.rotY(0.2 * Math.sin(tm * 0.35)), M.rotX(0.08 * Math.sin(tm * 0.27))))

    let earthPose = null
    if (e > 0 && this.earthTarget) {
      const phi = (this.earthTarget.latitude * Math.PI) / 180
      const lambda = (this.earthTarget.longitude * Math.PI) / 180
      const rotation4 = M.mul(M.rotX(this.latitude.value), M.rotY(-this.longitude.value))
      earthPose = {
        weight: e,
        rotation: M.mat3(rotation4),
        target: [Math.cos(phi) * Math.sin(lambda), Math.sin(phi), Math.cos(phi) * Math.cos(lambda)],
        radius: 1 - (1 - EARTH_WORKING_RADIUS) * g.working,
      }
    }

    return {
      time: this.time,
      motionTime: tm,
      reduced: calm,
      weights: g,
      level,
      history: envelope.history,
      pulse: this.pulse,
      // Bei reduzierter Bewegung läuft kein Ring nach außen; der Schwarm
      // hellt nur einmal kurz auf.
      pulseRadius: calm ? -1 : (1 - this.pulse) * 1.9,
      color,
      ice: PALETTE.ice,
      brightness: 1 - dip,
      inner,
      view,
      logoTurn,
      vortex: this.vortex,
      earth: earthPose,
    }
  }
}
