import { useEffect } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'
import { Loader } from '@/components/ui/Loader'

export function PublicOnlyRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading, checkAuth } = useAuthStore()

  useEffect(() => {
    if (!isAuthenticated && isLoading) {
      checkAuth()
    }
  }, [isAuthenticated, isLoading, checkAuth])

  if (isLoading) {
    return <Loader fullScreen label="Maunting Server Manager" />
  }

  if (isAuthenticated) {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}
