import { useState, useEffect } from 'react'
import { Routes, Route } from 'react-router-dom'
import { Shell } from './components/layout/Shell'
import { ProtectedRoute } from './components/ProtectedRoute'
import { PublicOnlyRoute } from './components/PublicOnlyRoute'
import { RequirePermission } from './components/RequirePermission'
import { ToastContainer } from './components/ui/ToastContainer'
import { ConfirmDialog } from './components/ui/ConfirmDialog'
import { SetupWizard } from './pages/SetupWizard'
import { Login } from './pages/Login'
import { Register } from './pages/Register'
import { VerifyEmail } from './pages/VerifyEmail'
import { ForgotPassword } from './pages/ForgotPassword'
import { ResetPassword } from './pages/ResetPassword'
import { Dashboard } from './pages/Dashboard'
import { Servers } from './pages/Servers'
import { ServerDetail } from './pages/ServerDetail'
import { Users } from './pages/Users'
import { Roles } from './pages/Roles'
import { Settings } from './pages/Settings'
import { Profile } from './pages/Profile'
import { Docs } from './pages/Docs'

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
    return <SetupWizard onComplete={() => setSetupRequired(false)} />
  }

  return (
    <>
      <Routes>
      {/* Oeffentliche Auth-Routen — nur fuer nicht-eingeloggte User */}
      <Route path="/login" element={<PublicOnlyRoute><Login /></PublicOnlyRoute>} />
      <Route path="/register" element={<PublicOnlyRoute><Register /></PublicOnlyRoute>} />
      <Route path="/verify-email" element={<VerifyEmail />} />
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
            // Nur `panel.settings.read` — die Settings-Seite ruft direkt
            // `GET /api/settings` auf, das genau diesen Key verlangt. Wer nur
            // `.write` haette, wuerde nur eine kaputte Seite sehen.
            <RequirePermission keys="panel.settings.read">
              <Settings />
            </RequirePermission>
          }
        />
        <Route path="profile" element={<Profile />} />
        <Route path="docs" element={<Docs />} />
      </Route>
    </Routes>
      <ToastContainer />
      <ConfirmDialog />
    </>
  )
}

export default App
