import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import de from './locales/de.json'
import en from './locales/en.json'

const resources = {
  de: { translation: de },
  en: { translation: en },
} as const

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'en',
    supportedLngs: ['en', 'de'],
    interpolation: {
      escapeValue: false,
    },
    detection: {
      order: ['localStorage', 'navigator', 'htmlTag'],
      caches: ['localStorage'],
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

export default i18n
