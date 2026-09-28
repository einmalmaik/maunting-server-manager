/**
 * Die Punkte des Schwarms und wo sie stehen.
 *
 * Jeder Punkt kennt alle Formen: seinen Platz auf dem Sternenglobus, seinen
 * Platz auf der Erde (meist dieselbe Richtung; die meisten Meerespunkte
 * wandern dort an Land), seinen Platz im Logo und ob er beim Arbeiten mit
 * Erde zum Ring wechselt. Welche Form gilt, entscheidet der Zustand.
 *
 * `placeParticle` ist die Rechnung des WebGL-Vertex-Shaders in JavaScript.
 * Der Canvas-Zeichner nimmt sie für seine kleinere Auswahl, und die Tests
 * prüfen an ihr, was eine Form ausmacht. Wer den Shader in `gl.js` ändert,
 * ändert diese Rechnung mit — sonst sieht der Schwarm ohne WebGL anders aus.
 */
import { isLand } from './landmask.js'
import { mulberry32 } from './motion.js'

/** So viele Punkte zeichnet WebGL. Genug für eine Erde mit Küsten, wenig genug fürs Telefon. */
export const SWARM_SIZE = 9000

/** Werte je Punkt: Richtung (3), Zufall (4), Logo-Platz (3), Land, Trabant, Platz auf der Erde (3). */
export const STRIDE = 15

/**
 * So viele Meerespunkte wandern auf der Erde an Land. Das Meer bedeckt gut
 * sieben Zehntel der Kugel; blieben alle Punkte, wo sie sind, zeichneten
 * nicht einmal 1300 von ihnen die Kontinente, und die Küsten zerfielen. Der
 * Rest des Meeres bleibt als Hauch, damit die Kugel ihren Umriss behält.
 */
const SEA_TO_LAND = 0.75

/** Der Ring des Logos und die zwei Bahnen, gekippt um die y- bzw. x-Achse. */
export const RING_RADIUS = 1.08
const ORBIT_A = 1.15
const ORBIT_B = 1.2
/** Die Normalen der beiden Bahnebenen: um sie fließen die Punkte beim Arbeiten. */
export const ORBIT_A_AXIS = [Math.sin(ORBIT_A), 0, Math.cos(ORBIT_A)]
export const ORBIT_B_AXIS = [0, -Math.sin(ORBIT_B), Math.cos(ORBIT_B)]

/**
 * Anteile nach der vierten Zufallszahl: bis 0,62 Ring, bis 0,82 Knoten, bis
 * 0,91 die erste Bahn, darüber die zweite. Unter 0,34 fliegen Punkte beim
 * Hören und Sprechen.
 */
export const SHARE = Object.freeze({ ring: 0.62, nodes: 0.82, orbitA: 0.91, fliers: 0.34, star: 0.975 })

const TAU = Math.PI * 2
/** Die vier Knoten des Logos auf dem Ring: oben, rechts, unten, links. */
export const LOGO_NODES = [
  [0, 1],
  [1, 0],
  [0, -1],
  [-1, 0],
]

function logoTarget(w, random) {
  if (w < SHARE.ring) {
    const a = random() * TAU
    const r = RING_RADIUS + (random() - 0.5) * 0.035
    return [Math.cos(a) * r, Math.sin(a) * r, (random() - 0.5) * 0.04]
  }
  if (w < SHARE.nodes) {
    const node = LOGO_NODES[Math.floor(random() * 4)]
    const r = Math.cbrt(random()) * 0.075
    const t = random() * TAU
    const p = Math.acos(2 * random() - 1)
    return [
      node[0] * RING_RADIUS + r * Math.sin(p) * Math.cos(t),
      node[1] * RING_RADIUS + r * Math.sin(p) * Math.sin(t),
      r * Math.cos(p),
    ]
  }
  const a = random() * TAU
  const cx = Math.cos(a) * RING_RADIUS
  const cy = Math.sin(a) * RING_RADIUS
  if (w < SHARE.orbitA) return [cx * Math.cos(ORBIT_A), cy, -cx * Math.sin(ORBIT_A)]
  return [cx, cy * Math.cos(ORBIT_B), cy * Math.sin(ORBIT_B)]
}

let cached = null

