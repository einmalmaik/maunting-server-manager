/**
 * Seiten des Browsers selbst (`pontis://einstellungen/suche`). Sie haben keine
 * Webview, die Oberfläche zeichnet sie wie die Startseite. Rust lädt nur
 * http(s) (`ziel_pruefen`), eine Seite kommt also nie an sie heran.
 *
 * `msb://` war der alte Name. Er wird noch gelesen (bestehende Tabs, Links),
 * aber nie mehr geschrieben; `msb:*`-Speicherkeys sind davon unberührt.
 */

const PRAEFIX = 'pontis://'
const PRAEFIX_ALT = 'msb://'

export const INTERNE_SEITEN = ['einstellungen', 'suche'] as const
export type InterneSeite = (typeof INTERNE_SEITEN)[number]

/** Eine Seite aus dem Netz (http oder https), keine eigene und keine leere. */
export function istWebseite(url: string): boolean {
  return /^https?:\/\//i.test(url)
}

export function istIntern(url: string): boolean {
  return url.startsWith(PRAEFIX) || url.startsWith(PRAEFIX_ALT)
}

/** Alte `msb://`-Adresse auf `pontis://` heben; alles andere bleibt. */
export function aufPontis(url: string): string {
  return url.startsWith(PRAEFIX_ALT) ? `${PRAEFIX}${url.slice(PRAEFIX_ALT.length)}` : url
}

/** Die Seite und ihr Unterpfad, oder `null`, wenn es keine eigene Seite ist. */
export function interneSeite(url: string): { seite: InterneSeite; teil: string | null } | null {
  if (!istIntern(url)) return null
  const praefix = url.startsWith(PRAEFIX) ? PRAEFIX : PRAEFIX_ALT
  const [seite, teil] = url.slice(praefix.length).split(/[?#]/)[0].toLowerCase().split('/')
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
