import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import { DEFAULT_VERSION, getCachedVersion, getVersion } from './versionService'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

describe('versionService', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  afterEach(() => {
    localStorage.clear()
  })

  it('fragt das Backend unter /system/version an und puffert den Wert', async () => {
    vi.mocked(api).mockResolvedValueOnce({
      current_version: 'v4.4.2',
      latest_version: 'v4.4.2',
      update_available: false,
    })

    const version = await getVersion()
    expect(version).toBe('v4.4.2')
    expect(api).toHaveBeenCalledTimes(1)
    expect(api).toHaveBeenCalledWith('/system/version')

    // Zweiter Aufruf nutzt den Cache ohne erneute Backend-Anfrage
    const cached = await getVersion()
    expect(cached).toBe('v4.4.2')
    expect(api).toHaveBeenCalledTimes(1)
  })

  it('liefert synchron den gespeicherten Cache-Wert', () => {
    localStorage.setItem(
      'msm_version_cache',
      JSON.stringify({ version: 'v4.4.2', fetchedAt: Date.now() }),
    )

    expect(getCachedVersion()).toBe('v4.4.2')
  })

  it('greift bei Backend-Fehler auf vorhandenen Cache zurück', async () => {
    localStorage.setItem(
      'msm_version_cache',
      JSON.stringify({ version: 'v4.3.0', fetchedAt: Date.now() - 100_000_000 }),
    )

    vi.mocked(api).mockRejectedValueOnce(new Error('Network error'))

    const version = await getVersion()
    expect(version).toBe('v4.3.0')
  })

  it('fällt auf DEFAULT_VERSION zurück, wenn Backend und Cache leer sind', async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error('Backend offline'))

    const version = await getVersion()
    expect(version).toBe(DEFAULT_VERSION)
  })

  it('kontaktiert niemals externe Hosts wie api.github.com', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    vi.mocked(api).mockResolvedValueOnce({
      current_version: 'v4.4.2',
    })

    await getVersion()

    // Kein direkter fetch an api.github.com
    for (const call of fetchSpy.mock.calls) {
      const url = String(call[0])
      expect(url).not.toContain('github.com')
    }
  })
})
