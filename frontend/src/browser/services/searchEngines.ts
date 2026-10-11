/**
 * Suchmaschinen und die Entscheidung „Adresse oder Suchbegriff“.
 *
 * Die Adresszeile fragt keine Suchmaschine nach Vorschlägen: jeder Tastendruck
 * ginge sonst an Google & Co. Vorschläge kommen nur aus Verlauf und
 * Lesezeichen auf dem Gerät.
 */
import type { Marke } from '../marken'
import { msmSucheAdresse } from './intern'
import { istGekoppelt, useSitzung } from './sitzung'

export type SuchmaschinenId = 'searxng' | 'duckduckgo' | 'brave' | 'ecosia' | 'google' | 'bing' | 'msm'

export interface Suchmaschine {
  id: SuchmaschinenId
  name: string
  /** Ohne Marke: trägt das Standard-Suchsymbol oder App-Logo. */
  marke?: Marke
  /** `%s` wird durch den kodierten Suchbegriff ersetzt. */
  vorlage: string
  /**
   * Bildsuche des Such-Widgets: dorthin geht das Foto als `multipart/form-data`
   * im Feld `feld`, bei `base64` als Text statt als Datei.
   */
  bild?: { url: string; feld: string; base64?: boolean }
}

export const SUCHMASCHINEN: Suchmaschine[] = [
  { id: 'duckduckgo', name: 'DuckDuckGo', marke: 'duckduckgo', vorlage: 'https://duckduckgo.com/?q=%s' },
  { id: 'searxng', name: 'SearXNG', marke: 'searxng', vorlage: '' },
  { id: 'brave', name: 'Brave Search', marke: 'brave', vorlage: 'https://search.brave.com/search?q=%s' },
  { id: 'ecosia', name: 'Ecosia', marke: 'ecosia', vorlage: 'https://www.ecosia.org/search?q=%s' },
  {
    id: 'google',
    name: 'Google',
    marke: 'google',
    vorlage: 'https://www.google.com/search?q=%s',
    bild: { url: 'https://lens.google.com/v3/upload', feld: 'encoded_image' },
  },
  {
    id: 'bing',
    name: 'Bing',
    marke: 'bing',
    vorlage: 'https://www.bing.com/search?q=%s',
    bild: { url: 'https://www.bing.com/images/search?view=detailv2&iss=sbiupload', feld: 'imageBin', base64: true },
  },
]

export function suchmaschine(id: string): Suchmaschine {
  if (id === 'msm') return suchmaschine('searxng')
  return SUCHMASCHINEN.find((s) => s.id === id) ?? suchmaschine('duckduckgo')
}

/**
 * Welche Suche gilt. Ohne eigene Wahl (`null`) sucht ein gekoppelter Browser
 * über die SearXNG-Instanz des eigenen Servers, sonst DuckDuckGo.
 * Wurde vor dem Koppeln eine eigene Suchmaschine gewählt, bleibt diese erhalten.
 */
export function wirksameSuche(gewaehlt: string | null, gekoppelt: boolean): SuchmaschinenId {
  const normiert = gewaehlt === 'msm' ? 'searxng' : gewaehlt
  const bekannt = SUCHMASCHINEN.find((s) => s.id === normiert)?.id ?? null
  if (bekannt !== null) return bekannt
  return gekoppelt ? 'searxng' : 'duckduckgo'
}

const SCHEMA = /^[a-z][a-z0-9+.-]*:/i
const LOKAL = /^(localhost|\d{1,3}(\.\d{1,3}){3}|\[[0-9a-f:]+\])(:\d{1,5})?([/?#].*)?$/i
const DOMAIN = /^([a-z0-9\u00a1-\uffff-]+\.)+[a-z\u00a1-\uffff]{2,}(:\d{1,5})?([/?#].*)?$/i

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

/**
 * Die Suchadresse für einen Begriff, oder `null`, wenn die Maschine nicht eingerichtet ist.
 * Für SearXNG: eigene URL vorrangig; ohne eigene URL im gekoppelten Zustand der Server-Sidecar.
 */
export function suchAdresse(
  begriff: string,
  id: string,
  searxngUrl?: string | null,
  gekoppelt?: boolean,
): string | null {
  const text = begriff.trim()
  if (!text) return null
  const q = encodeURIComponent(text)
  if (id === 'msm' || id === 'searxng') {
    const basis = searxngBasis(searxngUrl)
    if (basis) return `${basis}/search?q=${q}`
    const verbunden = gekoppelt ?? istGekoppelt(useSitzung.getState().stand)
    if (verbunden || id === 'msm') return msmSucheAdresse(text)
    return null
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
 * bekommt `http://`: HTTPS versucht der Browser dann selbst und fällt zurück,
 * wenn die Seite es nicht kann (`tabs/https.rs`). Alles andere
 * ist ein Suchbegriff; ist SearXNG gewählt, aber keine Adresse hinterlegt und
 * das Gerät nicht gekoppelt, sucht DuckDuckGo als sicherer Rückfall.
 */
export function baueZielUrl(
  eingabe: string,
  id: string,
  searxngUrl?: string | null,
  gekoppelt?: boolean,
): string | null {
  const text = eingabe.trim()
  if (!text) return null
  if (istAdresse(text)) {
    if (/^(https?:\/\/|about:)/i.test(text)) return text
    return `http://${text}`
  }
  return suchAdresse(text, id, searxngUrl, gekoppelt) ?? suchAdresse(text, 'duckduckgo')
}
