/**
 * Lage eines Popovers an seinem Auslöser, und die Stapelordnung der App.
 *
 * **Stapelordnung.** Inhalt bleibt bei z ≤ 40. Darüber, jeweils per Portal an
 * `body`:
 *
 * - 50: Dialog (`.msm-modal-overlay`, `Lichtbox`)
 * - 70: Blatt (`Blattmenue`, Vollbild-Listen im Messenger)
 * - 100 bis 120: Popover (`Dropdown` 100, Menüs und Auswahlen über
 *   `useAnkerLage` 110, `Kontextmenue` 120)
 * - 9999: Toast
 *
 * Ohne Portal zählt ein `z-50` nichts: `Shell.tsx` legt den Inhaltsbereich mit
 * `relative z-10` in einen eigenen Stapelkontext, und alles darin liegt nach
 * außen auf 10, unter Kopfzeile und Navigation.
 *
 * **Lage.** Ein Popover wird vor dem ersten Zeichnen gemessen und so gesetzt,
 * dass es ganz im Fenster bleibt. Passt es auf der gewünschten Seite nicht und
 * ist auf der anderen mehr Platz, klappt es um. Während es offen ist, folgt es
 * Fenstergröße und Scrollen.
 */
import { useLayoutEffect, useState, type CSSProperties, type RefObject } from 'react'

export const SCHICHT = {
  inhalt: 40,
  dialog: 50,
  blatt: 70,
  popover: 110,
  kontextmenue: 120,
  toast: 9999,
} as const

/** Abstand zum Fensterrand. */
export const RAND = 8

export interface Ankerbox {
  left: number
  right: number
  top: number
  bottom: number
}

export interface Ankerwunsch {
  /** Unter oder über dem Auslöser. */
  seite?: 'unten' | 'oben'
  /** `start`: linke Kanten bündig, das Popover wächst nach rechts. `ende`: rechte Kanten bündig. */
  ausrichtung?: 'start' | 'ende'
  /** Lücke zwischen Auslöser und Popover. */
  abstand?: number
}

/** Wohin ein Popover der Größe `breite` × `hoehe` an `anker` passt. */
export function ankerLage(
  anker: Ankerbox,
  breite: number,
  hoehe: number,
  fenster: { breite: number; hoehe: number },
  { seite = 'unten', ausrichtung = 'start', abstand = 0 }: Ankerwunsch = {},
) {
  const platzUnten = fenster.hoehe - RAND - (anker.bottom + abstand)
  const platzOben = anker.top - abstand - RAND
  const nachUnten = seite === 'unten' ? platzUnten >= hoehe || platzUnten >= platzOben : !(platzOben >= hoehe || platzOben >= platzUnten)
  const platzRechts = fenster.breite - RAND - anker.left
  const platzLinks = anker.right - RAND
  const nachRechts = ausrichtung === 'start' ? platzRechts >= breite || platzRechts >= platzLinks : !(platzLinks >= breite || platzLinks >= platzRechts)

  const top = nachUnten ? anker.bottom + abstand : anker.top - abstand - hoehe
  const left = nachRechts ? anker.left : anker.right - breite
  return {
    left: Math.max(RAND, Math.min(left, fenster.breite - breite - RAND)),
    top: Math.max(RAND, Math.min(top, fenster.hoehe - hoehe - RAND)),
  }
}

/**
 * Hält ein offenes Popover an seinem Auslöser. Das Popover wird gerendert,
 * sobald `offen` gilt, und trägt den zurückgegebenen Stil: bis zur ersten
 * Messung unsichtbar in der Ecke (dort hat es seine volle Breite), danach an
 * seinem Platz. `mindestensAnkerbreite` macht es mindestens so breit wie der Auslöser.
 *
 * Das Popover selbst trägt `max-h-[calc(100dvh-1rem)] overflow-y-auto`, damit
 * es auch in einem niedrigen Fenster ganz sichtbar bleibt.
 */
export function useAnkerLage(
  offen: boolean,
  anker: RefObject<HTMLElement | null>,
  popover: RefObject<HTMLElement | null>,
  { seite = 'unten', ausrichtung = 'start', abstand = RAND, mindestensAnkerbreite = false }: Ankerwunsch & { mindestensAnkerbreite?: boolean } = {},
): CSSProperties {
  const [lage, setLage] = useState<{ left: number; top: number; ankerbreite: number } | null>(null)

  // Layout-Effekt: gemessen und gesetzt wird vor dem Zeichnen, damit das Popover
  // nie einen Frame lang über den Rand ragt oder an der alten Stelle steht.
  useLayoutEffect(() => {
    if (!offen) {
      setLage(null)
      return
    }
    const setzen = () => {
      const a = anker.current?.getBoundingClientRect()
      const p = popover.current?.getBoundingClientRect()
      if (!a || !p) return
      const breite = mindestensAnkerbreite ? Math.max(p.width, a.width) : p.width
      const fenster = { breite: window.innerWidth, hoehe: window.innerHeight }
      // Höher als das Fenster wird es nicht (`max-h-[calc(100dvh-1rem)]` am Popover);
      // gerechnet wird mit der Höhe, die bleibt.
      const hoehe = Math.min(p.height, fenster.hoehe - 2 * RAND)
      setLage({ ...ankerLage(a, breite, hoehe, fenster, { seite, ausrichtung, abstand }), ankerbreite: a.width })
    }
    setzen()
    window.addEventListener('resize', setzen)
    window.addEventListener('scroll', setzen, true)
    return () => {
      window.removeEventListener('resize', setzen)
      window.removeEventListener('scroll', setzen, true)
    }
  }, [offen, anker, popover, seite, ausrichtung, abstand, mindestensAnkerbreite])

  const breite = mindestensAnkerbreite && lage ? { minWidth: lage.ankerbreite } : {}
  return lage
    ? { position: 'fixed', zIndex: SCHICHT.popover, left: lage.left, top: lage.top, ...breite }
    : { position: 'fixed', zIndex: SCHICHT.popover, left: 0, top: 0, visibility: 'hidden' }
}
