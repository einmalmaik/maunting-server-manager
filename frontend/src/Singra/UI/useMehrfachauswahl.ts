/**
 * Mehrfachauswahl in einer Liste oder einem Raster, wie im Explorer.
 *
 * Am Rechner mit Strg/Cmd-Klick (umschalten), Umschalt-Klick (alles zwischen
 * dem letzten und diesem), Strg+A und Escape; am Telefon mit langem Drücken.
 * Solange etwas gewählt ist, schaltet ein Klick um, statt zu öffnen. Zurück
 * beendet am Handy die Auswahl, nicht die App.
 *
 * Der Haken kennt nur Kennungen in der Reihenfolge, in der sie zu sehen sind.
 * Was ein Klick ohne Auswahl tut (öffnen) und welche Aktionen es gibt, regelt
 * der Aufrufer; Texte trägt der Haken keine.
 */
import { useCallback, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { useZurueckSchliesst } from '@/hooks/useZurueckSchliesst'

/** So lange nach einem langen Druck gilt der folgende Klick als dessen Ende, nicht als neuer Tipp. */
const KLICK_NACH_LANGDRUCK_MS = 700

/** Welche Tasten beim Klick gedrückt waren. */
export type Auswahltasten = Pick<MouseEvent, 'ctrlKey' | 'metaKey' | 'shiftKey'>

export interface Mehrfachauswahl {
  /** Gewählte Kennungen; `null` heißt: keine Auswahl. Eine leere Menge ist der Auswahlmodus ohne Treffer. */
  auswahl: ReadonlySet<string> | null
  /**
   * Ein Klick auf einen Eintrag. `true`: er gehörte der Auswahl (oder beendete
   * einen langen Druck); `false`: keine Auswahl im Spiel, der Aufrufer öffnet.
   */
  klick: (id: string, tasten: Auswahltasten) => boolean
  /** Langes Drücken: wählt `id` dazu und schaltet den Auswahlmodus ein. */
  langdruck: (id: string) => void
  umschalten: (id: string) => void
  /** Alles zwischen dem letzten Umgeschalteten und `id`; ohne diesen nur `id`. */
  bereich: (id: string) => void
  alle: () => void
  /** Auswahlmodus ohne Treffer, etwa über einen Knopf „Auswählen“. */
  starten: () => void
  leeren: () => void
  /** Strg/Cmd+A und Escape. `true`, wenn die Taste hier verbraucht wurde. */
  taste: (event: KeyboardEvent) => boolean
}

export function useMehrfachauswahl(reihenfolge: readonly string[]): Mehrfachauswahl {
  const [auswahl, setAuswahl] = useState<Set<string> | null>(null)
  /** Ausgangspunkt für Umschalt-Klick. */
  const anker = useRef<string | null>(null)
  const letzterLangdruck = useRef(0)
  // Über Refs, damit die Rückrufe gleich bleiben und gemerkte Kacheln nicht neu zeichnen.
  const reihe = useRef(reihenfolge)
  reihe.current = reihenfolge
  const aktiv = useRef(false)
  aktiv.current = auswahl !== null

  const leeren = useCallback(() => {
    setAuswahl(null)
    anker.current = null
  }, [])
  useZurueckSchliesst(auswahl !== null, leeren)

  const umschalten = useCallback((id: string) => {
    setAuswahl((a) => {
      const neu = new Set(a ?? [])
      if (neu.has(id)) neu.delete(id)
      else neu.add(id)
      return neu.size > 0 ? neu : null
    })
    anker.current = id
  }, [])

  const bereich = useCallback(
    (id: string) => {
      const ids = reihe.current
      const von = anker.current === null ? -1 : ids.indexOf(anker.current)
      const bis = ids.indexOf(id)
      if (von < 0 || bis < 0) return umschalten(id)
      setAuswahl(new Set(ids.slice(Math.min(von, bis), Math.max(von, bis) + 1)))
    },
    [umschalten],
  )

  const klick = useCallback(
    (id: string, tasten: Auswahltasten) => {
      if (Date.now() - letzterLangdruck.current < KLICK_NACH_LANGDRUCK_MS) return true
      if (tasten.shiftKey) bereich(id)
      else if (tasten.ctrlKey || tasten.metaKey || aktiv.current) umschalten(id)
      else return false
      return true
    },
    [bereich, umschalten],
  )

  const langdruck = useCallback((id: string) => {
    letzterLangdruck.current = Date.now()
    setAuswahl((a) => new Set([...(a ?? []), id]))
    anker.current = id
  }, [])

  const alle = useCallback(() => {
    if (reihe.current.length > 0) setAuswahl(new Set(reihe.current))
  }, [])

  const starten = useCallback(() => setAuswahl((a) => a ?? new Set()), [])

  const taste = useCallback(
    (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a' && reihe.current.length > 0) {
        event.preventDefault()
        alle()
        return true
      }
      if (event.key === 'Escape' && aktiv.current) {
        event.preventDefault()
        leeren()
        return true
      }
      return false
    },
    [alle, leeren],
  )

  return { auswahl, klick, langdruck, umschalten, bereich, alle, starten, leeren, taste }
}
