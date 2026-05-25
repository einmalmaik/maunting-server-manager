import { create } from 'zustand'
import { api } from '@/api/client'
import { usePermissionsStore } from '@/stores/permissionsStore'
import type { User } from '@/types'

interface AuthState {
  user: User | null
  isLoading: boolean
  isAuthenticated: boolean
  setUser: (user: User | null) => void
  setAuthenticated: (val: boolean) => void
  updateUser: (patch: Partial<User>) => void
  logout: () => Promise<void>
  checkAuth: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isLoading: true,
  isAuthenticated: false,

  setUser: (user) => set({ user }),

  setAuthenticated: (val) => set({ isAuthenticated: val }),

  updateUser: (patch) => set((state) => ({
    user: state.user ? { ...state.user, ...patch } : null,
  })),

  logout: async () => {
    try {
      await api('/auth/logout', { method: 'POST' })
    } catch {
      // Ignorieren: Backend hat Cookies geloescht, Client-State wird hier bereinigt
    }
    usePermissionsStore.getState().reset()
    set({ user: null, isAuthenticated: false, isLoading: false })
  },

  checkAuth: async () => {
    set({ isLoading: true })
    try {
      const user = await api<User>('/auth/me')
      set({ user, isAuthenticated: true, isLoading: false })
      // Permissions parallel laden — Frontend-Permission-Checks wissen damit Bescheid.
      void usePermissionsStore.getState().refresh()
    } catch {
      usePermissionsStore.getState().reset()
      set({ user: null, isAuthenticated: false, isLoading: false })
    }
  },
}))
