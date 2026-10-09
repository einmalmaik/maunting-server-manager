/**
 * Seiten des Browsers selbst (`msb://einstellungen/suche`). Sie haben keine
 * Webview, die Oberfläche zeichnet sie wie die Startseite. Rust lädt nur
 * http(s) (`ziel_pruefen`), eine Seite kommt also nie an sie heran.
 */

const PRAEFIX = 'msb://'

export const INTERNE_SEITEN = ['einstellungen', 'suche'] as const
export type InterneSeite = (typeof INTERNE_SEITEN)[number]

/** Eine Seite aus dem Netz (http oder https), keine eigene und keine leere. */
export function istWebseite(url: string): boolean {
  return /^https?:\/\//i.test(url)
}

export function istIntern(url: string): boolean {
  return url.startsWith(PRAEFIX)
}

/** Die Seite und ihr Unterpfad, oder `null`, wenn es keine eigene Seite ist. */
export function interneSeite(url: string): { seite: InterneSeite; teil: string | null } | null {
  if (!istIntern(url)) return null
  const [seite, teil] = url.slice(PRAEFIX.length).split(/[?#]/)[0].toLowerCase().split('/')
  if (!(INTERNE_SEITEN as readonly string[]).includes(seite)) return null
  return { seite: seite as InterneSeite, teil: teil || null }
}

export function interneAdresse(seite: InterneSeite, teil?: string | null): string {
  return `${PRAEFIX}${seite}${teil ? `/${teil}` : ''}`
}

/**
 * Die MSM-Suche (`seite/MsmSuche.tsx`): der Begriff steht in der Adresse, in
 * seiner Schreibweise. Er bleibt in der Oberfläche; an den eigenen Server geht
 * er erst, wenn die Seite die Treffer holt.
 */
export function msmSucheAdresse(begriff: string): string {
  return `${PRAEFIX}suche?q=${encodeURIComponent(begriff.trim())}`
}

/** Der Suchbegriff einer MSM-Suche, oder `null`, wenn es keine ist. */
export function msmSucheBegriff(url: string): string | null {
  const fragezeichen = url.indexOf('?')
  if (interneSeite(url)?.seite !== 'suche' || fragezeichen < 0) return null
  return new URLSearchParams(url.slice(fragezeichen + 1).split('#')[0]).get('q')?.trim() || null
}
