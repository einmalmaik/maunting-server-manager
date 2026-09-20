import type { ResourceLanguage } from 'i18next'

import de from '../locales/de.json'
import en from '../locales/en.json'
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
 */
export const localeResources: Record<PanelLanguageCode, ResourceLanguage> = {
  de: { translation: de },
  en: { translation: en },
}
