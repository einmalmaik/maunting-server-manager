/**
 * Seiten des Browsers selbst (`msb://einstellungen/suche`). Sie haben keine
 * Webview, die Oberfläche zeichnet sie wie die Startseite. Rust lädt nur
 * http(s) (`ziel_pruefen`), eine Seite kommt also nie an sie heran.
 */

const PRAEFIX = 'msb://'

export const INTERNE_SEITEN = ['einstellungen'] as const
export type InterneSeite = (typeof INTERNE_SEITEN)[number]

export function istIntern(url: string): boolean {
  return url.startsWith(PRAEFIX)
}

/** Die Seite und ihr Unterpfad, oder `null`, wenn es keine eigene Seite ist. */
export function interneSeite(url: string): { seite: InterneSeite; teil: string | null } | null {
  if (!istIntern(url)) return null
  const [seite, teil] = url.slice(PRAEFIX.length).toLowerCase().split('/')
  if (!(INTERNE_SEITEN as readonly string[]).includes(seite)) return null
  return { seite: seite as InterneSeite, teil: teil || null }
}

export function interneAdresse(seite: InterneSeite, teil?: string | null): string {
  return `${PRAEFIX}${seite}${teil ? `/${teil}` : ''}`
}
