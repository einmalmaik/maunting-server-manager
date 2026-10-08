/**
 * Der Jugend- und Suchtschutz für die Einstellungen. Was gilt, wann eine
 * Lockerung bestätigt werden kann und ob gebunden ist, entscheidet Rust
 * (`schild/schutz.rs`); hier wird nur angezeigt und gewünscht.
 *
 * Der `wunsch` ist, was gelten soll: der offene Antrag, sonst die geltenden
 * Regeln. Jede Änderung geht vom jüngsten Wunsch aus, auch von einem, den
 * Rust noch nicht bestätigt hat, und die Änderungen gehen nacheinander
 * hinaus. Sonst rechnete ein zweiter schneller Klick mit dem alten Stand und
 * machte aus zwei Verschärfungen eine Lockerung (Bugjagd 08.10.2026).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { TFunction } from 'i18next'

import { nativ, schutzEreignisse, type SchutzRegeln, type SchutzStand } from './nativ'

/** Sekunden seit dem letzten Stand von Rust, zählt jede Sekunde hoch. */
function useVergangen(geholt: number, laeuft: boolean): number {
  const [jetzt, setJetzt] = useState(() => Date.now())
  useEffect(() => {
    if (!laeuft) return
    const takt = window.setInterval(() => setJetzt(Date.now()), 1000)
    return () => window.clearInterval(takt)
  }, [laeuft])
  return Math.floor(Math.max(0, jetzt - geholt) / 1000)
}

/** Was von einer Frist nach `vergangen` Sekunden übrig ist. */
export const uebrig = (sekunden: number, vergangen: number) => Math.max(0, sekunden - vergangen)

export function useSchutz() {
  const [stand, setStand] = useState<SchutzStand | null>(null)
  const [geholt, setGeholt] = useState(() => Date.now())
  // Gewünscht, aber von Rust noch nicht beantwortet. Die Ref gilt sofort, der
  // State für die Anzeige.
  const ausstehend = useRef<SchutzRegeln | null>(null)
  const [ausstehendAnzeige, setAusstehendAnzeige] = useState<SchutzRegeln | null>(null)
  const kette = useRef<Promise<unknown>>(Promise.resolve())
  const offen = useRef(0)

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

  /** Hängt einen Aufruf an die Kette; erst wenn sie leer ist, zählt wieder der Stand von Rust. */
  const nacheinander = useCallback(
    (aufruf: () => Promise<SchutzStand | null>) => {
      offen.current += 1
      const lauf = kette.current.then(aufruf).then(uebernehmen)
      const fertig = () => {
        offen.current -= 1
        if (offen.current === 0) {
          ausstehend.current = null
          setAusstehendAnzeige(null)
        }
      }
      kette.current = lauf.then(fertig, fertig)
      return lauf
    },
    [uebernehmen],
  )

  const fristen = stand ? [stand.antrag?.rest_sekunden, stand.antrag?.fenster_sekunden, stand.gebunden_sekunden, stand.abkuehlen_sekunden] : []
  const vergangen = useVergangen(geholt, fristen.some((f) => (f ?? 0) > 0))
  const wunsch = ausstehendAnzeige ?? (stand ? (stand.antrag?.ziel ?? stand.regeln) : null)

  return {
    stand,
    wunsch,
    vergangen,
    /** `teil` wird auf den jüngsten Wunsch gelegt; als Funktion bekommt es ihn. */
    aendern: (teil: Partial<SchutzRegeln> | ((w: SchutzRegeln) => Partial<SchutzRegeln>)) => {
      const basis = ausstehend.current ?? (stand ? (stand.antrag?.ziel ?? stand.regeln) : null)
      if (!basis) return Promise.resolve()
      const neu = { ...basis, ...(typeof teil === 'function' ? teil(basis) : teil) }
      ausstehend.current = neu
      setAusstehendAnzeige(neu)
      return nacheinander(() => nativ.schutzAendern(neu))
    },
    binden: (tage: number) => nacheinander(() => nativ.schutzBinden(tage)),
    abbrechen: () => nacheinander(() => nativ.schutzAbbrechen()),
    bestaetigen: () => nacheinander(() => nativ.schutzBestaetigen()),
  }
}

/** `14:05`, `23:59:58`, ab einem Tag „3 Tage, 04:10:00“. */
export function dauerText(t: TFunction, sekunden: number): string {
  const s = Math.max(0, Math.floor(sekunden))
  const zwei = (n: number) => String(n).padStart(2, '0')
  const tage = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const ms = `${zwei(Math.floor((s % 3600) / 60))}:${zwei(s % 60)}`
  if (tage > 0) return t('browser.schutz.dauerTage', { count: tage, zeit: `${zwei(h)}:${ms}` })
  return h > 0 ? `${h}:${ms}` : `${Math.floor(s / 60)}:${zwei(s % 60)}`
}
