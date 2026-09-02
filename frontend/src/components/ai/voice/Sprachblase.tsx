import { useEffect, useRef } from 'react'

import type { Sprachzustand } from './useSprachsitzung'

/**
 * Die Kugel — eine dunkle Sphäre mit leuchtendem Rand, dahinter fliessende
 * Bänder.
 *
 * **Warum ein Canvas und keine CSS-Animation.** Eine CSS-Animation spielt einen
 * Ablauf ab, den jemand vorher aufgeschrieben hat. Diese Form entsteht aber aus
 * etwas, das erst zur Laufzeit da ist: der Lautstärke. Wenn sich die Kugel zum
 * gesprochenen Wort bewegen soll und nicht bloss ungefähr dazu, muss sie jedes
 * Bild neu gerechnet werden.
 *
 * **Warum keine 3D-Bibliothek.** Das hier ist ein Kreis mit vier Verläufen und
 * ein paar Sinuslinien. `three.js` kostet rund 600 KB, einen WebGL-Kontext und
 * eine Abhängigkeit, die Sicherheitsmeldungen bekommt — für eine Kugel, die
 * sich nicht dreht und die man nicht umkreist. Canvas 2D reicht, und der ganze
 * Effekt steht in einer Datei, die man lesen kann.
 *
 * Die Bänder laufen **hinter** der Kugel durch und sind an ihren Flanken am
 * kräftigsten. Das ist der Trick, der Tiefe macht: nicht die Kugel bewegt sich,
 * sondern das, worin sie liegt.
 *
 * Bewegung respektiert `prefers-reduced-motion`. Dann stehen die Bänder still
 * und nur der Pegel wirkt noch — wer Bewegung schlecht verträgt, soll trotzdem
 * sehen, ob die KI gerade redet.
 */

/** Die Bänder hinter der Kugel: Frequenz, Phase, Tempo, Deckkraft, Höhe. */
const BAENDER = [
  { welle: 2.1, phase: 0.0, tempo: 0.00021, deckkraft: 0.34, hoehe: 1.0 },
  { welle: 3.3, phase: 1.7, tempo: -0.00029, deckkraft: 0.22, hoehe: 0.72 },
  { welle: 1.6, phase: 3.4, tempo: 0.00016, deckkraft: 0.28, hoehe: 1.25 },
  { welle: 4.7, phase: 5.1, tempo: -0.00038, deckkraft: 0.14, hoehe: 0.5 },
  { welle: 2.7, phase: 2.2, tempo: 0.00027, deckkraft: 0.18, hoehe: 0.9 },
] as const

/**
 * Die Randfarbe je Zustand, als HSL-Anteile aus den Design-Tokens.
 *
 * Ausgeschrieben und nicht per `getComputedStyle` gelesen: das Canvas kennt
 * keine CSS-Variablen, und ein Auslesen je Bild wäre sechzigmal je Sekunde ein
 * Layout-Zugriff. Die Werte stehen in `tailwind.config.ts`.
 */
const RANDFARBE: Record<Sprachzustand, string> = {
  // Alles im Cyan des Panels (--primary 193 45% 86%, hier gesättigt als
  // Leuchtrand). Hier stand für „hört zu" zunächst Grün, nach der Regel der
  // Design-DNA, dass Grün den handelnden Menschen markiert. Für eine Kugel,
  // die die ganze Ansicht trägt, war das falsch: ein Farbwechsel über eine
  // solche Fläche liest sich als Zustandsalarm, nicht als Gesprächsverlauf.
  // Die Zustände unterscheiden sich jetzt in Helligkeit und Ton, nicht in der
  // Farbe — der Text darunter sagt ohnehin, was los ist.
  bereit: '187 82% 58%',
  hoert: '184 90% 64%',
  denkt: '196 75% 55%',
  spricht: '186 95% 68%',
  verbindet: '200 25% 45%',
  aus: '205 15% 32%',
}

