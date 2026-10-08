/**
 * Der Jugend- und Suchtschutz für die Einstellungen. Was gilt und wann eine
 * Lockerung greift, entscheidet Rust (`schild/schutz.rs`); hier wird nur
 * angezeigt und gewünscht. Der `wunsch` ist, was gelten soll: der offene
 * Antrag, sonst die geltenden Regeln. Jede Änderung geht vom Wunsch aus,
 * damit eine wartende Lockerung nicht still verloren geht.
 */
import { useCallback, useEffect, useState } from 'react'

import { nativ, schutzEreignisse, type SchutzRegeln, type SchutzStand } from './nativ'

/** Sekunden bis zur Lockerung, zählt jede Sekunde herunter. */
function useRest(stand: SchutzStand | null, geholt: number): number {
  const [jetzt, setJetzt] = useState(() => Date.now())
  const offen = stand?.antrag != null
  useEffect(() => {
    if (!offen) return
    const takt = window.setInterval(() => setJetzt(Date.now()), 1000)
    return () => window.clearInterval(takt)
  }, [offen])
  const rest = stand?.antrag?.rest_sekunden ?? 0
  return Math.max(0, rest - Math.floor(Math.max(0, jetzt - geholt) / 1000))
}

export function useSchutz() {
  const [stand, setStand] = useState<SchutzStand | null>(null)
  const [geholt, setGeholt] = useState(() => Date.now())

  const uebernehmen = useCallback((s: SchutzStand | null) => {
    if (!s) return
    setStand(s)
    setGeholt(Date.now())
  }, [])

  const laden = useCallback(() => {
    void nativ.schutzStand().then(uebernehmen).catch(() => null)
  }, [uebernehmen])

  useEffect(() => {
    laden()
    let aus: (() => void) | null = null
    let weg = false
    void schutzEreignisse(laden).then((f) => {
      if (weg) f()
      else aus = f
    })
    return () => {
      weg = true
      aus?.()
    }
  }, [laden])

  const rest = useRest(stand, geholt)
  return {
    stand,
    wunsch: stand ? (stand.antrag?.ziel ?? stand.regeln) : null,
    rest,
    aendern: (regeln: SchutzRegeln) => nativ.schutzAendern(regeln).then(uebernehmen),
    abbrechen: () => nativ.schutzAbbrechen().then(uebernehmen),
    bestaetigen: (text: string) => nativ.schutzBestaetigen(text).then(uebernehmen),
  }
}

/** `14:05` oder `23:59:58`. */
export function dauerText(sekunden: number): string {
  const s = Math.max(0, Math.floor(sekunden))
  const zwei = (n: number) => String(n).padStart(2, '0')
  const h = Math.floor(s / 3600)
  return h > 0 ? `${h}:${zwei(Math.floor((s % 3600) / 60))}:${zwei(s % 60)}` : `${Math.floor(s / 60)}:${zwei(s % 60)}`
}
