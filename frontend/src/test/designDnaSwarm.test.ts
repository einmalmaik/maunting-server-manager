import { afterEach, describe, expect, it, vi } from 'vitest'

import { createSwarm } from '@maunting/design-dna/swarm'
import { isLand } from '../../packages/design-dna/swarm/landmask.js'
import { Choreography, HISTORY_LENGTH, STATES } from '../../packages/design-dna/swarm/motion.js'
import {
  POINT_COVER,
  RING_RADIUS,
  SHARE,
  STRIDE,
  SWARM_SIZE,
  frameUniforms,
  highlights,
  particles,
  placeParticle,
} from '../../packages/design-dna/swarm/particles.js'

/**
 * Der Schwarm der Design-DNA ohne Grafikkarte: die Rechnung, die der
 * Vertex-Shader in `gl.js` Zeile für Zeile ebenso macht (`placeParticle`),
 * die Choreografie, aus der beide Zeichner ihre Pose bekommen, und die
 * Steuerung mit ihren Rückfällen. Was hier steht, sind die Zusagen des
 * Schwarms — dass er ein Globus, ein Logo und eine Erde ist, dass er dem
 * echten Pegel folgt statt einer Schleife und dass er ohne WebGL weiterlebt.
 */

const STEP = 1 / 60
const BERLIN = { latitude: 52.52, longitude: 13.405 }
const TOKIO = { latitude: 35.68, longitude: 139.69 }

type Eingabe = { state: string; level?: number; earth?: { latitude: number; longitude: number } | null; pulse?: boolean }

function laufen(choreografie: Choreography, eingabe: Eingabe, sekunden: number) {
  let pose = null
  for (let i = 0; i < Math.round(sekunden / STEP); i += 1) {
    pose = choreografie.step({ level: 0, earth: null, ...eingabe }, STEP)
  }
  return pose
}

function pose(state: string, extra: Omit<Eingabe, 'state'> = {}, reducedMotion = false) {
  return laufen(new Choreography({ reducedMotion }), { state, ...extra }, 4)
}

/** Stellt jeden `schritt`-ten Punkt auf und gibt Lage und Kennwerte zurück. */
function aufstellen(p: ReturnType<typeof pose>, schritt = 1) {
  const daten = particles()
  const u = frameUniforms(p)
  const aus = new Float32Array(7)
  const punkte = []
  for (let k = 0; k < SWARM_SIZE; k += schritt) {
    placeParticle(daten, k, u, aus)
    const o = k * STRIDE
    punkte.push({
      k,
      x: aus[0],
      y: aus[1],
      z: aus[2],
      hell: aus[3],
      r: Math.hypot(aus[0], aus[1], aus[2]),
      w: daten[o + 6],
      land: daten[o + 10] > 0.5,
      trabant: daten[o + 11] > 0.5,
    })
  }
  return punkte
}

function mittel(werte: number[]) {
  return werte.reduce((summe, wert) => summe + wert, 0) / werte.length
}

