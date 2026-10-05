/**
 * Die Hülle der Bestätigungskarten — im Hauptfenster als Dialog über allem,
 * im Sprach-Overlay kompakt.
 *
 * Im Overlay gibt es keinen abgedunkelten Kasten (Schwarm-DNA: das Fenster ist
 * durchsichtig und schwebt über dem Desktop). Die Karte steht dort als
 * schlichte Karte über dem Schwarm, nimmt als einzige Fläche neben dem X Klicks
 * an (`durchklick.rs`, gemeldet von `OverlayFenster`) und lässt sich mit ESC
 * ablehnen — ESC beendet dort sonst die ganze Sitzung.
 *
 * Die Logik der Karten bleibt dieselbe; nur die Hülle wechselt.
 */
import { useEffect, useRef, type ReactNode } from 'react'

export interface KartenHuellenProps {
  /** Im Sprach-Overlay: kompakt, ohne Kasten. */
  kompakt?: boolean
  /** Meldet dem Overlay, dass eine Karte steht (für Größe, Fokus, Klickfläche). */
  onSichtbar?: (offen: boolean) => void
}

export function KartenHuelle({
  kompakt = false,
  onSichtbar,
  beschriftung,
  breite = 'max-w-lg',
  onAbbrechen,
  children,
}: KartenHuellenProps & {
  beschriftung: string
  breite?: string
  /** ESC im Overlay: ablehnen, nicht die Sitzung beenden. */
  onAbbrechen: () => void
  children: ReactNode
}) {
  const abbrechen = useRef(onAbbrechen)
  useEffect(() => {
    abbrechen.current = onAbbrechen
  })

  const melden = useRef(onSichtbar)
  useEffect(() => {
    melden.current?.(true)
    return () => melden.current?.(false)
  }, [])

  useEffect(() => {
    if (!kompakt) return
    const taste = (ereignis: KeyboardEvent) => {
      if (ereignis.key !== 'Escape') return
      // Vor dem Overlay: dessen ESC schlösse sonst die Sitzung mit.
      ereignis.stopImmediatePropagation()
      abbrechen.current()
    }
    window.addEventListener('keydown', taste, { capture: true })
    return () => window.removeEventListener('keydown', taste, { capture: true })
  }, [kompakt])

  if (kompakt) {
    return (
      <div
        data-overlay-karte
        role="dialog"
        aria-label={beschriftung}
        className="msm-card pointer-events-auto w-full p-4 shadow-panel"
      >
        {children}
      </div>
    )
  }
  return (
    <div className="msm-modal-overlay" role="dialog" aria-modal="true" aria-label={beschriftung}>
      <div className={`msm-card w-full ${breite} p-6 shadow-panel`}>{children}</div>
    </div>
  )
}