/**
 * Alle Punkte, `SWARM_SIZE × STRIDE` Werte. Immer dieselben: der Zufall hat
 * eine feste Saat, zwei Aufnahmen desselben Zustands sehen gleich aus.
 *
 * Die Reihenfolge ist gemischt, damit jeder Anfang der Liste den Globus
 * gleichmäßig bedeckt. Ein Gerät, das weniger Punkte zeichnet, nimmt einfach
 * die ersten — und bekommt eine dünnere Kugel statt einer halben.
 */
export function particles() {
  if (cached) return cached
  const count = SWARM_SIZE
  const random = mulberry32(20260923)
  const order = Array.from({ length: count }, (_, i) => i)
  for (let i = count - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1))
    const swap = order[i]
    order[i] = order[j]
    order[j] = swap
  }
  const golden = Math.PI * (3 - Math.sqrt(5))
  const data = new Float32Array(count * STRIDE)
  const land = []
  for (let k = 0; k < count; k += 1) {
    const i = order[k]
    const y = 1 - (2 * (i + 0.5)) / count
    const r = Math.sqrt(1 - y * y)
    const a = i * golden
    const x = Math.cos(a) * r
    const z = Math.sin(a) * r
    const w = random()
    const o = k * STRIDE
    data[o] = x
    data[o + 1] = y
    data[o + 2] = z
    data[o + 3] = random()
    data[o + 4] = random()
    data[o + 5] = random()
    data[o + 6] = w
    const logo = logoTarget(w, random)
    data[o + 7] = logo[0]
    data[o + 8] = logo[1]
    data[o + 9] = logo[2]
    // Die Achsen folgen der Kamera: y nach Norden, +z zum Betrachter auf
    // Länge 0, +x nach Osten.
    data[o + 10] = isLand((Math.asin(y) * 180) / Math.PI, (Math.atan2(x, z) * 180) / Math.PI)
    // Trabanten: ein knappes Drittel von Ring und Knoten verlässt beim
    // Arbeiten die Erde und legt sich als Logo-Ring um sie.
    data[o + 11] = w < SHARE.nodes && random() < 0.36 ? 1 : 0
    data[o + 12] = x
    data[o + 13] = y
    data[o + 14] = z
    if (data[o + 10]) land.push(k)
  }
  // Meerespunkte an Land: jeder wandert zu einem zufälligen Landpunkt und
  // setzt sich knapp neben ihn, um weniger als den halben Punktabstand.
  const spread = 0.5 * Math.sqrt((4 * Math.PI) / count)
  for (let k = 0; k < count; k += 1) {
    const o = k * STRIDE
    if (data[o + 10] || random() >= SEA_TO_LAND || land.length === 0) continue
    const to = land[Math.floor(random() * land.length)] * STRIDE
    let x = data[to] + (random() - 0.5) * spread
    let y = data[to + 1] + (random() - 0.5) * spread
    let z = data[to + 2] + (random() - 0.5) * spread
    const length = Math.sqrt(x * x + y * y + z * z)
    x /= length
    y /= length
    z /= length
    data[o + 10] = 1
    data[o + 12] = x
    data[o + 13] = y
    data[o + 14] = z
  }
  cached = data
  return data
}

// ── Simplex-Rauschen in 3D ───────────────────────────────────────────────
// Nach Stefan Gustavsons gemeinfreier Fassung. Der Shader nimmt die von
// Ashima Arts (MIT); die Werte unterscheiden sich, der Charakter nicht.

const GRAD = new Float32Array([1, 1, 0, -1, 1, 0, 1, -1, 0, -1, -1, 0, 1, 0, 1, -1, 0, 1, 1, 0, -1, -1, 0, -1, 0, 1, 1, 0, -1, 1, 0, 1, -1, 0, -1, -1])
const PERM = (() => {
  const random = mulberry32(1)
  const p = Array.from({ length: 256 }, (_, i) => i)
  for (let i = 255; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1))
    const swap = p[i]
    p[i] = p[j]
    p[j] = swap
  }
  const out = new Uint8Array(512)
  for (let i = 0; i < 512; i += 1) out[i] = p[i & 255]
  return out
})()

function corner(h, x, y, z) {
  let t = 0.6 - x * x - y * y - z * z
  if (t <= 0) return 0
  const g = (h % 12) * 3
  t *= t
  return t * t * (GRAD[g] * x + GRAD[g + 1] * y + GRAD[g + 2] * z)
}