describe('Die Punkte', () => {
  it('bedecken den Globus mit jedem Anfang der Liste gleichmaessig', () => {
    // Ein Gerät, das weniger Punkte zeichnet, nimmt die ersten: Canvas 2D
    // 2400, ein langsames WebGL bis hinab auf 30 %. Es muss eine dünnere
    // Kugel werden, keine halbe.
    const daten = particles()
    for (const anzahl of [2400, Math.round(SWARM_SIZE * 0.3), SWARM_SIZE]) {
      const summe = [0, 0, 0]
      const oktanten = new Array(8).fill(0)
      for (let k = 0; k < anzahl; k += 1) {
        const [x, y, z] = [daten[k * STRIDE], daten[k * STRIDE + 1], daten[k * STRIDE + 2]]
        expect(Math.hypot(x, y, z)).toBeCloseTo(1, 5)
        summe[0] += x
        summe[1] += y
        summe[2] += z
        oktanten[(x > 0 ? 1 : 0) + (y > 0 ? 2 : 0) + (z > 0 ? 4 : 0)] += 1
      }
      expect(Math.hypot(...summe) / anzahl, `${anzahl} Punkte`).toBeLessThan(0.03)
      for (const zahl of oktanten) expect(zahl / anzahl).toBeCloseTo(0.125, 1)
    }
  })

  it('teilen sich auf Ring, Knoten und Bahnen wie das Logo', () => {
    const daten = particles()
    const anteil = (von: number, bis: number) => {
      let n = 0
      for (let k = 0; k < SWARM_SIZE; k += 1) {
        const w = daten[k * STRIDE + 6]
        if (w >= von && w < bis) n += 1
      }
      return n / SWARM_SIZE
    }
    expect(anteil(0, SHARE.ring)).toBeCloseTo(0.62, 1)
    expect(anteil(SHARE.ring, SHARE.nodes)).toBeCloseTo(0.2, 1)
    expect(anteil(SHARE.nodes, SHARE.orbitA)).toBeCloseTo(0.09, 1)
    expect(anteil(SHARE.orbitA, 1)).toBeCloseTo(0.09, 1)
  })

  it('zeichnen auf der Erde vor allem Land — sonst zerfallen die Kuesten', () => {
    const daten = particles()
    let land = 0
    let angekommen = 0
    let gewandert = 0
    for (let k = 0; k < SWARM_SIZE; k += 1) {
      const o = k * STRIDE
      if (!daten[o + 10]) continue
      land += 1
      const [x, y, z] = [daten[o + 12], daten[o + 13], daten[o + 14]]
      expect(Math.hypot(x, y, z)).toBeCloseTo(1, 4)
      if (x === daten[o] && y === daten[o + 1] && z === daten[o + 2]) continue
      // Gewandert: er sitzt jetzt neben einem Landpunkt.
      gewandert += 1
      if (isLand((Math.asin(y) * 180) / Math.PI, (Math.atan2(x, z) * 180) / Math.PI)) angekommen += 1
    }
    expect(land / SWARM_SIZE).toBeGreaterThan(0.78)
    expect(gewandert).toBeGreaterThan(SWARM_SIZE / 2)
    // Knapp neben einem Landpunkt kann an der Küste schon Meer sein, aber
    // nicht bei vielen.
    expect(angekommen / gewandert).toBeGreaterThan(0.85)
  })
})

describe('Die Landmaske', () => {
  it.each([
    ['Berlin', 52.52, 13.4],
    ['Moskau', 55.75, 37.62],
    ['Nairobi', -1.29, 36.82],
    ['Sao Paulo', -23.55, -46.63],
    ['Tokio', 35.68, 139.69],
    ['Groenland', 72, -40],
    ['Antarktis', -80, 0],
  ])('kennt %s als Land', (_ort, breite, laenge) => {
    expect(isLand(breite, laenge)).toBeTruthy()
  })

  it.each([
    ['Atlantik', 0, -30],
    ['Nordatlantik', 45, -35],
    ['Pazifik', 0, -150],
    ['Indischer Ozean', -20, 80],
    ['Mittelmeer', 35, 18],
    ['Suedpolarmeer', -60, 0],
  ])('kennt %s als Meer', (_ort, breite, laenge) => {
    expect(isLand(breite, laenge)).toBeFalsy()
  })

  it('bedeckt so viel der Kugel, wie die Erde Land hat', () => {
    let land = 0
    const n = 20_000
    for (let i = 0; i < n; i += 1) {
      const y = 1 - (2 * (i + 0.5)) / n
      const a = i * Math.PI * (3 - Math.sqrt(5))
      const r = Math.sqrt(1 - y * y)
      if (isLand((Math.asin(y) * 180) / Math.PI, (Math.atan2(Math.cos(a) * r, Math.sin(a) * r) * 180) / Math.PI)) land += 1
    }
    // Rund 29 % der Erdoberfläche sind Land.
    expect(land / n).toBeGreaterThan(0.25)
    expect(land / n).toBeLessThan(0.33)
  })
})

