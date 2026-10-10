/**
 * Der Host einer Adresse ohne `www.`, klein; leer, wenn es keine ist. Eine
 * Datei ohne Abhängigkeiten: der Browser vergleicht damit, ohne den Tresor zu laden.
 */
export function hostVon(url: string | undefined): string {
  return adresse(url)?.hostname.toLowerCase().replace(/^www\./, '') ?? ''
}

/**
 * Der Port, leer für den üblichen des Schemas (443, 80). Unter einem anderen
 * Port läuft oft ein anderer Dienst desselben Hosts, etwa ein Testsystem oder
 * eine Seite eines anderen Nutzers; er ist eine eigene Herkunft.
 */
export function portVon(url: string | undefined): string {
  return adresse(url)?.port ?? ''
}

function adresse(url: string | undefined): URL | null {
  if (!url) return null
  try {
    // Ein Schema nur mit `//` dahinter: in `beispiel.de:8443` ist `beispiel.de` der Host.
    return new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(url) ? url : `https://${url}`)
  } catch {
    return null
  }
}