export function Sprachblase({
  zustand,
  pegel,
  breite = 900,
  hoehe = 340,
}: {
  zustand: Sprachzustand
  pegel: () => number
  breite?: number
  hoehe?: number
}) {
  const leinwand = useRef<HTMLCanvasElement>(null)
  // Refs statt Abhängigkeiten der Schleife: ein Zustandswechsel soll die
  // Animation nicht abreissen und neu starten. Das gäbe bei jedem Wechsel
  // einen sichtbaren Ruck.
  const zustandRef = useRef(zustand)
  const pegelRef = useRef(pegel)
  zustandRef.current = zustand
  pegelRef.current = pegel

  useEffect(() => {
    const element = leinwand.current
    // jsdom hat keinen 2D-Kontext. Dann fehlt die Kugel; abstürzen darf nichts.
    const stift = element?.getContext?.('2d')
    if (!element || !stift) return

    const dichte = Math.min(2, window.devicePixelRatio || 1)
    element.width = breite * dichte
    element.height = hoehe * dichte
    stift.scale(dichte, dichte)

    const ruhig = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ?? false
    const mx = breite / 2
    const my = hoehe / 2
    const radius = Math.min(hoehe * 0.32, breite * 0.13)

    let laeuft = true
    let bild = 0
    // Der Pegel wird noch einmal nachgezogen. Die Quelle ist schon geglättet,
    // aber zwischen zwei Audioblöcken liegen rund 170 Millisekunden und
    // dazwischen zehn Bilder — ohne diesen zweiten Filter sieht man Stufen.
    let weich = 0

    const zeichne = (zeit: number) => {
      if (!laeuft) return
      weich += (pegelRef.current() - weich) * 0.12
      const farbe = RANDFARBE[zustandRef.current] ?? RANDFARBE.bereit
      const t = ruhig ? 0 : zeit

      stift.clearRect(0, 0, breite, hoehe)

      zeichneBaender(stift, { breite, mx, my, radius, farbe, t, weich })
      zeichneKugel(stift, { mx, my, radius, farbe, weich })

      bild = requestAnimationFrame(zeichne)
    }

    bild = requestAnimationFrame(zeichne)
    return () => {
      laeuft = false
      cancelAnimationFrame(bild)
    }
  }, [breite, hoehe])

  return (
    <canvas
      ref={leinwand}
      // `height: auto` mit festem Seitenverhältnis, nicht feste Höhe. Mit
      // fester Höhe und `max-w-full` staucht ein schmales Fenster nur die
      // Breite — aus der Kugel wurde ein senkrechtes Ei. Das Canvas rechnet
      // intern weiter in 900 × 340; die Skalierung macht der Browser.
      style={{ width: breite, height: 'auto', aspectRatio: `${breite} / ${hoehe}` }}
      className="pointer-events-none max-w-full select-none"
      // Die Kugel sagt nichts, was nicht danebensteht. Ein Screenreader soll
      // den Zustandstext vorlesen, keine Zeichnung beschreiben.
      aria-hidden="true"
    />
  )
}

/** Was beide Zeichenfunktionen brauchen. */
interface Kern {
  mx: number
  my: number
  radius: number
  farbe: string
  weich: number
}

/** Was zusätzlich nur die Bänder brauchen: die Fläche und die Zeit. */
interface Lage extends Kern {
  breite: number
  t: number
}

/**
 * Die fliessenden Bänder.
 *
 * Jedes ist eine Sinuslinie über die volle Breite. Die Amplitude wird von einer
 * Glocke um die Mitte gedämpft — so sind die Bänder an den Flanken der Kugel am
 * kräftigsten und laufen zu den Rändern hin aus, statt am Bildrand abgeschnitten
 * zu enden.
 */
function zeichneBaender(stift: CanvasRenderingContext2D, lage: Lage): void {
  const { breite, mx, my, radius, farbe, t, weich } = lage
  stift.save()
  stift.globalCompositeOperation = 'lighter'
  stift.lineWidth = 1

  for (const band of BAENDER) {
    stift.beginPath()
    for (let x = 0; x <= breite; x += 4) {
      const anteil = (x - mx) / breite
      // Die Glocke: nahe der Mitte voll, zum Rand hin gegen null.
      const glocke = Math.exp(-(anteil * anteil) * 9)
      const ausschlag =
        radius * 0.55 * band.hoehe * glocke * (0.28 + 1.5 * weich)
      const y =
        my +
        Math.sin(anteil * Math.PI * 2 * band.welle + band.phase + t * band.tempo) *
          ausschlag
      if (x === 0) stift.moveTo(x, y)
      else stift.lineTo(x, y)
    }
    // Ein Verlauf über die Breite, damit die Linien an den Rändern wirklich
    // verschwinden statt nur dünn zu werden.
    const strich = stift.createLinearGradient(0, 0, breite, 0)
    strich.addColorStop(0, `hsl(${farbe} / 0)`)
    strich.addColorStop(0.5, `hsl(${farbe} / ${band.deckkraft * (0.5 + weich)})`)
    strich.addColorStop(1, `hsl(${farbe} / 0)`)
    stift.strokeStyle = strich
    stift.stroke()
  }
  stift.restore()
}