/** Simplex-Rauschen, ungefähr zwischen −1 und 1. */
export function noise3(x, y, z) {
  const s = (x + y + z) / 3
  const i = Math.floor(x + s)
  const j = Math.floor(y + s)
  const k = Math.floor(z + s)
  const t = (i + j + k) / 6
  const x0 = x - i + t
  const y0 = y - j + t
  const z0 = z - k + t
  let i1, j1, k1, i2, j2, k2
  if (x0 >= y0) {
    if (y0 >= z0) [i1, j1, k1, i2, j2, k2] = [1, 0, 0, 1, 1, 0]
    else if (x0 >= z0) [i1, j1, k1, i2, j2, k2] = [1, 0, 0, 1, 0, 1]
    else [i1, j1, k1, i2, j2, k2] = [0, 0, 1, 1, 0, 1]
  } else if (y0 < z0) [i1, j1, k1, i2, j2, k2] = [0, 0, 1, 0, 1, 1]
  else if (x0 < z0) [i1, j1, k1, i2, j2, k2] = [0, 1, 0, 0, 1, 1]
  else [i1, j1, k1, i2, j2, k2] = [0, 1, 0, 1, 1, 0]
  const ii = i & 255
  const jj = j & 255
  const kk = k & 255
  const n =
    corner(PERM[ii + PERM[jj + PERM[kk]]], x0, y0, z0) +
    corner(PERM[ii + i1 + PERM[jj + j1 + PERM[kk + k1]]], x0 - i1 + 1 / 6, y0 - j1 + 1 / 6, z0 - k1 + 1 / 6) +
    corner(PERM[ii + i2 + PERM[jj + j2 + PERM[kk + k2]]], x0 - i2 + 1 / 3, y0 - j2 + 1 / 3, z0 - k2 + 1 / 3) +
    corner(PERM[ii + 1 + PERM[jj + 1 + PERM[kk + 1]]], x0 - 0.5, y0 - 0.5, z0 - 0.5)
  return 32 * n
}

// ── Die Rechnung des Vertex-Shaders ──────────────────────────────────────

const IDENTITY3 = new Float32Array([1, 0, 0, 0, 1, 0, 0, 0, 1])

/**
 * Was der Shader als Uniforms bekommt, einmal je Bild aus der Pose. `gl.js`
 * lädt genau diese Werte hoch; `placeParticle` rechnet mit ihnen.
 */
export function frameUniforms(pose) {
  const g = pose.weights
  return {
    rest: g.ready + g.off,
    listening: g.listening,
    thinking: g.thinking,
    working: g.working,
    speaking: g.speaking,
    fault: g.fault,
    expired: g.expired,
    off: g.off,
    connecting: g.connecting,
    calm: pose.reduced ? 1 : 0,
    time: pose.motionTime,
    level: pose.level,
    history: pose.history,
    view: pose.view,
    earth: pose.earth ? pose.earth.rotation : IDENTITY3,
    earthWeight: pose.earth ? pose.earth.weight : 0,
    target: pose.earth ? pose.earth.target : [0, 0, 1],
    logoTurn: pose.logoTurn,
    vortex: pose.vortex,
    pulse: pose.pulse,
    pulseRadius: pose.pulseRadius,
    brightness: pose.brightness,
  }
}

const clamp = (x, lo, hi) => (x < lo ? lo : x > hi ? hi : x)

/**
 * Um so viel deckt ein Punkt mehr, als er leuchtet. Über Schwarz ändert das
 * nichts; über einem hellen Fenster hinter dem durchsichtigen Overlay bleibt
 * ein Punkt dadurch ein Punkt, statt im Weiß zu verschwinden. Beide Zeichner
 * nehmen denselben Wert.
 */
export const POINT_COVER = 1.6

/**
 * Punktgröße in CSS-Pixeln, am Radius des Globus gemessen: im 150 Pixel
 * hohen Overlay feiner als im Panel, damit die Erde dort nicht zuläuft.
 */
export function pointSizeCss(radiusCss) {
  return clamp(radiusCss * 0.022, 1.2, 3)
}

const smoothstep = (a, b, x) => {
  const t = clamp((x - a) / (b - a), 0, 1)
  return t * t * (3 - 2 * t)
}
const fract = (x) => x - Math.floor(x)

/**
 * Leuchtflecke über den Punkten: die vier Knoten des Logos, solange es
 * steht, und der Ort auf der Erde, über den gesprochen wird. Die Marke aus
 * den Punkten allein ginge im 150 Pixel hohen Overlay unter. Größe als
 * Anteil des Globusradius.
 */
