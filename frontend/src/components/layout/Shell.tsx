import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'
import { LegalFooter } from '@/components/LegalFooter'

export function Shell() {
  return (
    <div className="min-h-screen bg-background text-on-surface flex relative overflow-hidden">
      {/* Deep Grid Background */}
      <div className="absolute inset-0 msm-deep-grid opacity-30 pointer-events-none" />

      {/* Sidebar */}
      <Sidebar />

      {/* Main Content Area */}
      <div className="flex-1 md:ml-64 flex flex-col min-w-0 relative z-10">
        <Topbar />
        <main className="flex-1 p-margin-mobile md:p-margin-desktop overflow-auto relative flex flex-col">
          <div className="relative z-10 flex-1">
            <Outlet />
          </div>

          <LegalFooter className="relative z-10 mt-12 border-t border-outline-variant/30 pt-4" />
        </main>
      </div>
    </div>
  )
}
