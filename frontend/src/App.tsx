import { useState, useEffect } from 'react'
import { Routes, Route } from 'react-router-dom'
import { Shell } from './components/layout/Shell'
import { ProtectedRoute } from './components/ProtectedRoute'
import { PublicOnlyRoute } from './components/PublicOnlyRoute'
import { RequirePermission } from './components/RequirePermission'
import { ToastContainer } from './components/ui/ToastContainer'
import { SetupWizard } from './pages/SetupWizard'
import { Login } from './pages/Login'
import { Register } from './pages/Register'
import { VerifyEmail } from './pages/VerifyEmail'
import { ForgotPassword } from './pages/ForgotPassword'
import { ResetPassword } from './pages/ResetPassword'
import { Dashboard } from './pages/Dashboard'
import { Servers } from './pages/Servers'
import { ServerDetail } from './pages/ServerDetail'
import { ConfigEditor } from './pages/ConfigEditor'
import { FileManager } from './pages/FileManager'
import { ModManager } from './pages/ModManager'
import { Backups } from './pages/Backups'
import { Users } from './pages/Users'
import { Roles } from './pages/Roles'
import { Settings } from './pages/Settings'
import { Profile } from './pages/Profile'

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
        <Route path="servers/:id/config" element={<ConfigEditor />} />
        <Route path="servers/:id/files" element={<FileManager />} />
        <Route path="servers/:id/mods" element={<ModManager />} />
        <Route path="servers/:id/backups" element={<Backups />} />
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
            <RequirePermission keys={['panel.settings.read', 'panel.settings.write']}>
              <Settings />
            </RequirePermission>
          }
        />
        <Route path="profile" element={<Profile />} />
      </Route>
    </Routes>
      <ToastContainer />
    </>
  )
}

export default App