export function highlights(pose) {
  const lights = []
  const working = pose.weights.working
  if (working > 0.01) {
    const turn = pose.logoTurn
    const color = [0, 1, 2].map((i) => pose.color[i] + (pose.ice[i] - pose.color[i]) * 0.5)
    for (const [nx, ny] of LOGO_NODES) {
      const x = nx * RING_RADIUS
      const y = ny * RING_RADIUS
      lights.push({
        position: [turn[0] * x + turn[3] * y, turn[1] * x + turn[4] * y, turn[2] * x + turn[5] * y],
        size: 0.3,
        color,
        strength: 0.75 * working * pose.brightness * (1 + pose.pulse),
        core: 12,
      })
    }
  }
  const earth = pose.earth
  if (earth) {
    const [tx, ty, tz] = earth.target
    const m = earth.rotation
    const x = m[0] * tx + m[3] * ty + m[6] * tz
    const y = m[1] * tx + m[4] * ty + m[7] * tz
    const z = m[2] * tx + m[5] * ty + m[8] * tz
    const visible = earth.weight * (1 - pose.weights.expired) * (1 - pose.weights.connecting)
    if (z > 0.05 && visible > 0.01) {
      const r = earth.radius * 1.01
      lights.push({
        position: [x * r, y * r, z * r],
        size: 0.16,
        color: pose.ice,
        strength: 0.9 * visible * Math.min(1, z * 3),
        core: 18,
      })
    }
  }
  return lights
}

/** Der Pegel von vor `x × 0,8` Sekunden, linear zwischen den Stützstellen. */
export function historyAt(history, x) {
  const last = history.length - 1
  const i = clamp(x, 0, 1) * last
  const a = Math.floor(i)
  const b = Math.min(last, a + 1)
  return history[a] + (history[b] - history[a]) * (i - a)
}

/** Dreht `p` um die Einheitsachse `k` (Rodrigues). */
function around(px, py, pz, k, angle, out) {
  const c = Math.cos(angle)
  const s = Math.sin(angle)
  const dot = k[0] * px + k[1] * py + k[2] * pz
  out[0] = px * c + (k[1] * pz - k[2] * py) * s + k[0] * dot * (1 - c)
  out[1] = py * c + (k[2] * px - k[0] * pz) * s + k[1] * dot * (1 - c)
  out[2] = pz * c + (k[0] * py - k[1] * px) * s + k[2] * dot * (1 - c)
  return out
}

const Z_AXIS = [0, 0, 1]
const scratch = [0, 0, 0]

/** Wo der Punkt im Logo steht; Ring und Bahnen fließen, die Knoten ruhen. */
export function logoPoint(data, o, time, out) {
  const w = data[o + 6]
  const lx = data[o + 7]
  const ly = data[o + 8]
  const lz = data[o + 9]
  if (w < SHARE.ring) return around(lx, ly, lz, Z_AXIS, time * (0.54 + data[o + 3] * 0.72), out)
  if (w < SHARE.nodes) {
    out[0] = lx
    out[1] = ly
    out[2] = lz
    return out
  }
  if (w < SHARE.orbitA) return around(lx, ly, lz, ORBIT_A_AXIS, time * 0.5, out)
  return around(lx, ly, lz, ORBIT_B_AXIS, -time * 0.45, out)
}

/**
 * Stellt Punkt `k` auf und schreibt nach `out`: x, y, z, Helligkeit, Anteil
 * Eis in der Farbe, Größenfaktor (ohne Perspektive) und die Stärke der
 * Zielmarke auf der Erde.
 */
