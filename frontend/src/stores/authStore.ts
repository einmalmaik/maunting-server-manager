import { create } from 'zustand'
import { api, clearCsrfTokenMemory } from '@/api/client'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { clearSqlConsoleHistory } from '@/lib/sqlConsoleStorage'
import type { User } from '@/types'

interface AuthState {
  user: User | null
  isLoading: boolean
  isAuthenticated: boolean
  setUser: (user: User | null) => void
  setAuthenticated: (val: boolean) => void
  finishLogin: (user: User) => Promise<void>
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

  finishLogin: async (user) => {
    set({ user, isAuthenticated: true, isLoading: false })
    await usePermissionsStore.getState().refresh()
  },

  updateUser: (patch) => set((state) => ({
    user: state.user ? { ...state.user, ...patch } : null,
  })),

  logout: async () => {
    try {
      await api('/auth/logout', { method: 'POST' })
    } catch {
      // Ignorieren: Backend hat Cookies geloescht, Client-State wird hier bereinigt
    }
    clearCsrfTokenMemory()
    // Der Abfrageverlauf der SQL-Konsole liegt im localStorage und überlebt das
    // Abmelden. Auf einem geteilten Rechner läge er sonst im Browser des
    // nächsten Benutzers — deshalb fällt er hier zusammen mit dem CSRF-Speicher.
    clearSqlConsoleHistory()
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
      clearCsrfTokenMemory()
      usePermissionsStore.getState().reset()
      set({ user: null, isAuthenticated: false, isLoading: false })
    }
  },
}))
