import {
  createContext,
  useCallback,
  useContext,
  useMemo,
} from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, authApi } from '@/lib/api'
import { getDefaultRoute } from '@/lib/permissions'
import type { User } from '@/lib/types'

interface AuthContextValue {
  user: User | null
  isLoading: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)

/** @internal Only call this inside AuthProvider (must be inside a Router). Use useAuth() everywhere else. */
export function useAuthState(): AuthContextValue {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: authApi.me,
    retry: false,
    staleTime: 5 * 60 * 1000,
  })

  const login = useCallback(
    async (username: string, password: string) => {
      const response = await authApi.login(username, password)
      if (!('user' in response)) {
        return
      }
      await queryClient.invalidateQueries({ queryKey: ['auth', 'me'] })
      navigate(getDefaultRoute(response.user), { replace: true })
    },
    [navigate, queryClient],
  )

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } catch {
      // ignore errors on logout
    }
    queryClient.clear()
    navigate('/login', { replace: true })
  }, [navigate, queryClient])

  return useMemo(
    () => ({
      user: data?.user ?? null,
      isLoading,
      login,
      logout,
    }),
    [data?.user, isLoading, login, logout],
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}

export { ApiError }
