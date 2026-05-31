export interface LocaleMetadata {
  code: string
  label: string
  nativeLabel: string
  direction: 'ltr' | 'rtl'
  fallback: string
}

const METADATA_MAPPING: Record<string, Omit<LocaleMetadata, 'code'>> = {
  de: { label: 'German', nativeLabel: 'Deutsch', direction: 'ltr', fallback: 'en' },
  en: { label: 'English', nativeLabel: 'English', direction: 'ltr', fallback: 'en' },
}

// Auto-detect files in locales directory
const localeFiles = import.meta.glob('../locales/*.json', { eager: true }) as Record<string, { default: any }>

export const supportedLocales: LocaleMetadata[] = Object.keys(localeFiles).map((path) => {
  const code = path.split('/').pop()?.replace('.json', '') || ''
  const meta = METADATA_MAPPING[code] || {
    label: code.toUpperCase(),
    nativeLabel: code.toUpperCase(),
    direction: 'ltr',
    fallback: 'en',
  }
  return {
    code,
    ...meta,
  }
})

export const localeResources = Object.keys(localeFiles).reduce((acc, path) => {
  const code = path.split('/').pop()?.replace('.json', '') || ''
  acc[code] = { translation: localeFiles[path].default }
  return acc;
}, {} as Record<string, { translation: any }>)
