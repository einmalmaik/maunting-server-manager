/**
 * Ein Knopf, den eine Seite auslösen kann, nimmt kurz nach dem Erscheinen
 * noch keinen Klick an. Sonst lässt eine Seite ihn genau dann erscheinen, wenn
 * der Nutzer ohnehin klickt oder Enter drückt, und der Klick, der der Seite
 * galt, erlaubt die Kamera oder füllt das Passwort ein. Chrome hält seine
 * Rückfragen ebenso kurz zu.
 */
import { useLayoutEffect, useRef, type SyntheticEvent } from 'react'

export const KLICKSPERRE_MS = 500

/**
 * Für `onClickCapture` am Rahmen der Knöpfe. Die Frist beginnt neu, sobald
 * sich `schluessel` ändert (eine andere Frage im selben Rahmen). Auch Enter
 * und Leertaste lösen einen Klick aus und werden mit abgefangen.
 */
export function useKlickSperre(schluessel?: unknown): (e: SyntheticEvent) => void {
  const seit = useRef(0)
  // Vor dem Zeichnen: was zu sehen ist, ist schon gesperrt.
  useLayoutEffect(() => {
    seit.current = Date.now()
  }, [schluessel])
  return (e) => {
    if (Date.now() - seit.current >= KLICKSPERRE_MS) return
    e.preventDefault()
    e.stopPropagation()
  }
}
