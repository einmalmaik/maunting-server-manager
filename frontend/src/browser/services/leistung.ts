/**
 * Wann Tabs im Hintergrund schlafen (Einstellungen → Leistung). Die Regeln
 * setzt Rust durch (`tabs/ruhe.rs`); die Oberfläche schickt sie beim Start und
 * nach jeder Änderung.
 */
import { useEffect } from 'react'

import { useEinstellungenStore } from './einstellungenStore'
import { nativ } from './nativ'

const HOST = /^[a-z0-9.-]{1,253}$/

/** Ein Host für die Ausnahmeliste aus einer Eingabe wie `youtube.com` oder einer ganzen Adresse; `null`, wenn keiner. */
export function ausnahmeHost(eingabe: string): string | null {
  const text = eingabe.trim().toLowerCase().replace(/^\*\./, '')
  // Immer über `URL`, auch ohne Schema: sonst blieb `bücher.de` ohne
  // Punycode und galt als ungültig, mit `https://` davor aber nicht (bis 09.10.2026).
  let host: string
  try {
    host = new URL(/^https?:\/\//.test(text) ? text : `https://${text}`).hostname
  } catch {
    return null
  }
  host = host.replace(/^www\./, '')
  return HOST.test(host) && host.includes('.') && !host.startsWith('.') && !host.endsWith('.') ? host : null
}

export function useLeistung() {
  const schlafenNach = useEinstellungenStore((s) => s.schlafenNach)
  const speicherSparen = useEinstellungenStore((s) => s.speicherSparen)
  const ausnahmen = useEinstellungenStore((s) => s.schlafAusnahmen)
  useEffect(() => {
    void nativ.tabsLeistung(schlafenNach || null, speicherSparen, ausnahmen).catch(() => null)
  }, [schlafenNach, speicherSparen, ausnahmen])
}
