/**
 * Der Host einer Adresse ohne `www.`, klein; leer, wenn es keine ist. Eine
 * Datei ohne Abhängigkeiten: der Browser vergleicht damit, ohne den Tresor zu laden.
 */
export function hostVon(url: string | undefined): string {
  if (!url) return ''
  try {
    return new URL(/^[a-z][a-z0-9+.-]*:/i.test(url) ? url : `https://${url}`).hostname.toLowerCase().replace(/^www\./, '')
  } catch {
    return ''
  }
}
