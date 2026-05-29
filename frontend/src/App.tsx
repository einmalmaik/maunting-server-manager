import { useState, useEffect, lazy, Suspense } from 'react'
import { Routes, Route } from 'react-router-dom'
import { Shell } from './components/layout/Shell'
import { ProtectedRoute } from './components/ProtectedRoute'
import { PublicOnlyRoute } from './components/PublicOnlyRoute'
import { RequirePermission } from './components/RequirePermission'
import { ToastContainer } from './components/ui/ToastContainer'
import { ConfirmDialog } from './components/ui/ConfirmDialog'

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
import { CookieBanner } from './components/ui/CookieBanner'

function App() {
  const [setupRequired, setSetupRequired] = useState<boolean | null>(null)

  useEffect(() => {
    fetch('/api/auth/setup-status')
      .then((res) => res.json())
      .then((data) => setSetupRequired(data.setup_required))
      .catch(() => setSetupRequired(false))
  }, [])

  if (setupRequired === null) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (setupRequired) {
    return (
      <Suspense fallback={
        <div className="min-h-screen bg-background flex items-center justify-center">
          <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        </div>
      }>
        <SetupWizard onComplete={() => setSetupRequired(false)} />
      </Suspense>
    )
  }

  return (
    <>
      <Suspense fallback={
        <div className="min-h-screen bg-background flex items-center justify-center">
          <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        </div>
      }>
        <Routes>
        {/* Oeffentliche Auth-Routen — nur fuer nicht-eingeloggte User */}
        <Route path="/login" element={<PublicOnlyRoute><Login /></PublicOnlyRoute>} />
        <Route path="/register" element={<PublicOnlyRoute><Register /></PublicOnlyRoute>} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route path="/forgot-password" element={<PublicOnlyRoute><ForgotPassword /></PublicOnlyRoute>} />

        {/* Geschuetzte App-Routen */}
        <Route path="/*" element={<ProtectedRoute><Shell /></ProtectedRoute>}>
          <Route index element={<Dashboard />} />
          <Route path="servers" element={<Servers />} />
          <Route path="servers/:id" element={<ServerDetail />} />
          <Route
            path="users"
            element={
              <RequirePermission keys={['users.read', 'users.manage']}>
                <Users />
              </RequirePermission>
            }
          />
          <Route
            path="roles"
            element={
              <RequirePermission keys="roles.manage">
                <Roles />
              </RequirePermission>
            }
          />
          <Route
            path="settings"
            element={
              <RequirePermission keys="panel.settings.read">
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
              <RequirePermission keys="panel.settings.read">
                <Blueprints />
              </RequirePermission>
            }
          />
        </Route>
      </Routes>
      </Suspense>
      <CookieBanner />
      <ToastContainer />
      <ConfirmDialog />
    </>
  )
}

export default App
