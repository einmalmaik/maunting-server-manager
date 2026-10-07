import { useState, useEffect, useRef } from 'react'
import { TabStrip } from './modules/chrome/TabStrip'
import { NavigationBar } from './modules/chrome/NavigationBar'
import { Omnibox } from './modules/chrome/Omnibox'
import { SidebarNavigation } from './modules/sidebar/SidebarNavigation'
import { SidebarContainer } from './modules/sidebar/SidebarContainer'
import { NewTabPage } from './modules/newtab/NewTabPage'
import { PasswordPrompt, type SavePromptData } from './modules/autofill/PasswordPrompt'
import { PaymentConfirm } from './modules/autofill/PaymentConfirm'
import { useBrowserStore } from './services/browserStore'
import {
  nativeTabBoundsAnpassen,
  setupTauriListeners,
  nativeAdblockStatus,
  nativeTabSichtbarkeitSetzen,
  nativeHauptfensterFokussieren,
} from './services/tauriBridge'

export function BrowserApp() {
  const {
    tabs,
    activeTabId,
    createTab,
    closeTab,
    reloadActiveTab,
    toggleDrawer,
    setOpenDrawer,
    openDrawer,
    theme,
    incrementBlockCount,
    setBlockedStats,
    updateTab,
  } = useBrowserStore()

  const activeTab = tabs.find((t) => t.id === activeTabId)
  const containerRef = useRef<HTMLElement>(null)

  // Simulation für Password Prompt & Payment Confirm
  const [savePromptData, setSavePromptData] = useState<SavePromptData | null>(null)
  const [showPaymentConfirm, setShowPaymentConfirm] = useState(false)

  // Synchronisation von Tauri Events & Statistiken
  useEffect(() => {
    let cleanup: (() => void) | undefined

    setupTauriListeners({
      onAdblockEvent: (e) => {
        incrementBlockCount(e.typ === 'tracker' ? 'tracker' : 'ad')
      },
      onTabNavigated: (e) => {
        updateTab(e.id, { url: e.url, title: e.url.startsWith('about:') ? 'Neuer Tab' : e.url })
      },
      onPageLoading: (e) => {
        updateTab(e.id, { url: e.url, isLoading: true })
      },
      onPageLoaded: (e) => {
        updateTab(e.id, { url: e.url, title: e.url.startsWith('about:') ? 'Neuer Tab' : e.url, isLoading: false })
      },
    }).then((fn) => {
      cleanup = fn
    })

    nativeAdblockStatus().then((stats) => {
      if (stats) {
        setBlockedStats(stats.geblockte_anzeigen, stats.geblockte_tracker)
      }
    })

    return () => {
      cleanup?.()
    }
  }, [incrementBlockCount, setBlockedStats, updateTab])

  // Native Webview Bounds synchronisieren (bei Resize & Drawer-Wechsel)
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const updateBounds = () => {
      const rect = el.getBoundingClientRect()
      // Nur aktualisieren wenn Maße gültig sind
      if (rect.width > 0 && rect.height > 0) {
        void nativeTabBoundsAnpassen(rect.left, rect.top, rect.width, rect.height)
      }
    }

    const observer = new ResizeObserver(updateBounds)
    observer.observe(el)
    updateBounds()

    window.addEventListener('resize', updateBounds)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', updateBounds)
    }
  }, [openDrawer])

  // Wenn ein Drawer oder modaler Dialog offen ist, native Webview ausblenden, damit Klicks & Scrollen voll funktionieren
  useEffect(() => {
    if (openDrawer !== 'none' || showPaymentConfirm || savePromptData !== null) {
      void nativeTabSichtbarkeitSetzen(false)
    } else {
      void nativeTabSichtbarkeitSetzen(true)
    }
  }, [openDrawer, showPaymentConfirm, savePromptData])

  // Bei Klick in HTML Inputs (Omnibox, Einstellungen etc.) OS-Fokus auf Hauptfenster sicherstellen
  useEffect(() => {
    const handleFocusIn = (e: FocusEvent) => {
      const target = e.target as HTMLElement | null
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) {
        void nativeHauptfensterFokussieren()
      }
    }

    window.addEventListener('focusin', handleFocusIn)
    return () => {
      window.removeEventListener('focusin', handleFocusIn)
    }
  }, [])

  // Global Keyboard Shortcuts
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 't') {
        e.preventDefault()
        createTab()
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'w') {
        e.preventDefault()
        if (activeTabId) closeTab(activeTabId)
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'r') {
        e.preventDefault()
        reloadActiveTab()
      } else if (e.altKey && e.key.toLowerCase() === 's') {
        e.preventDefault()
        toggleDrawer('ai')
      } else if (e.key === 'Escape') {
        setOpenDrawer('none')
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [activeTabId, createTab, closeTab, reloadActiveTab, toggleDrawer, setOpenDrawer])

  // Theme-Klassen synchronisieren
  useEffect(() => {
    const root = document.documentElement
    if (theme === 'dark') {
      root.classList.add('dark')
    } else if (theme === 'light') {
      root.classList.remove('dark')
    } else {
      // System Theme
      const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches
      if (isDark) root.classList.add('dark')
      else root.classList.remove('dark')
    }
  }, [theme])

  const isBlankPage = !activeTab?.url || activeTab.url === 'about:blank'

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-background text-foreground select-none font-sans">
      {/* 1. Tab-Leiste ganz oben */}
      <TabStrip />

      {/* 2. Navigationsleiste & Omnibox */}
      <header className="flex items-center px-2 py-1.5 bg-background border-b border-border shadow-sm z-10">
        <NavigationBar />
        <Omnibox />
      </header>

      {/* 3. Arbeitsbereich (Seitenleiste + Viewport) */}
      <div className="flex flex-1 overflow-hidden relative">
        {/* Seitenleisten-Dock (Opera Style) */}
        <SidebarNavigation />

        {/* Aufklappbarer Drawer-Inhalt */}
        <SidebarContainer />

        {/* Webview / Content Viewport */}
        <main
          ref={containerRef}
          className="flex-1 h-full overflow-hidden bg-background relative flex flex-col"
        >
          {isBlankPage ? (
            <NewTabPage />
          ) : (
            /* Wenn externe Webseite geladen ist, rendert der native Child-Webview direkt in diesem Bereich */
            <div className="flex-1 w-full h-full bg-transparent pointer-events-none" />
          )}
        </main>
      </div>

      {/* 4. Autofill Prompts & Modals */}
      <PasswordPrompt
        data={savePromptData}
        onSave={() => {
          setSavePromptData(null)
          alert('Zugangsdaten erfolgreich im DIS-Passworttresor gespeichert!')
        }}
        onDismiss={() => setSavePromptData(null)}
      />

      <PaymentConfirm
        isOpen={showPaymentConfirm}
        paymentType="credit_card"
        domain={activeTab?.url ? new URL(activeTab.url).hostname : 'Online-Shop'}
        onConfirm={() => {
          setShowPaymentConfirm(false)
          alert('Zahlungsdaten sicher und verifiziert eingefügt!')
        }}
        onCancel={() => setShowPaymentConfirm(false)}
      />
    </div>
  )
}
