import { create } from 'zustand'
import { api } from '@/api/client'
import type { User } from '@/types'

interface AuthState {
  token: string | null
  user: User | null
  isLoading: boolean
  setToken: (token: string) => void
  setUser: (user: User) => void
  logout: () => void
  fetchUser: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem('token'),
  user: null,
  isLoading: false,

  setToken: (token) => {
    localStorage.setItem('token', token)
    set({ token })
  },

  setUser: (user) => set({ user }),

  logout: () => {
    localStorage.removeItem('token')
    set({ token: null, user: null })
  },

  fetchUser: async () => {
    try {
      const user = await api<User>('/auth/me')
      set({ user })
    } catch {
      localStorage.removeItem('token')
      set({ token: null, user: null })
    }
  },
}))
