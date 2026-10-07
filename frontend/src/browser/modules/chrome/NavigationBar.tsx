import { ArrowLeft, ArrowRight, RotateCw, X, Home } from 'lucide-react'
import { useBrowserStore } from '../../services/browserStore'

export function NavigationBar() {
  const { tabs, activeTabId, reloadActiveTab, navigateActiveTab } = useBrowserStore()
  const activeTab = tabs.find((t) => t.id === activeTabId)

  return (
    <div className="flex items-center gap-0.5 shrink-0 text-muted-foreground">
      <button
        disabled={!activeTab?.canGoBack}
        onClick={() => {
          // Zurück-Aktion
        }}
        className="p-1.5 rounded-md hover:bg-muted hover:text-foreground disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
        title="Zurück (Alt+Links)"
      >
        <ArrowLeft className="w-4 h-4" />
      </button>

      <button
        disabled={!activeTab?.canGoForward}
        onClick={() => {
          // Vorwärts-Aktion
        }}
        className="p-1.5 rounded-md hover:bg-muted hover:text-foreground disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
        title="Vorwärts (Alt+Rechts)"
      >
        <ArrowRight className="w-4 h-4" />
      </button>

      <button
        onClick={reloadActiveTab}
        className="p-1.5 rounded-md hover:bg-muted hover:text-foreground transition-colors"
        title={activeTab?.isLoading ? 'Laden anhalten (Esc)' : 'Diese Seite neu laden (Strg+R)'}
      >
        {activeTab?.isLoading ? (
          <X className="w-4 h-4 text-status-destructive" />
        ) : (
          <RotateCw className="w-4 h-4" />
        )}
      </button>

      <button
        onClick={() => navigateActiveTab('about:blank')}
        className="p-1.5 rounded-md hover:bg-muted hover:text-foreground transition-colors"
        title="Startseite öffnen"
      >
        <Home className="w-4 h-4" />
      </button>
    </div>
  )
}
