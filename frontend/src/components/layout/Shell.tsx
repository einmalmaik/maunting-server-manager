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
        <main className="flex-1 p-margin-mobile md:p-margin-desktop overflow-auto relative flex flex-col">
          {/* Ambient Glow */}
          <div className="absolute top-20 left-1/2 -translate-x-1/2 w-[600px] h-[400px] bg-secondary/10 blur-[100px] rounded-full pointer-events-none opacity-40" />

          <div className="relative z-10 flex-1">
            <Outlet />
          </div>

          <footer className="relative z-10 mt-12 pt-4 border-t border-border/20 text-center text-xs text-muted-foreground/60">
             © 2026 · <a href="/privacy" className="hover:text-foreground transition-colors">Datenschutz</a> · <a href="/imprint" className="hover:text-foreground transition-colors">Impressum</a>
          </footer>
        </main>
      </div>
    </div>
  )
}
