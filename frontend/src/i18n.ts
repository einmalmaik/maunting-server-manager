import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import { localeResources, supportedLocales } from './config/locales'
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

const supportedCodes = supportedLocales.map((l) => l.code)

i18n
  .use(detector)
  .use(initReactI18next)
  .init({
    resources: localeResources,
    fallbackLng: 'en',
    supportedLngs: supportedCodes,
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

if (typeof document !== 'undefined') {
  i18n.on('languageChanged', (lng) => {
    const meta = supportedLocales.find((l) => l.code === lng)
    const dir = meta?.direction || 'ltr'
    document.documentElement.dir = dir
    document.documentElement.lang = lng
  })
}

export default i18n

