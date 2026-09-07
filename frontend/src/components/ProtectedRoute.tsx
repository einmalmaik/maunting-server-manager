import { useEffect } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'
import { Loader } from '@/components/ui/Loader'

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading, checkAuth } = useAuthStore()
  const location = useLocation()

  useEffect(() => {
    if (!isAuthenticated && isLoading) {
      checkAuth()
    }
  }, [isAuthenticated, isLoading, checkAuth])

  if (isLoading) {
    return <Loader fullScreen label="Maunting Service Manager" />
  }

  if (!isAuthenticated) {
    const target = location.pathname + location.search
    return (
      <Navigate
        to={`/login?redirect=${encodeURIComponent(target)}`}
        replace
        state={{ from: target }}
      />
    )
  }

  return <>{children}</>
}
