import { useEffect } from 'react'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'

export function Shell() {
  const navigate = useNavigate()
  const location = useLocation()
  const { token, user, fetchUser } = useAuthStore()

  useEffect(() => {
    if (!token) {
      navigate('/login', { replace: true })
      return
    }
    if (!user) {
      fetchUser()
    }
  }, [token, user, navigate, fetchUser])

  if (!token) return null

  return (
    <div className="min-h-screen bg-background text-foreground flex">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <Topbar />
        <main className="flex-1 p-6 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
