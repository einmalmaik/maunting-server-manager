/**
 * Suchmaschinen und die Entscheidung „Adresse oder Suchbegriff“.
 *
 * Die Adresszeile fragt keine Suchmaschine nach Vorschlägen: jeder Tastendruck
 * ginge sonst an Google & Co. Vorschläge kommen nur aus Verlauf und
 * Lesezeichen auf dem Gerät.
 */
import type { Marke } from '../marken'

export type SuchmaschinenId = 'google' | 'duckduckgo' | 'ecosia' | 'brave' | 'bing' | 'searxng'

export interface Suchmaschine {
  id: SuchmaschinenId
  name: string
  marke: Marke
  /** `%s` wird durch den kodierten Suchbegriff ersetzt. */
  vorlage: string
}

export const SUCHMASCHINEN: Suchmaschine[] = [
  { id: 'duckduckgo', name: 'DuckDuckGo', marke: 'duckduckgo', vorlage: 'https://duckduckgo.com/?q=%s' },
  { id: 'brave', name: 'Brave Search', marke: 'brave', vorlage: 'https://search.brave.com/search?q=%s' },
  { id: 'ecosia', name: 'Ecosia', marke: 'ecosia', vorlage: 'https://www.ecosia.org/search?q=%s' },
  { id: 'google', name: 'Google', marke: 'google', vorlage: 'https://www.google.com/search?q=%s' },
  { id: 'bing', name: 'Bing', marke: 'bing', vorlage: 'https://www.bing.com/search?q=%s' },
  { id: 'searxng', name: 'SearXNG', marke: 'searxng', vorlage: '' },
]

export function suchmaschine(id: string): Suchmaschine {
  return SUCHMASCHINEN.find((s) => s.id === id) ?? SUCHMASCHINEN[0]
}

const SCHEMA = /^[a-z][a-z0-9+.-]*:/i
const LOKAL = /^(localhost|\d{1,3}(\.\d{1,3}){3}|\[[0-9a-f:]+\])(:\d{1,5})?([/?#].*)?$/i
const DOMAIN = /^([a-z0-9¡-￿-]+\.)+[a-z¡-￿]{2,}(:\d{1,5})?([/?#].*)?$/i

/** Adresse oder Suchbegriff? Mit Leerzeichen ist es immer eine Suche. */
export function istAdresse(eingabe: string): boolean {
  const text = eingabe.trim()
  if (!text || /\s/.test(text)) return false
  if (/^https?:\/\//i.test(text) || text === 'about:blank') return true
  // `localhost:3000` beginnt wie ein Schema, ist aber keins.
  if (LOKAL.test(text)) return true
  if (SCHEMA.test(text) && !DOMAIN.test(text)) return false
  return DOMAIN.test(text)
}

/** Die Suchadresse für einen Begriff, oder `null`, wenn die Maschine nicht eingerichtet ist. */
export function suchAdresse(begriff: string, id: string, searxngUrl?: string | null): string | null {
  const q = encodeURIComponent(begriff.trim())
  if (id === 'searxng') {
    const basis = searxngBasis(searxngUrl)
    return basis ? `${basis}/search?q=${q}` : null
  }
  return suchmaschine(id).vorlage.replace('%s', q)
}

/** Nur `https://` (oder `http://` auf dem eigenen Rechner), ohne Zugangsdaten. */
export function searxngBasis(url?: string | null): string | null {
  if (!url) return null
  try {
    const u = new URL(url.trim())
    const lokal = ['localhost', '127.0.0.1', '[::1]'].includes(u.hostname)
    if (u.protocol !== 'https:' && !(u.protocol === 'http:' && lokal)) return null
    if (u.username || u.password) return null
    return `${u.origin}${u.pathname.replace(/\/+$/, '')}`
  } catch {
    return null
  }
}

/**
 * Was die Adresszeile lädt. Leere Eingabe: nichts. Eine Adresse ohne Schema
 * bekommt `https://`, auf dem eigenen Rechner `http://`. Alles andere ist ein
 * Suchbegriff; ist SearXNG gewählt, aber nicht eingerichtet, sucht
 * DuckDuckGo, statt dass nichts passiert.
 */
export function baueZielUrl(eingabe: string, id: string, searxngUrl?: string | null): string | null {
  const text = eingabe.trim()
  if (!text) return null
  if (istAdresse(text)) {
    if (/^(https?:\/\/|about:)/i.test(text)) return text
    return LOKAL.test(text) ? `http://${text}` : `https://${text}`
  }
  return suchAdresse(text, id, searxngUrl) ?? suchAdresse(text, 'duckduckgo')
}