describe('Die Bewegung', () => {
  it('folgt dem echten Pegel statt einer Schleife', () => {
    const c = new Choreography()
    let p = laufen(c, { state: 'listening', level: 0 }, 1)
    // Stille bleibt still: keine Welle, die von selbst läuft.
    expect(p.level).toBeLessThan(0.01)
    expect(Math.max(...p.history)).toBeLessThan(0.01)

    // Eine Silbe, 150 ms laut.
    p = laufen(c, { state: 'listening', level: 0.9 }, 0.15)
    expect(p.level).toBeGreaterThan(0.8)
    expect(p.history[0]).toBeGreaterThan(0.8)
    // Am Rand ist sie noch nicht angekommen.
    expect(p.history[HISTORY_LENGTH - 1]).toBeLessThan(0.01)

    // 400 ms später: der Pegel ist abgeklungen, die Silbe wandert als Welle
    // durch den Verlauf.
    p = laufen(c, { state: 'listening', level: 0 }, 0.4)
    expect(p.level).toBeLessThan(0.15)
    const spitze = p.history.indexOf(Math.max(...p.history))
    expect(spitze).toBeGreaterThan(10)
    expect(p.history[spitze]).toBeGreaterThan(0.8)
  })

  it('blendet Zustaende weich ineinander, und die Gewichte ergeben immer eins', () => {
    const c = new Choreography()
    laufen(c, { state: 'ready' }, 2)
    let p = null
    for (let i = 0; i < 6; i += 1) {
      p = c.step({ state: 'working', level: 0, earth: null }, STEP)
      expect(STATES.reduce((summe: number, s: string) => summe + p.weights[s], 0)).toBeCloseTo(1, 6)
    }
    // Nach 100 ms ist das Logo zu gut einem Viertel da — kein Sprung.
    expect(p.weights.working).toBeGreaterThan(0.2)
    expect(p.weights.working).toBeLessThan(0.35)
    p = laufen(c, { state: 'working' }, 2.5)
    expect(p.weights.working).toBeGreaterThan(0.99)
  })

  it('steht bei reduzierter Bewegung still und blitzt nur einmal auf', () => {
    const c = new Choreography({ reducedMotion: true })
    const vorher = laufen(c, { state: 'thinking' }, 1)
    const zeitVorher = vorher.motionTime
    const nachher = laufen(c, { state: 'thinking', pulse: true }, STEP)

    expect(zeitVorher).toBe(0)
    expect(nachher.motionTime).toBe(0)
    expect(nachher.vortex).toEqual([0, 0, 0])
    // Kein Ring, der nach außen läuft: ein kurzes, gleichmäßiges Aufhellen.
    expect(nachher.pulse).toBeGreaterThan(0.9)
    expect(nachher.pulseRadius).toBe(-1)

    // Und die Punkte stehen: zwei Aufnahmen, eine Sekunde auseinander, gleich.
    const ruhig = new Choreography({ reducedMotion: true })
    const a = aufstellen(laufen(ruhig, { state: 'ready', level: 0.8 }, 1), 97)
    const b = aufstellen(laufen(ruhig, { state: 'ready', level: 0.8 }, 1), 97)
    for (let i = 0; i < a.length; i += 1) {
      expect(b[i].x).toBeCloseTo(a[i].x, 6)
      expect(b[i].y).toBeCloseTo(a[i].y, 6)
    }
  })

  it('schlaeft aus, wenn nichts mehr zu zeigen ist', () => {
    const c = new Choreography()
    const p = laufen(c, { state: 'off' }, 5)
    const zeit = p.motionTime
    laufen(c, { state: 'off' }, 1)

    // Die Steuerung hört dann auf zu zeichnen — kein Akku für ein Standbild.
    expect(c.resting()).toBe(true)
    expect(c.step({ state: 'off', level: 0, earth: null }, STEP).motionTime - zeit).toBeLessThan(1e-3)
    // Solange eine Erde erscheint, ruht er nicht …
    laufen(c, { state: 'off', earth: BERLIN }, 1)
    expect(c.resting()).toBe(false)
    // … steht sie, ist auch sie ein Standbild. Nach dem Ende einer Sitzung
    // über einer Region zeichnete er sonst weiter, solange die Ansicht offen war.
    laufen(c, { state: 'off', earth: BERLIN }, 4)
    expect(c.resting()).toBe(true)
    // Ein neuer Ort dreht sie hinüber, und solange sie sich dreht, wird gezeichnet.
    c.step({ state: 'off', level: 0, earth: TOKIO }, STEP)
    expect(c.resting()).toBe(false)
    laufen(c, { state: 'off', earth: TOKIO }, 8)
    expect(c.resting()).toBe(true)
  })

  it('ruht bei reduzierter Bewegung, sobald sich nichts mehr ändert — auch mitten im Gespräch', () => {
    const c = new Choreography({ reducedMotion: true })
    laufen(c, { state: 'listening', earth: BERLIN }, 6)
    expect(c.resting()).toBe(true)
    // Spricht jemand, lebt er auf.
    c.step({ state: 'listening', level: 0.6, earth: BERLIN }, STEP)
    expect(c.resting()).toBe(false)

    // Ohne reduzierte Bewegung dreht sich der Globus weiter und ruht nie.
    const bewegt = new Choreography()
    laufen(bewegt, { state: 'listening' }, 6)
    expect(bewegt.resting()).toBe(false)
  })

  it('schickt beim Werkzeugstart einen Lichtring nach aussen, der wieder vergeht', () => {
    const c = new Choreography()
    laufen(c, { state: 'working' }, 1)
    const start = c.step({ state: 'working', level: 0, earth: null, pulse: true }, STEP)
    expect(start.pulse).toBeGreaterThan(0.95)
    const halb = laufen(c, { state: 'working' }, 0.5)
    expect(halb.pulseRadius).toBeGreaterThan(start.pulseRadius)
    expect(laufen(c, { state: 'working' }, 0.7).pulse).toBe(0)
  })

  it('dreht die Erde zum Ort und schwenkt bei einem neuen Ort hinueber, statt zu springen', () => {
    const vorn = (p: NonNullable<ReturnType<typeof pose>>) => {
      const [tx, ty, tz] = p.earth.target
      const m = p.earth.rotation
      return m[2] * tx + m[5] * ty + m[8] * tz
    }
    const c = new Choreography()
    const berlin = laufen(c, { state: 'speaking', earth: BERLIN }, 3)
    expect(berlin.earth.weight).toBe(1)
    expect(vorn(berlin)).toBeGreaterThan(0.95)

    const sofort = c.step({ state: 'speaking', level: 0, earth: TOKIO }, STEP)
    expect(vorn(sofort)).toBeLessThan(0.5)
    expect(vorn(laufen(c, { state: 'speaking', earth: TOKIO }, 4))).toBeGreaterThan(0.95)
  })
})

