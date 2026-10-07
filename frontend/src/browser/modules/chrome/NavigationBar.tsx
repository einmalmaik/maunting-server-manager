import { ArrowLeft, ArrowRight, RotateCw, X, Home } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useBrowserStore } from '../../services/browserStore'

export function NavigationBar() {
  const { t } = useTranslation()
  const {
    tabs,
    activeTabId,
    reloadActiveTab,
    navigateActiveTab,
    goBackActiveTab,
    goForwardActiveTab,
  } = useBrowserStore()
  const activeTab = tabs.find((t) => t.id === activeTabId)

  return (
    <div className="flex items-center gap-0.5 shrink-0 text-muted-foreground">
      <button
        disabled={!activeTab?.canGoBack}
        onClick={goBackActiveTab}
        className="p-1.5 rounded-md hover:bg-muted hover:text-foreground disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
        aria-label={`${t('browser.nav.back')} (Alt+Links)`}
      >
        <ArrowLeft className="w-4 h-4" />
      </button>

      <button
        disabled={!activeTab?.canGoForward}
        onClick={goForwardActiveTab}
        className="p-1.5 rounded-md hover:bg-muted hover:text-foreground disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
        aria-label={`${t('browser.nav.forward')} (Alt+Rechts)`}
      >
        <ArrowRight className="w-4 h-4" />
      </button>

      <button
        onClick={reloadActiveTab}
        className="p-1.5 rounded-md hover:bg-muted hover:text-foreground transition-colors"
        aria-label={activeTab?.isLoading ? `${t('browser.nav.stop')} (Esc)` : `${t('browser.nav.reload')} (Strg+R)`}
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
        aria-label={t('browser.nav.home')}
      >
        <Home className="w-4 h-4" />
      </button>
    </div>
  )
}
