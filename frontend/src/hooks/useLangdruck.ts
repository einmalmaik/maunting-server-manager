/**
 * Langes Drücken — am Telefon der Weg zu allem, was am Rechner ein Hover ist.
 *
 * Die Knöpfe an einer Nachrichtenblase hingen bisher an `group-hover`. Wo es
 * keinen Zeiger gibt, gibt es kein Hover, und damit war Bearbeiten, Löschen und
 * alles Weitere am Telefon schlicht nicht erreichbar. Dieser Haken ist die
 * Gegenseite: gedrückt halten öffnet dieselbe Auswahl.
 *
 * Mit der Maus passiert hier **nichts**. Ein wartender Mausklick fühlt sich
 * kaputt an, und die rechte Taste gehört dem Browser: wer eine Nachricht
 * markiert und kopiert, braucht dessen Menü. Am Rechner führt der gewohnte
 * Weg über den Zeiger, nicht über diesen Haken.
 */

import { useCallback, useEffect, useRef } from 'react'

import { hatSichBewegt, LANGDRUCK_MS, rueckmeldung } from '@/lib/gesten'

export interface LangdruckOptionen {
  /** Wie lange gedrückt werden muss. Vorgabe: `LANGDRUCK_MS`. */
  dauerMs?: number
  /** Auf `false` ruht der Haken vollständig, etwa im laufenden Auswahlmodus. */
  aktiv?: boolean
}

/** Was an das angefasste Element gehängt wird. */
export interface LangdruckBindung {
  onPointerDown: (e: React.PointerEvent) => void
  onPointerMove: (e: React.PointerEvent) => void
  onPointerUp: () => void
  onPointerCancel: () => void
  onContextMenu: (e: React.MouseEvent) => void
}

export function useLangdruck(
  beiAusloesen: () => void,
  optionen: LangdruckOptionen = {},
): LangdruckBindung {
  const { dauerMs = LANGDRUCK_MS, aktiv = true } = optionen

  const uhr = useRef<ReturnType<typeof setTimeout> | null>(null)
  const start = useRef<{ x: number; y: number } | null>(null)
  /** Womit zuletzt gedrückt wurde — entscheidet über das Kontextmenü. */
  const letzteArt = useRef<string>('')

  // Damit eine neue Rückmeldung keine laufende Uhr neu aufsetzt.
  const ausloesen = useRef(beiAusloesen)
  ausloesen.current = beiAusloesen

  const abbrechen = useCallback(() => {
    if (uhr.current !== null) {
      clearTimeout(uhr.current)
      uhr.current = null
    }
    start.current = null
  }, [])

  // Eine Uhr, die nach dem Ausbauen der Komponente ausläuft, griffe ins Leere.
  useEffect(() => abbrechen, [abbrechen])

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      letzteArt.current = e.pointerType || ''
      if (!aktiv) return
      // Die Maus wartet nicht.
      if (e.pointerType === 'mouse') return
      abbrechen()
      start.current = { x: e.clientX, y: e.clientY }
      uhr.current = setTimeout(() => {
        uhr.current = null
        start.current = null
        rueckmeldung()
        ausloesen.current()
      }, dauerMs)
    },
    [abbrechen, aktiv, dauerMs],
  )

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      const angefasst = start.current
      if (!angefasst || uhr.current === null) return
      // Wer scrollt, will nichts auswählen.
      if (hatSichBewegt(e.clientX - angefasst.x, e.clientY - angefasst.y)) {
        abbrechen()
      }
    },
    [abbrechen],
  )

  const onContextMenu = useCallback(
    (e: React.MouseEvent) => {
      // Der Rechtsklick bleibt beim Browser. Nur am Finger wird unterdrückt:
      // dort folgt dem langen Druck sonst noch das Systemmenü mit eigener
      // Textauswahl, und beides läge übereinander.
      if (!aktiv || letzteArt.current === 'mouse' || !letzteArt.current) return
      e.preventDefault()
    },
    [aktiv],
  )

  return {
    onPointerDown,
    onPointerMove,
    onPointerUp: abbrechen,
    onPointerCancel: abbrechen,
    onContextMenu,
  }
}