export function placeParticle(data, k, u, out) {
  const o = k * STRIDE
  const dx = data[o]
  const dy = data[o + 1]
  const dz = data[o + 2]
  const rx = data[o + 3]
  const ry = data[o + 4]
  const rz = data[o + 5]
  const rw = data[o + 6]
  const land = data[o + 10]
  const satellite = data[o + 11]
  const ax = data[o + 12]
  const ay = data[o + 13]
  const az = data[o + 14]
  const e = u.earthWeight
  const t = u.time
  const moving = 1 - u.calm
  const v = u.view
  const m = u.earth

  // Der Punkt auf dem Sternenglobus (g) und auf der Erde (q). Auf der Erde
  // steht er an seinem Erdplatz — für Meerespunkte, die an Land wandern,
  // ist das eine andere Richtung als am Himmel.
  const gx = v[0] * dx + v[3] * dy + v[6] * dz
  const gy = v[1] * dx + v[4] * dy + v[7] * dz
  const gz = v[2] * dx + v[5] * dy + v[8] * dz
  const qx = m[0] * ax + m[3] * ay + m[6] * az
  const qy = m[1] * ax + m[4] * ay + m[7] * az
  const qz = m[2] * ax + m[5] * ay + m[8] * az
  const nx = gx + (qx - gx) * e
  const ny = gy + (qy - gy) * e
  const nz0 = gz + (qz - gz) * e
  const nz = nz0 / (Math.sqrt(nx * nx + ny * ny + nz0 * nz0) || 1)

  const flow = noise3(dx * 2.2, dy * 2.2, dz * 2.2 + t * 0.25)
  const angle = Math.acos(clamp(nz, -1, 1)) / Math.PI
  const front = smoothstep(-0.2, 1, nz)
  const waveIn = u.calm ? u.level : historyAt(u.history, 1 - angle)
  const waveOut = u.calm ? u.level : historyAt(u.history, angle)
  const life = fract(t * 0.85 + rx * 7)
  const flier = rw < SHARE.fliers ? moving : 0
  const flyIn = flier * (1 - life) * u.level
  const flyOut = flier * life * historyAt(u.history, life * 0.9)

  let px = 0
  let py = 0
  let pz = 0
  let total = 0
  // Globus-Form (a) und Erd-Form (b) eines Zustands, nach Erdgewicht gemischt.
  const add = (w, ax, ay, az, bx, by, bz) => {
    px += w * (ax + (bx - ax) * e)
    py += w * (ay + (by - ay) * e)
    pz += w * (az + (bz - az) * e)
    total += w
  }

  if (u.rest > 0.001) {
    const a = 1 + 0.02 * flow
    const b = 1 + 0.008 * flow
    add(u.rest, gx * a, gy * a, gz * a, qx * b, qy * b, qz * b)
  }
  if (u.listening > 0.001) {
    const a = 1 + moving * (0.05 + 0.3 * front) * waveIn + 0.02 * flow + flyIn * 1.1
    const b = 1 + moving * (0.03 + 0.12 * front) * waveIn + flyIn * 0.5
    add(u.listening, gx * a, gy * a, gz * a, qx * b, qy * b, qz * b)
  }
  if (u.speaking > 0.001) {
    const a = 1 + moving * 0.2 * waveOut + 0.02 * flow + flyOut * 1.2
    const b = 1 + moving * 0.08 * waveOut + flyOut * 0.55
    add(u.speaking, gx * a, gy * a, gz * a, qx * b, qy * b, qz * b)
  }
  if (u.thinking > 0.001) {
    const turn = u.vortex[0] + u.vortex[1] * Math.abs(dy) + u.vortex[2] * flow
    const c = Math.cos(turn)
    const s = Math.sin(turn)
    // Erst um die eigene Achse wirbeln, dann wie der Globus drehen.
    const wx = c * dx + s * dz
    const wz = -s * dx + c * dz
    const swell = 1 + moving * 0.07 * noise3(dx * 3 + t * 0.8, dy * 3 + t * 0.8, dz * 3 + t * 0.8)
    add(
      u.thinking,
      (v[0] * wx + v[3] * dy + v[6] * wz) * swell,
      (v[1] * wx + v[4] * dy + v[7] * wz) * swell,
      (v[2] * wx + v[5] * dy + v[8] * wz) * swell,
      qx,
      qy,
      qz,
    )
  }
  if (u.working > 0.001) {
    const l = logoPoint(data, o, t, scratch)
    const r = u.logoTurn
    const lx = r[0] * l[0] + r[3] * l[1] + r[6] * l[2]
    const ly = r[1] * l[0] + r[4] * l[1] + r[7] * l[2]
    const lz = r[2] * l[0] + r[5] * l[1] + r[8] * l[2]
    if (satellite > 0.5) add(u.working, lx, ly, lz, lx, ly, lz)
    else add(u.working, lx, ly, lz, qx * 0.8, qy * 0.8, qz * 0.8)
  }
  if (u.fault > 0.001) {
    const fray = noise3(dx * 1.7 + t * 1.6, dy * 1.7 + t * 1.6, dz * 1.7 + t * 1.6)
    const jx = noise3(dx + t, dy + t, dz + t)
    const jy = noise3(dy + t, dz + t, dx + t)
    const a = 1 + 0.45 * fray
    const b = 1 + 0.2 * fray
    add(u.fault, gx * a + 0.12 * jx, gy * a + 0.12 * jy, gz * a, qx * b + 0.06 * jx, qy * b + 0.06 * jy, qz * b)
  }
  if (u.expired > 0.001) {
    const x = dx * 1.15
    const y = -0.62 + dy * 0.05
    const z = dz * 1.15
    add(u.expired, x, y, z, x, y, z)
  }
  if (u.connecting > 0.001) {
    // Eine weite Hülle, so gleichmäßig wie der Globus: jeder Punkt in seiner
    // eigenen Richtung, nur weiter draußen. Steht die Leitung, zieht sie sich
    // zum Globus zusammen. Zufällige Würfelpunkte als Richtung ballten sich an
    // den acht Ecken und machten aus der Wolke Klumpen.
    const reach = (1.25 + 0.55 * rx) * (1 - 0.08 * moving * Math.sin(t * 1.7 + ry * TAU))
    add(u.connecting, gx * reach, gy * reach, gz * reach, gx * reach, gy * reach, gz * reach)
  }
  if (total > 1e-4) {
    px /= total
    py /= total
    pz /= total
  }

  // ── Helligkeit ──
  // Trabanten im Ring und Punkte in Scheibe oder Wolke zählen nicht zur Erde.
  const onEarth = e * (1 - u.working * satellite) * (1 - u.expired) * (1 - u.connecting)
  // Sterne gibt es auf der Erde nur an Land — dort sehen sie aus wie Städte.
  const star = rz >= SHARE.star ? 1 - onEarth * (1 - land) : 0
  const depth = smoothstep(-1.2, 1.1, pz)
  let bright = (0.22 + 0.78 * depth * depth) * (0.5 + 0.5 * ry) * (1 + star * 1.4) * 0.8
  // Auf der Erde leuchtet Land, das Meer bleibt ein Hauch. Die Rückseite
  // verschwindet: der Sternenglobus ist durchsichtig, die Erde ein Körper —
  // sonst schienen die Kontinente der anderen Seite spiegelverkehrt durch.
  const facing = smoothstep(-0.25, 0.35, qz)
  bright *= 1 + ((land > 0.5 ? 1.35 : 0.14) * (0.04 + 0.96 * facing) - 1) * onEarth
  const tx = u.target
  const dist = Math.acos(clamp(ax * tx[0] + ay * tx[1] + az * tx[2], -1, 1))
  const ping = fract(t * 0.6)
  const q = (dist - ping * 0.35) * 40
  const target = onEarth * (1 - smoothstep(0, 0.03, dist) + moving * 0.8 * Math.exp(-q * q) * (1 - ping))
  // Der Wellenkamm leuchtet: so sieht man die Welle auch im Standbild.
  bright *= 1 + 2.2 * (waveIn * u.listening + waveOut * u.speaking)
  // Fliegende Punkte verblassen auf ihrem Weg — aber nur, wenn sie fliegen.
  bright *=
    1 -
    flier *
      (u.listening * (1 - life) * 0.8 * Math.min(1, u.level * 3) +
        u.speaking * life * 0.8 * Math.min(1, historyAt(u.history, life * 0.9) * 3))
  // Denken mit Erde: ein Band tastet sie von oben nach unten ab.
  const band = (qy - Math.sin(t * 1.3) * 0.85) * 8
  bright *= 1 + 1.6 * u.thinking * onEarth * Math.exp(-band * band) * moving
  let shell = 0
  if (u.pulse > 0.001) {
    const sr = (Math.sqrt(px * px + py * py) - u.pulseRadius) * 5
    shell = u.pulseRadius < 0 ? u.pulse * 0.6 : u.pulse * Math.exp(-sr * sr)
  }
  bright *= 1 + 2.2 * shell
  // Die äußeren Punkte der Hülle am schwächsten: sie verlaufen, statt am
  // Rand der Bühne abzubrechen.
  bright *= 1 - u.connecting * (0.25 + 0.45 * rx)
  bright *= (1 - 0.85 * u.off - 0.55 * u.expired) * u.brightness

  out[0] = px
  out[1] = py
  out[2] = pz
  out[3] = bright
  out[4] = clamp(0.25 * rx + star * 0.5 + target + shell * 0.6, 0, 1)
  // Land etwas gröber, Meer feiner: die Kontinente lesen sich als Fläche.
  out[5] = (0.7 + 0.7 * ry) * (1 + star * 1.3 + target * 1.5) * (1 + onEarth * (land > 0.5 ? 0.2 : -0.3))
  out[6] = target
  return out
}
