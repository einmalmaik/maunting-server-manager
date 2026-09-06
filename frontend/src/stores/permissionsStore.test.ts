import { describe, it, expect, vi, beforeEach } from 'vitest'
import { usePermissionsStore } from './permissionsStore'
import { rbacApi } from '@/api/rbac'

vi.mock('@/api/rbac', () => ({
  rbacApi: {
    me: vi.fn(),
  },
}))

describe('permissionsStore', () => {
  beforeEach(() => {
    localStorage.clear()
    usePermissionsStore.setState({ me: null, isLoading: false, error: null })
    vi.mocked(rbacApi.me).mockReset()
  })

  it('lädt permissions und speichert sie im localStorage cache', async () => {
    const mockMe = {
      is_owner: false,
      role_id: 1,
      role_name: 'Nutzer',
      global_keys: ['ai.calendar.use', 'ai.notes.use'],
      server_keys: {},
    }
    vi.mocked(rbacApi.me).mockResolvedValueOnce(mockMe)

    await usePermissionsStore.getState().refresh()

    expect(usePermissionsStore.getState().me).toEqual(mockMe)
    expect(localStorage.getItem('msm_cached_permissions')).toBe(JSON.stringify(mockMe))
  })

  it('behält gecachte permissions bei Netzwerk-/Offline-Fehlern', async () => {
    const mockMe = {
      is_owner: false,
      role_id: 1,
      role_name: 'Nutzer',
      global_keys: ['ai.calendar.use', 'ai.notes.use'],
      server_keys: {},
    }
    localStorage.setItem('msm_cached_permissions', JSON.stringify(mockMe))
    usePermissionsStore.setState({ me: mockMe })

    vi.mocked(rbacApi.me).mockRejectedValueOnce(new TypeError('Failed to fetch (offline)'))

    await usePermissionsStore.getState().refresh()

    expect(usePermissionsStore.getState().me).toEqual(mockMe)
    expect(usePermissionsStore.getState().error).toBeNull()
  })

  it('leert die gecachten permissions bei reset()', () => {
    const mockMe = {
      is_owner: false,
      role_id: 1,
      role_name: 'Nutzer',
      global_keys: ['ai.calendar.use', 'ai.notes.use'],
      server_keys: {},
    }
    localStorage.setItem('msm_cached_permissions', JSON.stringify(mockMe))
    usePermissionsStore.setState({ me: mockMe })

    usePermissionsStore.getState().reset()

    expect(usePermissionsStore.getState().me).toBeNull()
    expect(localStorage.getItem('msm_cached_permissions')).toBeNull()
  })

  it('Negativtest: beschädigter/manipulierter Permissions-Cache führt nicht zum Crash bei Offline-Ausfall', async () => {
    localStorage.setItem('msm_cached_permissions', '{"invalid": true, missing_keys')
    usePermissionsStore.setState({ me: null })

    vi.mocked(rbacApi.me).mockRejectedValueOnce(new TypeError('Failed to fetch'))

    await usePermissionsStore.getState().refresh()

    expect(usePermissionsStore.getState().me).toBeNull()
    expect(usePermissionsStore.getState().isLoading).toBe(false)
  })

  it('Negativtest: ungültige Datentypen im Cache (kein Array für global_keys) werden verworfen', async () => {
    const payloads = [
      '{"global_keys": "ADMIN"}',
      '{"global_keys": 123}',
      '{"global_keys": null}',
      '{"global_keys": {}}',
      '"string_instead_of_object"',
      '12345',
    ]

    for (const bad of payloads) {
      localStorage.setItem('msm_cached_permissions', bad)
      usePermissionsStore.setState({ me: null })

      vi.mocked(rbacApi.me).mockRejectedValueOnce(new TypeError('Failed to fetch (Offline)'))
      await usePermissionsStore.getState().refresh()

      expect(usePermissionsStore.getState().me).toBeNull()
    }
  })

  it('Negativtest: LocalStorage-Ausnahme bringt permissionsStore nicht zum Absturz', async () => {
    const originalGetItem = localStorage.getItem
    try {
      localStorage.getItem = vi.fn(() => {
        throw new DOMException('The operation is insecure.', 'SecurityError')
      })
      usePermissionsStore.setState({ me: null })
      vi.mocked(rbacApi.me).mockRejectedValueOnce(new TypeError('Failed to fetch'))

      await expect(usePermissionsStore.getState().refresh()).resolves.not.toThrow()
      expect(usePermissionsStore.getState().me).toBeNull()
    } finally {
      localStorage.getItem = originalGetItem
    }
  })
})
