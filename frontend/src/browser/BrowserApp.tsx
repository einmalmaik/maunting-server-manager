import { useState, useEffect } from 'react'
import { TabStrip } from './modules/chrome/TabStrip'
import { NavigationBar } from './modules/chrome/NavigationBar'
import { Omnibox } from './modules/chrome/Omnibox'
import { SidebarNavigation } from './modules/sidebar/SidebarNavigation'
import { SidebarContainer } from './modules/sidebar/SidebarContainer'
import { NewTabPage } from './modules/newtab/NewTabPage'
import { PasswordPrompt, type SavePromptData } from './modules/autofill/PasswordPrompt'
import { PaymentConfirm } from './modules/autofill/PaymentConfirm'
import { useBrowserStore } from './services/browserStore'
import { GlobeIcon } from './modules/newtab/brandIcons'

export function BrowserApp() {
  const {
    tabs,
    activeTabId,
    createTab,
    closeTab,
    reloadActiveTab,
    toggleDrawer,
    setOpenDrawer,
    theme,
  } = useBrowserStore()

  const activeTab = tabs.find((t) => t.id === activeTabId)

  // Simulation für Password Prompt & Payment Confirm
  const [savePromptData, setSavePromptData] = useState<SavePromptData | null>(null)
  const [showPaymentConfirm, setShowPaymentConfirm] = useState(false)

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
        <main className="flex-1 h-full overflow-hidden bg-background relative flex flex-col">
          {isBlankPage ? (
            <NewTabPage />
          ) : (
            <div className="flex-1 w-full h-full flex flex-col items-center justify-center p-6 text-center select-text bg-muted/20">
              <div className="max-w-md w-full p-6 bg-card border border-border rounded-2xl shadow-xl flex flex-col items-center">
                <div className="w-12 h-12 rounded-2xl bg-primary/10 text-primary flex items-center justify-center mb-3">
                  <GlobeIcon className="w-6 h-6" />
                </div>
                <h3 className="text-sm font-semibold text-foreground mb-1">
                  {activeTab?.title || 'Externe Webseite geladen'}
                </h3>
                <p className="text-xs text-muted-foreground font-mono truncate max-w-full mb-4">
                  {activeTab?.url}
                </p>
                <div className="p-3 bg-muted/50 rounded-xl text-left text-label-sm text-muted-foreground space-y-1.5 w-full mb-4">
                  <div className="flex justify-between">
                    <span>Engine:</span>
                    <span className="font-semibold text-foreground">Microsoft Edge WebView2 (Chromium)</span>
                  </div>
                  <div className="flex justify-between">
                    <span>adblock-rs Filter:</span>
                    <span className="text-status-success font-semibold">Aktiv</span>
                  </div>
                  <div className="flex justify-between">
                    <span>DIS Form-Erkennung:</span>
                    <span className="text-status-success font-semibold">Scharfgeschaltet</span>
                  </div>
                </div>

                <div className="flex gap-2 w-full">
                  <button
                    onClick={() => {
                      setSavePromptData({
                        domain: new URL(activeTab?.url || 'https://example.com').hostname,
                        url: activeTab?.url || '',
                        username: 'test.user@beispiel.de',
                        password: 'GeheimesPasswort123!',
                      })
                    }}
                    className="flex-1 py-2 rounded-xl bg-muted hover:bg-muted/80 text-xs font-medium text-foreground transition-colors"
                  >
                    Login simulieren
                  </button>
                  <button
                    onClick={() => setShowPaymentConfirm(true)}
                    className="flex-1 py-2 rounded-xl bg-muted hover:bg-muted/80 text-xs font-medium text-foreground transition-colors"
                  >
                    Kreditkarte testen
                  </button>
                </div>
              </div>
            </div>
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