/**
 * Die Kugel selbst — vier Lagen, jede mit einer Aufgabe.
 *
 * 1. Der Schein aussen: sie liegt nicht auf dem Hintergrund, sie leuchtet hinein.
 * 2. Der Leuchtrand: das, was sie zu einer Kugel macht statt zu einer Scheibe.
 * 3. Der dunkle Körper: fast schwarz, mit einem Hauch Blau zum Rand hin.
 * 4. Der Glanzpunkt oben links: eine Kugel ohne Lichtquelle ist ein Kreis.
 */
function zeichneKugel(stift: CanvasRenderingContext2D, lage: Kern): void {
  const { mx, my, radius, farbe, weich } = lage
  // Sie atmet. Wenig — vier Prozent bei voller Lautstärke; mehr sieht nach
  // Pumpen aus, nicht nach Sprechen.
  const r = radius * (1 + 0.04 * weich)

  stift.save()

  // Der Schein sitzt eng am Rand und fällt schnell ab. Er reichte zunächst
  // fast doppelt so weit — das sah aus wie Nebel um die Kugel statt wie eine
  // Kante, die leuchtet.
  stift.globalCompositeOperation = 'lighter'
  const schein = stift.createRadialGradient(mx, my, r * 0.97, mx, my, r * 1.45)
  schein.addColorStop(0, `hsl(${farbe} / ${0.34 + 0.3 * weich})`)
  schein.addColorStop(0.35, `hsl(${farbe} / ${0.1 + 0.1 * weich})`)
  schein.addColorStop(1, `hsl(${farbe} / 0)`)
  stift.fillStyle = schein
  stift.beginPath()
  stift.arc(mx, my, r * 1.45, 0, Math.PI * 2)
  stift.fill()
  stift.globalCompositeOperation = 'source-over'

  // Der dunkle Körper. Der Mittelpunkt des Verlaufs sitzt oben links, damit die
  // Kugel von dort beleuchtet wirkt — dieselbe Richtung wie der Glanzpunkt.
  const koerper = stift.createRadialGradient(
    mx - r * 0.3, my - r * 0.35, r * 0.05,
    mx, my, r,
  )
  koerper.addColorStop(0, 'hsl(205 30% 13%)')
  koerper.addColorStop(0.55, 'hsl(206 34% 7%)')
  koerper.addColorStop(1, 'hsl(206 40% 4%)')
  stift.fillStyle = koerper
  stift.beginPath()
  stift.arc(mx, my, r, 0, Math.PI * 2)
  stift.fill()

  // Der Leuchtrand, innen an der Kante. Ein gefüllter Ring statt eines
  // Strichs: ein Strich hat eine Breite, ein Verlauf hat einen Abfall — und
  // nur der Abfall sieht nach Licht aus.
  const rand = stift.createRadialGradient(mx, my, r * 0.86, mx, my, r * 1.01)
  rand.addColorStop(0, `hsl(${farbe} / 0)`)
  rand.addColorStop(0.72, `hsl(${farbe} / ${0.12 + 0.14 * weich})`)
  rand.addColorStop(0.93, `hsl(${farbe} / ${0.9 + 0.1 * weich})`)
  rand.addColorStop(0.99, `hsl(${farbe} / ${0.95 + 0.05 * weich})`)
  rand.addColorStop(1, `hsl(${farbe} / 0.2)`)
  stift.fillStyle = rand
  stift.beginPath()
  stift.arc(mx, my, r * 1.02, 0, Math.PI * 2)
  stift.fill()

  // Der Glanzpunkt. Innerhalb der Kugel beschnitten, sonst läge er auf dem
  // Hintergrund und die Kugel sähe angeklebt aus.
  stift.beginPath()
  stift.arc(mx, my, r, 0, Math.PI * 2)
  stift.clip()
  const glanz = stift.createRadialGradient(
    mx - r * 0.34, my - r * 0.4, 0,
    mx - r * 0.34, my - r * 0.4, r * 0.78,
  )
  glanz.addColorStop(0, 'hsl(200 40% 92% / 0.22)')
  glanz.addColorStop(0.5, 'hsl(200 40% 88% / 0.06)')
  glanz.addColorStop(1, 'hsl(200 40% 88% / 0)')
  stift.fillStyle = glanz
  stift.fillRect(mx - r, my - r, r * 2, r * 2)

  stift.restore()
}
