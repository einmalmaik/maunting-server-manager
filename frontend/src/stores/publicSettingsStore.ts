import { create } from 'zustand'
import { api } from '@/api/client'

export interface PublicSettings {
  desktop_app_download_enabled: boolean
  story_fable_download_enabled: boolean
  imprint_enabled: boolean
  imprint_url: string
  calendar_enabled: boolean
  notes_enabled: boolean
  vault_enabled: boolean
  social_enabled: boolean
}

export const DEFAULT_PUBLIC_SETTINGS: PublicSettings = {
  desktop_app_download_enabled: true,
  story_fable_download_enabled: false,
  imprint_enabled: false,
  imprint_url: '',
  calendar_enabled: true,
  notes_enabled: true,
  vault_enabled: true,
  social_enabled: true,
}

const CACHED_SETTINGS_KEY = 'msm_cached_public_settings'

function loadCachedSettings(): PublicSettings {
  try {
    if (typeof localStorage === 'undefined') return DEFAULT_PUBLIC_SETTINGS
    const raw = localStorage.getItem(CACHED_SETTINGS_KEY)
    if (!raw) return DEFAULT_PUBLIC_SETTINGS
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object') {
      return {
        desktop_app_download_enabled:
          typeof parsed.desktop_app_download_enabled === 'boolean'
            ? parsed.desktop_app_download_enabled
            : DEFAULT_PUBLIC_SETTINGS.desktop_app_download_enabled,
        story_fable_download_enabled:
          typeof parsed.story_fable_download_enabled === 'boolean'
            ? parsed.story_fable_download_enabled
            : DEFAULT_PUBLIC_SETTINGS.story_fable_download_enabled,
        imprint_enabled:
          typeof parsed.imprint_enabled === 'boolean'
            ? parsed.imprint_enabled
            : DEFAULT_PUBLIC_SETTINGS.imprint_enabled,
        imprint_url:
          typeof parsed.imprint_url === 'string'
            ? parsed.imprint_url
            : DEFAULT_PUBLIC_SETTINGS.imprint_url,
        calendar_enabled:
          typeof parsed.calendar_enabled === 'boolean'
            ? parsed.calendar_enabled
            : DEFAULT_PUBLIC_SETTINGS.calendar_enabled,
        notes_enabled:
          typeof parsed.notes_enabled === 'boolean'
            ? parsed.notes_enabled
            : DEFAULT_PUBLIC_SETTINGS.notes_enabled,
        vault_enabled:
          typeof parsed.vault_enabled === 'boolean'
            ? parsed.vault_enabled
            : DEFAULT_PUBLIC_SETTINGS.vault_enabled,
        social_enabled:
          typeof parsed.social_enabled === 'boolean'
            ? parsed.social_enabled
            : DEFAULT_PUBLIC_SETTINGS.social_enabled,
      }
    }
    return DEFAULT_PUBLIC_SETTINGS
  } catch {
    return DEFAULT_PUBLIC_SETTINGS
  }
}

function saveCachedSettings(settings: PublicSettings): void {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(CACHED_SETTINGS_KEY, JSON.stringify(settings))
    }
  } catch {}
}

interface PublicSettingsState extends PublicSettings {
  isLoading: boolean
  error: string | null
  refresh: () => Promise<PublicSettings>
  setSettings: (partial: Partial<PublicSettings>) => void
  initFromCache: () => void
}

let letzteAbfrage = 0

export const usePublicSettingsStore = create<PublicSettingsState>((set, get) => {
  const initial = loadCachedSettings()

  return {
    ...initial,
    isLoading: false,
    error: null,

    refresh: async () => {
      const abfrage = ++letzteAbfrage
      set({ isLoading: true, error: null })
      try {
        const remote = await api<Partial<PublicSettings>>('/settings/public')
        if (abfrage !== letzteAbfrage) return get()
        const aktualisiert: PublicSettings = {
          desktop_app_download_enabled:
            typeof remote.desktop_app_download_enabled === 'boolean'
              ? remote.desktop_app_download_enabled
              : get().desktop_app_download_enabled,
          story_fable_download_enabled:
            typeof remote.story_fable_download_enabled === 'boolean'
              ? remote.story_fable_download_enabled
              : get().story_fable_download_enabled,
          imprint_enabled:
            typeof remote.imprint_enabled === 'boolean'
              ? remote.imprint_enabled
              : get().imprint_enabled,
          imprint_url:
            typeof remote.imprint_url === 'string'
              ? remote.imprint_url
              : get().imprint_url,
          calendar_enabled:
            typeof remote.calendar_enabled === 'boolean'
              ? remote.calendar_enabled
              : get().calendar_enabled,
          notes_enabled:
            typeof remote.notes_enabled === 'boolean'
              ? remote.notes_enabled
              : get().notes_enabled,
          vault_enabled:
            typeof remote.vault_enabled === 'boolean'
              ? remote.vault_enabled
              : get().vault_enabled,
          social_enabled:
            typeof remote.social_enabled === 'boolean'
              ? remote.social_enabled
              : get().social_enabled,
        }
        saveCachedSettings(aktualisiert)
        set({ ...aktualisiert, isLoading: false, error: null })
        return aktualisiert
      } catch (err: unknown) {
        if (abfrage !== letzteAbfrage) return get()
        // Bei Netzwerk- oder Offline-Fehlern: Behalte gecachte Einstellungen unverändert bei.
        set({
          isLoading: false,
          error: err instanceof Error ? err.message : 'Fehler beim Laden der Einstellungen',
        })
        return get()
      }
    },

    setSettings: (partial: Partial<PublicSettings>) => {
      const updated: PublicSettings = {
        desktop_app_download_enabled:
          partial.desktop_app_download_enabled ?? get().desktop_app_download_enabled,
        story_fable_download_enabled:
          partial.story_fable_download_enabled ?? get().story_fable_download_enabled,
        imprint_enabled: partial.imprint_enabled ?? get().imprint_enabled,
        imprint_url: partial.imprint_url ?? get().imprint_url,
        calendar_enabled: partial.calendar_enabled ?? get().calendar_enabled,
        notes_enabled: partial.notes_enabled ?? get().notes_enabled,
        vault_enabled: partial.vault_enabled ?? get().vault_enabled,
        social_enabled: partial.social_enabled ?? get().social_enabled,
      }
      saveCachedSettings(updated)
      set({ ...updated })
    },

    initFromCache: () => {
      const cached = loadCachedSettings()
      set({ ...cached })
    },
  }
})
