import { useState, useEffect, lazy, Suspense } from 'react'
import { Routes, Route } from 'react-router-dom'
import { Shell } from './components/layout/Shell'
import { ProtectedRoute } from './components/ProtectedRoute'
import { PublicOnlyRoute } from './components/PublicOnlyRoute'
import { RequirePermission } from './components/RequirePermission'
import { ToastContainer } from './components/ui/ToastContainer'
import { ConfirmDialog } from './components/ui/ConfirmDialog'
import { Loader } from './components/ui/Loader'

const SetupWizard = lazy(() => import('./pages/SetupWizard').then(module => ({ default: module.SetupWizard })))
const Login = lazy(() => import('./pages/Login').then(module => ({ default: module.Login })))
const Register = lazy(() => import('./pages/Register').then(module => ({ default: module.Register })))
const ForgotPassword = lazy(() => import('./pages/ForgotPassword').then(module => ({ default: module.ForgotPassword })))
const ResetPassword = lazy(() => import('./pages/ResetPassword').then(module => ({ default: module.ResetPassword })))
const Dashboard = lazy(() => import('./pages/Dashboard').then(module => ({ default: module.Dashboard })))
const Servers = lazy(() => import('./pages/Servers').then(module => ({ default: module.Servers })))
const ServerDetail = lazy(() => import('./pages/ServerDetail').then(module => ({ default: module.ServerDetail })))
const Users = lazy(() => import('./pages/Users').then(module => ({ default: module.Users })))
const Roles = lazy(() => import('./pages/Roles').then(module => ({ default: module.Roles })))
const Settings = lazy(() => import('./pages/Settings').then(module => ({ default: module.Settings })))
const Profile = lazy(() => import('./pages/Profile').then(module => ({ default: module.Profile })))
const Docs = lazy(() => import('./pages/Docs').then(module => ({ default: module.Docs })))
const Blueprints = lazy(() => import('./pages/Blueprints').then(module => ({ default: module.Blueprints })))
const Privacy = lazy(() => import('./pages/Privacy').then(module => ({ default: module.Privacy })))
import { useAuthStore } from '@/stores/authStore'
import { CookieConsent } from './components/ui/CookieConsent'

function App() {
  const [setupRequired, setSetupRequired] = useState<boolean | null>(null)
  const { isAuthenticated } = useAuthStore()

  useEffect(() => {
    fetch('/api/auth/setup-status')
      .then((res) => res.json())
      .then((data) => setSetupRequired(data.setup_required))
      .catch(() => setSetupRequired(false))
  }, [])

  if (setupRequired === null) {
    return <Loader fullScreen label="Maunting Server Manager" />
  }

  if (setupRequired) {
    return (
      <Suspense fallback={
        <Loader fullScreen label="Maunting Server Manager" />
      }>
        <SetupWizard onComplete={() => setSetupRequired(false)} />
      </Suspense>
    )
  }

  return (
    <>
      <Suspense fallback={
        <Loader fullScreen label="Maunting Server Manager" />
      }>
        <Routes>
        {/* Oeffentliche Auth-Routen — nur fuer nicht-eingeloggte User */}
        <Route path="/login" element={<PublicOnlyRoute><Login /></PublicOnlyRoute>} />
        <Route path="/register" element={<PublicOnlyRoute><Register /></PublicOnlyRoute>} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route path="/forgot-password" element={<PublicOnlyRoute><ForgotPassword /></PublicOnlyRoute>} />
        
        {/* Oeffentliche Datenschutz-Route, wenn nicht eingeloggt */}
        {!isAuthenticated && <Route path="/privacy" element={<Privacy />} />}

        {/* Geschuetzte App-Routen */}
        <Route path="/*" element={<ProtectedRoute><Shell /></ProtectedRoute>}>
          <Route index element={<Dashboard />} />
          <Route path="servers" element={<Servers />} />
          <Route path="servers/:id" element={<ServerDetail />} />
          <Route
            path="users"
            element={
              <RequirePermission routeKey="users">
                <Users />
              </RequirePermission>
            }
          />
          <Route
            path="roles"
            element={
              <RequirePermission routeKey="roles">
                <Roles />
              </RequirePermission>
            }
          />
          <Route
            path="settings"
            element={
              <RequirePermission routeKey="settings">
                <Settings />
              </RequirePermission>
            }
          />
          <Route path="profile" element={<Profile />} />
          <Route path="docs" element={<Docs />} />
          <Route path="privacy" element={<Privacy />} />
          <Route
            path="blueprints"
            element={
              <RequirePermission routeKey="blueprints">
                <Blueprints />
              </RequirePermission>
            }
          />
          <Route path="*" element={<RequirePermission routeKey="missing-route" />} />
        </Route>
      </Routes>
      </Suspense>
      <CookieConsent />
      <ToastContainer />
      <ConfirmDialog />
    </>
  )
}

export default App
