import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useAuthStore } from './authStore'
import { usePermissionsStore } from './permissionsStore'
import * as client from '@/api/client'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
  clearCsrfTokenMemory: vi.fn(),
}))

describe('authStore', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null, isAuthenticated: false, isLoading: true })
    usePermissionsStore.setState({ me: null, isLoading: false, error: null })
    vi.mocked(client.api).mockReset()
  })

  describe('initial state', () => {
    it('should not be authenticated initially', () => {
      const state = useAuthStore.getState()
      expect(state.isAuthenticated).toBe(false)
      expect(state.user).toBeNull()
      expect(state.isLoading).toBe(true)
    })

    it('should NOT read token from localStorage', () => {
      // Verify no localStorage access — store has no token field at all
      const state = useAuthStore.getState()
      expect(state).not.toHaveProperty('token')
    })
  })

  describe('checkAuth', () => {
    it('should authenticate on successful /auth/me', async () => {
      const mockUser = { id: 1, username: 'test', is_owner: true }
      vi.mocked(client.api)
        .mockResolvedValueOnce(mockUser)
        .mockResolvedValueOnce({
          is_owner: true,
          role_id: null,
          role_name: null,
          global_keys: [],
          server_keys: {},
        })

      const store = useAuthStore.getState()
      await store.checkAuth()

      expect(useAuthStore.getState().isAuthenticated).toBe(true)
      expect(useAuthStore.getState().user).toEqual(mockUser)
      expect(useAuthStore.getState().isLoading).toBe(false)
    })

    it('should set isAuthenticated=false on failed /auth/me', async () => {
      vi.mocked(client.api).mockRejectedValueOnce(new Error('Unauthorized'))

      const store = useAuthStore.getState()
      await store.checkAuth()

      expect(useAuthStore.getState().isAuthenticated).toBe(false)
      expect(useAuthStore.getState().user).toBeNull()
      expect(useAuthStore.getState().isLoading).toBe(false)
    })
  })

  describe('logout', () => {
    it('should call /auth/logout and clear state', async () => {
      vi.mocked(client.api).mockResolvedValueOnce({})

      const store = useAuthStore.getState()
      store.setUser({ id: 1, username: 'test', is_owner: true } as any)
      store.setAuthenticated(true)

      await store.logout()

      expect(client.api).toHaveBeenCalledWith('/auth/logout', { method: 'POST' })
      expect(useAuthStore.getState().isAuthenticated).toBe(false)
      expect(useAuthStore.getState().user).toBeNull()
    })

    it('should clear state even if /auth/logout fails', async () => {
      vi.mocked(client.api).mockRejectedValueOnce(new Error('Network error'))

      const store = useAuthStore.getState()
      store.setUser({ id: 1, username: 'test', is_owner: true } as any)
      store.setAuthenticated(true)

      await store.logout()

      expect(useAuthStore.getState().isAuthenticated).toBe(false)
      expect(useAuthStore.getState().user).toBeNull()
    })
  })

  describe('finishLogin', () => {
    it('sets auth state and loads permissions for route guards', async () => {
      const mockUser = { id: 1, username: 'test', is_owner: true }
      const mockPermissions = {
        is_owner: true,
        role_id: null,
        role_name: null,
        global_keys: [],
        server_keys: {},
      }
      vi.mocked(client.api).mockResolvedValueOnce(mockPermissions)

      await useAuthStore.getState().finishLogin(mockUser as any)

      expect(useAuthStore.getState().user).toEqual(mockUser)
      expect(useAuthStore.getState().isAuthenticated).toBe(true)
      expect(useAuthStore.getState().isLoading).toBe(false)
      expect(usePermissionsStore.getState().me).toEqual(mockPermissions)
      expect(client.api).toHaveBeenCalledWith('/permissions/me')
    })
  })

  describe('setAuthenticated', () => {
    it('should toggle authentication state', () => {
      const store = useAuthStore.getState()
      store.setAuthenticated(true)
      expect(useAuthStore.getState().isAuthenticated).toBe(true)

      store.setAuthenticated(false)
      expect(useAuthStore.getState().isAuthenticated).toBe(false)
    })
  })

  describe('security invariant: no token in state', () => {
    it('should never expose token in store', () => {
      const state = useAuthStore.getState()
      const keys = Object.keys(state)
      expect(keys).not.toContain('token')
      expect(keys).not.toContain('accessToken')
      expect(keys).not.toContain('refreshToken')
    })
  })
})
