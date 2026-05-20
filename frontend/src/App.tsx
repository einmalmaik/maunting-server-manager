import { Routes, Route } from 'react-router-dom'
import { Shell } from './components/layout/Shell'
import { Setup } from './pages/Setup'
import { Login } from './pages/Login'
import { Register } from './pages/Register'
import { Dashboard } from './pages/Dashboard'
import { Servers } from './pages/Servers'
import { Backups } from './pages/Backups'
import { Users } from './pages/Users'
import { Settings } from './pages/Settings'

function App() {
  return (
    <Routes>
      <Route path="/setup" element={<Setup />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/verify-email" element={<div>Email Verification (TODO)</div>} />
      <Route path="/reset-password" element={<div>Reset Password (TODO)</div>} />
      <Route path="/forgot-password" element={<div>Forgot Password (TODO)</div>} />
      <Route path="/*" element={<Shell />}>
        <Route index element={<Dashboard />} />
        <Route path="servers" element={<Servers />} />
        <Route path="backups" element={<Backups />} />
        <Route path="users" element={<Users />} />
        <Route path="settings" element={<Settings />} />
      </Route>
    </Routes>
  )
}

export default App
