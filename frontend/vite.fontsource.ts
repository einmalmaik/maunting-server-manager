import type { Plugin } from 'vite'

/**
 * Wirft die `woff`-Rückfallebene aus den @fontsource-Stylesheets.
 *
 * Jede `@font-face`-Regel dort nennt zwei Dateien: `woff2` und dahinter
 * dasselbe Zeichen-Set noch einmal als `woff`. Vite sieht beide Verweise und
 * legt deshalb beide Dateien in den Build — 63 Dateien, die kein Browser je
 * abruft, der dieses Panel überhaupt starten kann. `woff2` versteht jede
 * Engine, die ES-Module, Service Worker und WebSockets versteht, und ohne die
 * kommt die Oberfläche keine Zeile weit.
 *
 * Der Eingriff ist absichtlich eng: greift der Ausdruck nicht mehr, weil
 * @fontsource seine Schreibweise ändert, bleibt das Stylesheet unverändert und
 * der Build enthält wieder beide Formate. Das ist Ballast, kein Defekt.
 *
 * `enforce: 'pre'` ist Pflicht — nach Vites CSS-Auflösung stünden statt der
 * Pfade bereits Asset-Kennungen da, und die Dateien wären längst eingeplant.
 *
 * Eigene Datei statt Kopie: Panel-Build (`vite.config.ts`) und Desktop-Build
 * (`vite.desktop.config.ts`) brauchen denselben Eingriff, und zwei Fassungen
 * veralteten gegeneinander.
 */
export function fontsourceWoff2Only(): Plugin {
  return {
    name: 'msm:fontsource-woff2-only',
    enforce: 'pre',
    transform(code, id) {
      if (!id.includes('@fontsource') || !id.endsWith('.css')) return null
      const stripped = code.replace(/,\s*url\([^)]+\.woff\)\s*format\(['"]woff['"]\)/g, '')
      return stripped === code ? null : { code: stripped, map: null }
    },
  }
}
