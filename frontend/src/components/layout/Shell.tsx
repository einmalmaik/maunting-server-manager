import { useEffect, useRef, useState } from 'react'
import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'
import { VersionFooter } from '@/components/VersionFooter'

export function Shell() {
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false)
  const mobileNavigationTriggerRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!mobileNavigationOpen) return
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        mobileNavigationTriggerRef.current?.focus()
        setMobileNavigationOpen(false)
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [mobileNavigationOpen])

  const closeMobileNavigation = () => {
    mobileNavigationTriggerRef.current?.focus()
    setMobileNavigationOpen(false)
  }

  return (
    <div className="min-h-screen bg-background text-on-surface flex relative overflow-x-hidden">
      {/* Deep Grid Background */}
      <div className="absolute inset-0 msm-deep-grid opacity-30 pointer-events-none" />

      {/* Sidebar */}
      <Sidebar />

      {mobileNavigationOpen && (
        <div className="fixed inset-0 z-50 h-[100dvh] w-screen overflow-hidden md:hidden" role="presentation" data-testid="mobile-navigation-layer">
          <div
            className="absolute inset-0 bg-black/70 backdrop-blur-sm"
            aria-hidden="true"
            onClick={closeMobileNavigation}
          />
          <Sidebar mobile onNavigate={closeMobileNavigation} />
        </div>
      )}

      {/* Main Content Area */}
      <div className="flex-1 md:ml-64 flex flex-col min-w-0 relative z-10">
        <Topbar menuButtonRef={mobileNavigationTriggerRef} onOpenNavigation={() => setMobileNavigationOpen(true)} />
        <main className="flex-1 p-margin-mobile md:p-margin-desktop overflow-auto relative flex flex-col">
          <div className="relative z-10 flex-1 w-full">
            <Outlet />
          </div>

          <VersionFooter />
        </main>
      </div>
    </div>
  )
}
