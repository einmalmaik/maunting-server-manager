import { useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'
import TopBar from './TopBar'
import { loadLocalValue, saveLocalValue } from '@/lib/workspace'

const SIDEBAR_COLLAPSED_KEY = 'conan-panel:sidebar-collapsed'

export default function AppLayout() {
  const [collapsed, setCollapsed] = useState(() => loadLocalValue(SIDEBAR_COLLAPSED_KEY, false))

  useEffect(() => {
    saveLocalValue(SIDEBAR_COLLAPSED_KEY, collapsed)
  }, [collapsed])

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((c) => !c)} />

      <div className="flex flex-1 flex-col overflow-hidden">
        <TopBar />
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
