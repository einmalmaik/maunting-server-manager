/**
 * Seitenfarbschema (Einstellungen → Design). Ob eine Seite dunkel rendert,
 * entscheidet die WebView (Windows: WebView2, Android: System-WebView); die
 * Oberfläche schickt den Wunsch beim Start und nach jeder Änderung. Eine
 * Logik für beide Plattformen.
 */
import { useEffect } from 'react'

import { useEinstellungenStore } from './einstellungenStore'
import { nativ } from './nativ'

export function useDarstellung() {
  const seitenDunkel = useEinstellungenStore((s) => s.seitenDunkel)
  useEffect(() => {
    void nativ.tabsDarstellung(seitenDunkel).catch(() => null)
  }, [seitenDunkel])
}
