import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  usePublicSettingsStore,
  DEFAULT_PUBLIC_SETTINGS,
} from './publicSettingsStore'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

import { api } from '@/api/client'

describe('publicSettingsStore', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    usePublicSettingsStore.setState({
      ...DEFAULT_PUBLIC_SETTINGS,
      isLoading: false,
      error: null,
    })
  })

  it('verwendet standardmäßig DEFAULT_PUBLIC_SETTINGS bei leerem Cache', () => {
    const state = usePublicSettingsStore.getState()
    expect(state.calendar_enabled).toBe(true)
    expect(state.notes_enabled).toBe(true)
    expect(state.vault_enabled).toBe(true)
    expect(state.social_enabled).toBe(true)
  })

  it('lädt zuvor gespeicherte Einstellungen aus dem localStorage', () => {
    localStorage.setItem(
      'msm_cached_public_settings',
      JSON.stringify({
        calendar_enabled: false,
        notes_enabled: false,
        vault_enabled: false,
        social_enabled: false,
      }),
    )

    usePublicSettingsStore.getState().setSettings({
      calendar_enabled: false,
      notes_enabled: false,
      vault_enabled: false,
      social_enabled: false,
    })

    const state = usePublicSettingsStore.getState()
    expect(state.calendar_enabled).toBe(false)
    expect(state.notes_enabled).toBe(false)
    expect(state.vault_enabled).toBe(false)
    expect(state.social_enabled).toBe(false)
  })

  it('schreibt bei erfolgreichem refresh in den localStorage und aktualisiert State', async () => {
    vi.mocked(api).mockResolvedValueOnce({
      calendar_enabled: false,
      notes_enabled: true,
      vault_enabled: false,
      social_enabled: false,
    })

    await usePublicSettingsStore.getState().refresh()

    const state = usePublicSettingsStore.getState()
    expect(state.calendar_enabled).toBe(false)
    expect(state.notes_enabled).toBe(true)
    expect(state.vault_enabled).toBe(false)
    expect(state.social_enabled).toBe(false)

    const cached = JSON.parse(
      localStorage.getItem('msm_cached_public_settings') || '{}',
    )
    expect(cached.calendar_enabled).toBe(false)
    expect(cached.vault_enabled).toBe(false)
    expect(cached.social_enabled).toBe(false)
  })

  it('behält bei Netzwerkfehlern im Offline-Modus die gecachten Werte bei', async () => {
    usePublicSettingsStore.setState({
      ...DEFAULT_PUBLIC_SETTINGS,
      vault_enabled: false,
      social_enabled: false,
    })
    localStorage.setItem(
      'msm_cached_public_settings',
      JSON.stringify({
        ...DEFAULT_PUBLIC_SETTINGS,
        vault_enabled: false,
        social_enabled: false,
      }),
    )

    vi.mocked(api).mockRejectedValueOnce(new TypeError('Failed to fetch (Offline)'))

    await usePublicSettingsStore.getState().refresh()

    const state = usePublicSettingsStore.getState()
    expect(state.vault_enabled).toBe(false)
    expect(state.social_enabled).toBe(false)
    expect(state.error).toBe('Failed to fetch (Offline)')
  })

  it('initFromCache aktualisiert den State synchron mit dem localStorage', () => {
    localStorage.setItem(
      'msm_cached_public_settings',
      JSON.stringify({
        ...DEFAULT_PUBLIC_SETTINGS,
        vault_enabled: false,
        notes_enabled: false,
      }),
    )

    usePublicSettingsStore.getState().initFromCache()

    const state = usePublicSettingsStore.getState()
    expect(state.vault_enabled).toBe(false)
    expect(state.notes_enabled).toBe(false)
    expect(state.calendar_enabled).toBe(true)
  })
})

