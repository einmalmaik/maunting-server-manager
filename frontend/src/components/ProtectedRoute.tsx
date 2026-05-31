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
    return <Loader fullScreen label="Maunting Server Manager" />
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return <>{children}</>
}
