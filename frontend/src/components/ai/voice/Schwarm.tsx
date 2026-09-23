import { useEffect, useRef } from 'react'
import type { Swarm, SwarmState } from '@maunting/design-dna/swarm'

import type { Sprachzustand } from './useSprachsitzung'

/**
 * Der Sprachschwarm — neuntausend Lichtpunkte, die eine Form annehmen:
 * Sternenglobus in Ruhe, das MSM-Logo beim Arbeiten, die Erde bei einer
 * Regionalanalyse.
 *
 * Gezeichnet wird er in der Design-DNA (`@maunting/design-dna/swarm`), damit
 * Panel, Desktop-Overlay und Android-App denselben Schwarm zeigen und keine
 * eine eigene Fassung pflegt. Hier steht nur, was MSM daraus macht: welcher
 * Sitzungszustand welche Form ist, wann ein Werkzeug einen Lichtring
 * auslöst und welcher Ort zur Erde wird.
 *
 * **Nachgeladen, nie im Startpaket.** Das Modul mit den Shadern kommt erst,
 * wenn ein Schwarm wirklich erscheint; bis dahin ist die Fläche leer, und der
 * Zustandstext daneben sagt ohnehin, was los ist. Scheitert das Nachladen
 * (Netz weg, Aktualisierung mitten im Betrieb), bleibt es dabei — ein
 * fehlender Schwarm darf den Sprachmodus nicht mitreißen.
 *
 * Der Schwarm sagt nichts, was nicht danebensteht: `aria-hidden`, damit ein
 * Screenreader den Zustandstext vorliest und keine Zeichnung beschreibt.
 */

export type SchwarmZustand = SwarmState

/**
 * Welche Form zu einer Sitzung gehört.
 *
 * Eine Störung schlägt alles andere: sie steht so lange, bis die Leitung
 * wieder trägt (`LEITUNG_TRAEGT` im Hook nimmt den Fehler zurück). Danach
 * das planmäßige Ende — der Schwarm legt sich als Scheibe ab, während neu
 * verbunden wird. „Denkt" in einem Zug mit Werkzeug ist Arbeit: das Logo.
 */
export function schwarmZustand({
  zustand,
  fehler = null,
  abgelaufen = false,
  werkzeugLaeuft = false,
}: {
  zustand: Sprachzustand
  fehler?: string | null
  abgelaufen?: boolean
  werkzeugLaeuft?: boolean
}): SwarmState {
  if (fehler) return 'fault'
  if (abgelaufen) return 'expired'
  switch (zustand) {
    case 'aus':
      return 'off'
    case 'verbindet':
      return 'connecting'
    case 'hoert':
      return 'listening'
    case 'denkt':
      return werkzeugLaeuft ? 'working' : 'thinking'
    case 'spricht':
      return 'speaking'
    default:
      return 'ready'
  }
}

export interface SchwarmOrt {
  latitude: number
  longitude: number
}

export function Schwarm({
  zustand,
  pegel,
  ort = null,
  impulse = 0,
  vorfuehrung = false,
  className,
}: {
  zustand: SwarmState
  /** Lautstärke zwischen 0 und 1; der Schwarm liest sie je Bild selbst. */
  pegel: () => number
  /** Solange gesetzt, wird der Schwarm zur Erde und dreht diesen Ort nach vorn. */
  ort?: SchwarmOrt | null
  /**
   * Ein Zähler: jedes Mal, wenn er steigt, läuft ein Lichtring durch den
   * Schwarm. Ein Zähler und kein Werkzeugname, damit auch dasselbe Werkzeug
   * zweimal hintereinander zweimal aufleuchtet.
   */
  impulse?: number
  /**
   * Sprechen ohne Mikrofon: Silben und Pausen statt eines echten Pegels. Für
   * das Schaufenster des Overlays, in dem niemand spricht, der Schwarm aber
   * zeigen soll, wie er es im Ernstfall täte.
   */
  vorfuehrung?: boolean
  className?: string
}) {
  const buehne = useRef<HTMLDivElement>(null)
  const schwarm = useRef<Swarm | null>(null)
  // Refs statt Abhängigkeiten: ein neuer Pegel oder ein Zustandswechsel soll
  // den Schwarm nicht abbauen und neu aufbauen — das wäre ein sichtbarer Ruck.
  const pegelRef = useRef(pegel)
  pegelRef.current = pegel
  const vorfuehrungRef = useRef(vorfuehrung)
  vorfuehrungRef.current = vorfuehrung
  const zustandRef = useRef(zustand)
  zustandRef.current = zustand
  const breite = ort?.latitude
  const laenge = ort?.longitude
  const ortRef = useRef<SchwarmOrt | null>(ort)
  ortRef.current = ort

  useEffect(() => {
    const element = buehne.current
    if (!element) return
    let abgebaut = false
    const beginn = performance.now()
    void import('@maunting/design-dna/swarm')
      .then(({ createSwarm, simulatedSpeech }) => {
        if (abgebaut) return
        const silben = simulatedSpeech(7)
        schwarm.current = createSwarm(element, {
          state: zustandRef.current,
          earth: ortRef.current,
          level: () => {
            if (!vorfuehrungRef.current) return pegelRef.current()
            const z = zustandRef.current
            return z === 'listening' || z === 'speaking' ? silben((performance.now() - beginn) / 1000) : 0
          },
        })
      })
      .catch(() => undefined)
    return () => {
      abgebaut = true
      schwarm.current?.destroy()
      schwarm.current = null
    }
  }, [])

  useEffect(() => {
    schwarm.current?.update({
      state: zustand,
      earth: breite === undefined || laenge === undefined ? null : { latitude: breite, longitude: laenge },
    })
  }, [zustand, breite, laenge])

  const letzterImpuls = useRef(impulse)
  useEffect(() => {
    if (impulse > letzterImpuls.current) schwarm.current?.pulse()
    letzterImpuls.current = impulse
  }, [impulse])

  return <div ref={buehne} className={className} aria-hidden="true" />
}