describe('Die Formen', () => {
  it('rechnen in keinem Zustand ein NaN, mit und ohne Erde', () => {
    for (const state of STATES) {
      for (const earth of [null, BERLIN]) {
        for (const p of aufstellen(pose(state, { level: 0.7, earth }), 13)) {
          expect(Number.isFinite(p.x + p.y + p.z + p.hell), `${state} ${earth ? 'Erde' : ''}`).toBe(true)
        }
      }
    }
  })

  it('in Ruhe: ein Globus', () => {
    for (const p of aufstellen(pose('ready'), 7)) {
      expect(Math.abs(p.r - 1)).toBeLessThan(0.03)
    }
  })

  it('beim Arbeiten: das Logo aus Ring, Knoten und Bahnen', () => {
    for (const p of aufstellen(pose('working'), 7)) {
      expect(Math.abs(p.r - RING_RADIUS)).toBeLessThan(0.08)
    }
  })

  it('beim Verbinden: eine weite, gleichmaessige Huelle', () => {
    const punkte = aufstellen(pose('connecting'), 3)
    for (const p of punkte) {
      expect(p.r).toBeGreaterThan(1.1)
      expect(p.r).toBeLessThan(1.95)
    }
    // Keine Klumpen: jede Seite gleich dicht.
    const oktanten = new Array(8).fill(0)
    for (const p of punkte) oktanten[(p.x > 0 ? 1 : 0) + (p.y > 0 ? 2 : 0) + (p.z > 0 ? 4 : 0)] += 1
    for (const zahl of oktanten) expect(zahl / punkte.length).toBeCloseTo(0.125, 1)
  })

  it('am Ende der Sitzung: eine flache Scheibe', () => {
    for (const p of aufstellen(pose('expired'), 7)) {
      expect(Math.abs(p.y + 0.62)).toBeLessThan(0.06)
    }
  })

  it('als Erde: Land leuchtet, das Meer bleibt ein Hauch, die Rueckseite verschwindet', () => {
    const punkte = aufstellen(pose('ready', { earth: BERLIN }))
    const vorn = punkte.filter((p) => p.z > 0.5)
    const land = mittel(vorn.filter((p) => p.land).map((p) => p.hell))
    const meer = mittel(vorn.filter((p) => !p.land).map((p) => p.hell))
    expect(land / meer).toBeGreaterThan(5)
    // Sonst schienen die Kontinente der anderen Seite spiegelverkehrt durch.
    const hinten = mittel(punkte.filter((p) => p.land && p.z < -0.5).map((p) => p.hell))
    expect(hinten / land).toBeLessThan(0.1)
  })

  it('als Erde beim Arbeiten: Trabanten bilden den Ring um die kleinere Erde', () => {
    const punkte = aufstellen(pose('working', { earth: BERLIN }), 3)
    for (const p of punkte) {
      if (p.trabant) expect(Math.abs(p.r - RING_RADIUS)).toBeLessThan(0.08)
      else expect(Math.abs(p.r - 0.8)).toBeLessThan(0.02)
    }
  })

  it('beim Hoeren: die Welle kommt aus dem Pegel', () => {
    const still = aufstellen(pose('listening', { level: 0 }), 5)
    const laut = aufstellen(pose('listening', { level: 0.9 }), 5)
    const vorn = (punkte: typeof still) => mittel(punkte.filter((p) => p.z > 0.3).map((p) => p.r))
    expect(vorn(laut) - vorn(still)).toBeGreaterThan(0.05)
  })

  it('setzt Leuchtflecken auf die Knoten des Logos und auf den Ort', () => {
    expect(highlights(pose('ready'))).toHaveLength(0)
    expect(highlights(pose('working'))).toHaveLength(4)
    const ort = highlights(pose('ready', { earth: BERLIN }))
    expect(ort).toHaveLength(1)
    expect(ort[0].position[2]).toBeGreaterThan(0.9)
  })
})

