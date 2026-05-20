import type { ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { AuthContext, useAuthState } from '@/hooks/useAuth'
import AppLayout from '@/components/layout/AppLayout'
import ProtectedRoute from '@/components/layout/ProtectedRoute'
import LoginPage from '@/pages/LoginPage'
import RegisterPage from '@/pages/RegisterPage'
import ForgotPasswordPage from '@/pages/ForgotPasswordPage'
import ResetPasswordPage from '@/pages/ResetPasswordPage'
import SetupPage from '@/pages/SetupPage'
import DashboardPage from '@/pages/DashboardPage'
import BackupsPage from '@/pages/BackupsPage'
import AutorestartPage from '@/pages/AutorestartPage'
import ConsolePage from '@/pages/ConsolePage'
import ModsPage from '@/pages/ModsPage'
import FileManagerPage from '@/pages/FileManagerPage'
import ConfigCenterPage from '@/pages/ConfigCenterPage'
import ServersPage from '@/pages/ServersPage'
import UsersPage from '@/pages/UsersPage'
import AccountPage from '@/pages/AccountPage'
import { setupApi } from '@/lib/api'
import { getDefaultRoute, hasPermission, UI_PERMISSIONS } from '@/lib/permissions'
import { UiLanguageProvider, useUiLanguage } from '@/lib/ui-language'
import { useAuth } from '@/hooks/useAuth'

function AuthProvider({ children }: { children: ReactNode }) {
  const auth = useAuthState()
  return <AuthContext.Provider value={auth}>{children}</AuthContext.Provider>
}

function SetupGuard({ children }: { children: ReactNode }) {
  const { copy } = useUiLanguage()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['setup', 'status'],
    queryFn: setupApi.status,
    retry: false,
    staleTime: Infinity,
  })
  const location = useLocation()

  if (isLoading) return null
  if (isError) {
    return (
      <div className="flex h-screen items-center justify-center text-muted-foreground text-sm">
        {copy.setupUnavailable}
      </div>
    )
  }
  if (data?.needs_setup && location.pathname !== '/setup') {
    return <Navigate to="/setup" replace />
  }
  if (!data?.needs_setup && location.pathname === '/setup') {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

function PermissionRoute({
  permission,
  children,
}: {
  permission: string
  children: ReactNode
}) {
  const { user, isLoading } = useAuth()

  if (isLoading) return null
  if (!user) return <Navigate to="/login" replace />
  if (!hasPermission(user, permission)) {
    return <Navigate to={getDefaultRoute(user)} replace />
  }
  return <>{children}</>
}

function DefaultRedirect() {
  const { user, isLoading } = useAuth()

  if (isLoading) return null
  return <Navigate to={getDefaultRoute(user)} replace />
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <UiLanguageProvider>
          <SetupGuard>
            <Routes>
              <Route path="/setup" element={<SetupPage />} />
              <Route path="/login" element={<LoginPage />} />
              <Route path="/register" element={<RegisterPage />} />
              <Route path="/forgot-password" element={<ForgotPasswordPage />} />
              <Route path="/reset-password" element={<ResetPasswordPage />} />

              <Route element={<ProtectedRoute />}>
                <Route element={<AppLayout />}>
                  <Route path="/dashboard" element={<PermissionRoute permission={UI_PERMISSIONS.dashboardView}><DashboardPage /></PermissionRoute>} />
                  <Route path="/servers" element={<PermissionRoute permission={UI_PERMISSIONS.serversView}><ServersPage /></PermissionRoute>} />
                  <Route path="/backups" element={<PermissionRoute permission={UI_PERMISSIONS.backupsView}><BackupsPage /></PermissionRoute>} />
                  <Route path="/autorestart" element={<PermissionRoute permission={UI_PERMISSIONS.autorestartView}><AutorestartPage /></PermissionRoute>} />
                  <Route path="/console" element={<PermissionRoute permission={UI_PERMISSIONS.consoleView}><ConsolePage /></PermissionRoute>} />
                  <Route path="/mods" element={<PermissionRoute permission={UI_PERMISSIONS.modsView}><ModsPage /></PermissionRoute>} />
                  <Route path="/config" element={<PermissionRoute permission={UI_PERMISSIONS.filesRead}><ConfigCenterPage /></PermissionRoute>} />
                  <Route path="/files" element={<PermissionRoute permission={UI_PERMISSIONS.filesRead}><FileManagerPage /></PermissionRoute>} />
                  <Route path="/users" element={<PermissionRoute permission={UI_PERMISSIONS.usersView}><UsersPage /></PermissionRoute>} />
                  <Route path="/account" element={<AccountPage />} />
                </Route>
              </Route>

              <Route path="*" element={<DefaultRedirect />} />
            </Routes>
          </SetupGuard>
        </UiLanguageProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
