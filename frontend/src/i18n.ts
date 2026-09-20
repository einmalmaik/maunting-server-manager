import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import { localeResources } from './config/locales'
import { panelLanguageCodes } from './config/panelLocales'
import { getPersistedLocale, setPersistedLocale } from './utils/localePersistence'

const detector = new LanguageDetector()
detector.addDetector({
  name: 'customConsentDetector',
  lookup() {
    return getPersistedLocale() ?? undefined
  },
  cacheUserLanguage(lng) {
    setPersistedLocale(lng)
  },
})

i18n
  .use(detector)
  .use(initReactI18next)
  .init({
    resources: localeResources,
    fallbackLng: 'en',
    // Alles ausser DE/EN landet auf Englisch. i18next prüft eine erkannte
    // Sprache gegen diese Liste, bevor es sie übernimmt — ein Browser, der
    // `ar-SA` meldet, bekommt damit `fallbackLng` und nicht eine Sprache, für
    // die es keine Texte gibt.
    supportedLngs: panelLanguageCodes,
    // `de-AT`, `en-GB` und Verwandte fallen auf die Basissprache. Ohne das
    // wären sie „nicht unterstützt" und ein österreichischer Browser bekäme
    // Englisch statt Deutsch.
    load: 'languageOnly',
    interpolation: {
      escapeValue: false,
    },
    detection: {
      order: ['customConsentDetector', 'navigator', 'htmlTag'],
      caches: ['customConsentDetector'],
    },
    react: {
      useSuspense: false,
    },
    // Robustness: never return empty strings for missing keys
    returnEmptyString: false,
    // If a key is missing even in the fallback language, return the key itself
    // so the UI shows a human-readable indicator instead of a blank.
    //
    // `fallbackValue` MUST be honoured. i18next calls this handler even when a
    // `defaultValue` was resolved successfully — it passes the resolved text as
    // the second argument (i18next 23.16.8, translator.js: the guard is
    // `(usedKey || usedDefault)`, and the call site passes `usedDefault ? res :
    // undefined`). The previous one-argument version therefore discarded EVERY
    // `defaultValue` in the entire application.
    //
    // The visible consequence: an AI stream error rendered the raw key
    // `ai.errors.codes.AI_TOOL_REJECTED` in a toast, even though AiChat.tsx
    // supplies a two-step fallback. The operator saw a key where a sentence
    // belonged, and the sentence existed all along.
    parseMissingKeyHandler: (key: string, fallbackValue?: string) => fallbackValue ?? key,
  })

// Nur `lang`, nicht `dir`: beide Panelsprachen laufen von links nach rechts,
// und `dir` steht fest im HTML. Vorher schaltete Arabisch hier auf `rtl` — auf
// ein Layout mit 374 physischen Richtungsklassen (`ml-`, `pl-`, `left-`), das
// dabei auseinanderfiel. Käme RTL zurück, wäre das Umstellen dieser Klassen auf
// logische Eigenschaften der eigentliche Auftrag, nicht diese Zeile.
if (typeof document !== 'undefined') {
  i18n.on('languageChanged', (lng) => {
    document.documentElement.lang = lng
  })
}

export default i18n

