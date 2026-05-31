import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import { localeResources, supportedLocales } from './config/locales'
import { getPersistedLocale, setPersistedLocale } from './utils/localePersistence'

const detector = new LanguageDetector()
detector.addDetector({
  name: 'customConsentDetector',
  lookup() {
    return getPersistedLocale()
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
    parseMissingKeyHandler: (key: string) => key,
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

