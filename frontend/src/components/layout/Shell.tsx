import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'

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
        <main className="flex-1 p-margin-mobile md:p-margin-desktop overflow-auto relative">
          {/* Ambient Glow */}
          <div className="absolute top-20 left-1/2 -translate-x-1/2 w-[600px] h-[400px] bg-secondary/10 blur-[100px] rounded-full pointer-events-none opacity-40" />

          <div className="relative z-10">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
