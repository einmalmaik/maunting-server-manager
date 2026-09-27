import type { BackendModule, ResourceKey } from 'i18next'

import type { PanelLanguageCode } from './panelLocales'

/**
 * Die Textbündel der beiden Panelsprachen — mehr gibt es nicht.
 *
 * Vorher stand hier ein `import.meta.glob('../locales/*.json')`. Das war der
 * Grund, warum neun halbfertige Sprachdateien (772 von 4859 Schlüsseln) zu
 * aktiven Sprachen wurden: wer eine Datei in den Ordner legte, schaltete sie
 * damit frei. Die Spracherkennung des Browsers griff darauf zu, der Umschalter
 * in den Einstellungen kannte sie nie — ein französischer Besucher sah eine zu
 * 84 % englische Oberfläche und daneben „EN" als aktiv markiert.
 *
 * Jetzt sind es zwei benannte Importe. Eine neue Sprache kostet damit eine
 * Zeile hier und eine in `panelLocales.ts` — und genau dort fällt auf, dass
 * eine Sprache auch jemanden braucht, der sie übersetzt.
 *
 * Geladen wird nur die Sprache, die angezeigt wird. Beide zusammen sind über
 * 700 kB und lagen bis 27.09.2026 in jedem ersten Seitenaufruf.
 */
const textbuendel: Record<PanelLanguageCode, () => Promise<{ default: ResourceKey }>> = {
  de: () => import('../locales/de.json'),
  en: () => import('../locales/en.json'),
}

export const localeBackend: BackendModule = {
  type: 'backend',
  init() {},
  read(language, _namespace, callback) {
    const laden = textbuendel[language as PanelLanguageCode]
    if (!laden) {
      callback(null, {})
      return
    }
    laden().then(
      (modul) => callback(null, modul.default),
      (fehler: unknown) => callback(fehler instanceof Error ? fehler : new Error(String(fehler)), null),
    )
  },
}
