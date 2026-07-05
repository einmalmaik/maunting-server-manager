import { useEffect, useState } from 'react'
import { getPublicLegalSettings, type PublicLegalSettings } from '@/api/legal'

const DEFAULT_LEGAL_SETTINGS: PublicLegalSettings = {
  imprint_enabled: false,
  imprint_url: '',
}

let cachedLegalSettings: PublicLegalSettings | null = null
const listeners = new Set<(settings: PublicLegalSettings) => void>()

export function publishPublicLegalSettings(settings: PublicLegalSettings) {
  cachedLegalSettings = settings
  listeners.forEach((listener) => listener(settings))
}

export function usePublicLegalSettings(): PublicLegalSettings {
  const [settings, setSettings] = useState<PublicLegalSettings>(
    cachedLegalSettings ?? DEFAULT_LEGAL_SETTINGS,
  )

  useEffect(() => {
    let cancelled = false
    listeners.add(setSettings)
    getPublicLegalSettings()
      .then((next) => {
        publishPublicLegalSettings(next)
      })
      .catch(() => {
        if (!cancelled) setSettings(DEFAULT_LEGAL_SETTINGS)
      })
    return () => {
      cancelled = true
      listeners.delete(setSettings)
    }
  }, [])

  return settings
}
