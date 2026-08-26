import { useEffect, useRef, useState } from 'react'
import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'
import { VersionFooter } from '@/components/VersionFooter'
import { AiRunNotice } from '@/components/ai/AiRunNotice'
import { PanelPopupModal } from '@/components/popups/PanelPopupModal'

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
    // `overflow-x-clip` statt `overflow-x-hidden`: `hidden` auf einer Achse
    // lässt die andere zu `auto` rechnen und macht diese Wurzel damit zum
    // Scroll-Container. Weil sie mit `min-h-screen` mitwächst, scrollt sie nie
    // — und jedes `position: sticky` darunter, allen voran `.msm-topbar`,
    // bekommt dadurch nie einen Versatz. `clip` klemmt den waagerechten
    // Überlauf genauso ab, erzeugt aber keinen Scroll-Container.
    <div className="min-h-screen bg-background text-on-surface flex relative overflow-x-clip">
      {/* Deep Grid Background */}
      <div className="absolute inset-0 msm-deep-grid opacity-30 pointer-events-none" />

      {/* Sidebar */}
      <Sidebar />

      {mobileNavigationOpen && (
        <div className="fixed inset-0 z-50 h-[100dvh] w-screen overflow-hidden lg:hidden" role="presentation" data-testid="mobile-navigation-layer">
          <div
            className="absolute inset-0 bg-black/70 backdrop-blur-sm"
            aria-hidden="true"
            onClick={closeMobileNavigation}
          />
          <Sidebar mobile onNavigate={closeMobileNavigation} />
        </div>
      )}

      {/* Main Content Area */}
      <div className="flex-1 lg:ml-64 flex flex-col min-w-0 relative z-10">
        <Topbar menuButtonRef={mobileNavigationTriggerRef} onOpenNavigation={() => setMobileNavigationOpen(true)} />
        {/* Ohne `overflow-auto`: `main` hat als `flex-1` in einer Spalte ohne
            feste Höhe immer genau seine Inhaltshöhe, lief also nie über. Die
            Klasse hat nur einen Scroll-Container erzeugt, an dem sich die
            Klebeelemente der Seiten (Reiterleiste, Inhaltsverzeichnisse)
            vergeblich ausgerichtet haben. Breite Inhalte bringen ihr eigenes
            `overflow-x-auto` mit. */}
        <main className="flex-1 p-margin-mobile md:p-margin-desktop relative flex flex-col">
          <div className="relative z-10 flex-1 w-full">
            <Outlet />
          </div>

          <VersionFooter />
        </main>
      </div>

      {/* Meldet unten rechts, wenn ein KI-Auftrag fertig ist oder wartet —
          auf jeder Seite. Der Gegenwert dazu, dass die KI im Hintergrund
          weiterarbeitet: sonst muesste man den Chat offen lassen, also genau
          das tun, was nicht mehr noetig sein soll. */}
      <AiRunNotice />

      {/* Aktive Pop-ups / Ankündigungen des Panels */}
      <PanelPopupModal />
    </div>
  )
}