describe('createSwarm', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function buehne() {
    const element = document.createElement('div')
    vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({
      width: 400, height: 200, top: 0, left: 0, right: 400, bottom: 200, x: 0, y: 0, toJSON: () => ({}),
    } as DOMRect)
    document.body.appendChild(element)
    return element
  }

  /** Ein 2D-Kontext, der mitschreibt — genug für `canvas.js`, sonst nichts. */
  function zeichenflaeche() {
    const aufrufe = { rect: 0, fill: 0, drawImage: 0 }
    const fuellungen: { modus: string; deckung: number; farbe: string }[] = []
    const kontext = {
      setTransform: () => undefined,
      clearRect: () => undefined,
      beginPath: () => undefined,
      rect: () => { aufrufe.rect += 1 },
      fill: () => {
        aufrufe.fill += 1
        fuellungen.push({ modus: kontext.globalCompositeOperation, deckung: kontext.globalAlpha, farbe: kontext.fillStyle })
      },
      fillRect: () => undefined,
      drawImage: () => { aufrufe.drawImage += 1 },
      createRadialGradient: () => ({ addColorStop: () => undefined }),
      globalAlpha: 1,
      globalCompositeOperation: 'source-over',
      fillStyle: '',
    }
    return { aufrufe, fuellungen, kontext }
  }

  it('bleibt ohne WebGL und ohne Canvas 2D leer, ohne zu werfen', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
    const element = buehne()

    const schwarm = createSwarm(element, { autoplay: false, state: 'listening', level: () => 0.5 })
    expect(schwarm.renderer).toBe('none')
    expect(element.dataset.swarmRenderer).toBe('none')
    expect(() => {
      schwarm.update({ state: 'working', earth: BERLIN })
      schwarm.pulse()
      schwarm.advance(0.5)
    }).not.toThrow()

    schwarm.destroy()
    expect(element.querySelector('canvas')).toBeNull()
    expect(element.dataset.swarmRenderer).toBeUndefined()
  })

  it('zeichnet ohne WebGL denselben Schwarm mit Canvas 2D', () => {
    const { aufrufe, kontext } = zeichenflaeche()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
      ((art: string) => (art === '2d' ? kontext : null)) as typeof HTMLCanvasElement.prototype.getContext,
    )
    const element = buehne()

    const schwarm = createSwarm(element, { autoplay: false, state: 'ready' })
    expect(schwarm.renderer).toBe('canvas')
    // Lesbar auch dort, wo es kein Protokoll gibt: in der Desktop-App.
    expect(element.dataset.swarmRenderer).toBe('canvas')
    schwarm.advance(1)

    // 2400 Punkte in wenigen Pfaden, Sterne und Licht als Bilder.
    expect(aufrufe.rect).toBeGreaterThan(1500)
    expect(aufrufe.fill).toBeGreaterThan(0)
    expect(aufrufe.fill).toBeLessThanOrEqual(16)
    expect(aufrufe.drawImage).toBeGreaterThan(0)
    expect(schwarm.stats()).toMatchObject({ renderer: 'canvas', width: 400, height: 200 })
    schwarm.destroy()
  })

  it('deckt ohne WebGL wie der Shader — sichtbar auch ueber einem hellen Fenster', () => {
    const { fuellungen, kontext } = zeichenflaeche()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
      ((art: string) => (art === '2d' ? kontext : null)) as typeof HTMLCanvasElement.prototype.getContext,
    )
    const schwarm = createSwarm(buehne(), { autoplay: false, state: 'listening', level: () => 0.6 })
    schwarm.advance(1)

    expect(fuellungen.length).toBeGreaterThan(0)
    for (const { modus, deckung, farbe } of fuellungen) {
      // Vormultipliziert gemischt, nicht addiert: „lighter" ließe die Punkte
      // über Weiß verschwinden, weil sie nur Licht hinzufügen.
      expect(modus).toBe('source-over')
      const kanal = (farbe.match(/\d+/g) ?? []).map((wert) => Number(wert) / 255)
      const licht = Math.max(...kanal) * deckung
      // Die Punkte decken so viel mehr, als sie leuchten, wie im Shader.
      expect(deckung).toBeCloseTo(Math.min(1, licht * POINT_COVER), 1)
    }
    schwarm.destroy()
  })

  it('schlaeft bei reduzierter Bewegung und wacht auf, sobald jemand spricht', () => {
    vi.useFakeTimers()
    const bilder: FrameRequestCallback[] = []
    vi.stubGlobal('requestAnimationFrame', (rueckruf: FrameRequestCallback) => bilder.push(rueckruf))
    vi.stubGlobal('cancelAnimationFrame', () => undefined)
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
    /** Spielt Bilder ab, bis die Schleife keins mehr anfordert. */
    const abspielen = () => {
      let gezeigt = 0
      for (let zeit = 16; bilder.length > 0 && gezeigt < 5000; zeit += 16) {
        bilder.shift()?.(zeit)
        gezeigt += 1
      }
      return gezeigt
    }
    let pegel = 0
    try {
      const schwarm = createSwarm(buehne(), { state: 'listening', level: () => pegel, reducedMotion: true })
      expect(abspielen()).toBeLessThan(5000)
      expect(bilder).toHaveLength(0)

      // Stille: Er sieht nur nach, und es bleibt still.
      vi.advanceTimersByTime(500)
      expect(bilder).toHaveLength(0)

      // Jemand spricht: beim nächsten Blick wacht er auf, und danach schläft
      // er wieder ein.
      pegel = 0.5
      vi.advanceTimersByTime(100)
      expect(bilder).toHaveLength(1)
      pegel = 0
      expect(abspielen()).toBeLessThan(5000)

      schwarm.destroy()
      pegel = 0.5
      vi.advanceTimersByTime(500)
      expect(bilder).toHaveLength(0)
    } finally {
      vi.useRealTimers()
      vi.unstubAllGlobals()
    }
  })

  it('folgt reduzierter Bewegung aus dem System, auch wenn sie sich spaeter aendert', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
    let melden: ((ereignis: { matches: boolean }) => void) | null = null
    vi.stubGlobal('matchMedia', () => ({
      matches: false,
      addEventListener: (_art: string, rueckruf: typeof melden) => { melden = rueckruf },
      removeEventListener: () => undefined,
    }))
    const schwarm = createSwarm(buehne(), { autoplay: false })
    expect(schwarm.stats().reducedMotion).toBe(false)

    melden?.({ matches: true })

    expect(schwarm.stats().reducedMotion).toBe(true)
    schwarm.destroy()
    vi.unstubAllGlobals()
  })

  it('uebersteht eine Pegelquelle, die wirft oder Unsinn liefert', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
    const schwarm = createSwarm(buehne(), {
      autoplay: false,
      state: 'speaking',
      level: () => {
        throw new Error('Messpunkt weg')
      },
    })
    expect(() => schwarm.advance(0.2)).not.toThrow()
    schwarm.update({ level: () => Number.NaN })
    expect(() => schwarm.advance(0.2)).not.toThrow()
    schwarm.destroy()
  })
})
