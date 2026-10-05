/**
 * Eine Karte, zwei Fenster: Hauptfenster und Sprach-Overlay zeigen dieselbe
 * Bestätigungskarte. Wer zuerst entscheidet, gewinnt — und sagt es allen
 * übrigen Fenstern mit `mss:karte-erledigt`, damit dort keine Karte stehen
 * bleibt, deren Auftrag schon beantwortet ist.
 *
 * Kommt ein zweiter Klick trotzdem durch, antwortet Rust mit
 * `MSS_NICHTS_WARTET` (`auftrag::NICHTS_WARTET`). Das ist kein Fehler des
 * Auftrags: das erste Fenster hat sein Ergebnis längst gemeldet, und ein
 * zweites `DESKTOP_TOOL_FAILED` würde es im Wettlauf überschreiben.
 */
import { useEffect, useRef } from 'react'
import { emit, listen } from '@tauri-apps/api/event'

export const EREIGNIS_KARTE_ERLEDIGT = 'mss:karte-erledigt'

/** Die Marke, mit der Rust „zu dieser Kennung wartet nichts“ beginnt. */
export const NICHTS_WARTET = 'MSS_NICHTS_WARTET'

export function wartetNichts(fehler: unknown): boolean {
  const text = fehler instanceof Error ? fehler.message : String(fehler)
  return text.startsWith(NICHTS_WARTET)
}

/** Allen Fenstern sagen, dass die Karte zu `auftragId` beantwortet ist. */
export function karteErledigtMelden(auftragId: string): void {
  void emit(EREIGNIS_KARTE_ERLEDIGT, { auftrag_id: auftragId }).catch(() => {})
}

/**
 * Ruft `schliessen` mit der Kennung jeder anderswo beantworteten Karte. Der
 * Rückruf steht in einem Ref, damit sich der Listener nicht bei jedem Render
 * ab- und wieder anmeldet (beides ist asynchron, und genau in das Fenster
 * dazwischen fiele das Ereignis).
 */
export function useKarteErledigt(schliessen: (auftragId: string) => void): void {
  const rueckruf = useRef(schliessen)
  useEffect(() => {
    rueckruf.current = schliessen
  })
  useEffect(() => {
    const abmelden = listen<{ auftrag_id?: string }>(EREIGNIS_KARTE_ERLEDIGT, (ereignis) => {
      const id = ereignis.payload?.auftrag_id
      if (id) rueckruf.current(id)
    })
    return () => {
      void abmelden.then((weg) => weg())
    }
  }, [])
}
