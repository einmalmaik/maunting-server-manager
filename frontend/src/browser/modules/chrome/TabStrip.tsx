import { Plus, X, EyeOff, Loader2, Pin, Minus, Square } from 'lucide-react'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { useTranslation } from 'react-i18next'
import { useBrowserStore } from '../../services/browserStore'
import {
  nativeFensterMinimieren,
  nativeFensterMaximierenUmschalten,
  nativeFensterSchliessen,
} from '../../services/tauriBridge'

export function TabStrip() {
  const { t } = useTranslation()
  const { tabs, activeTabId, createTab, closeTab, activateTab } = useBrowserStore()

  const handleMinimize = async () => {
    try {
      await nativeFensterMinimieren()
    } catch {
      await getCurrentWindow().minimize().catch(() => {})
    }
  }

  const handleDragStart = async (e: React.MouseEvent) => {
    if (e.button === 0) {
      try {
        await getCurrentWindow().startDragging()
      } catch {
        // Nicht in Tauri-Umgebung
      }
    }
  }

  const handleToggleMaximize = async () => {
    try {
      await nativeFensterMaximierenUmschalten()
    } catch {
      await getCurrentWindow().toggleMaximize().catch(() => {})
    }
  }

  const handleClose = async () => {
    try {
      await nativeFensterSchliessen()
    } catch {
      await getCurrentWindow().close().catch(() => {})
    }
  }

  return (
    <div
      data-tauri-drag-region
      onMouseDown={handleDragStart}
      onDoubleClick={handleToggleMaximize}
      className="flex items-center bg-muted/70 px-2 pt-1 gap-1 border-b border-border select-none h-10 w-full cursor-default"
    >
      {/* Tabs Container */}
      <div
        data-tauri-drag-region
        onMouseDown={handleDragStart}
        className="flex items-center gap-1 flex-1 min-w-0 overflow-x-auto msm-ohne-rollbalken h-full pt-1"
      >
        {tabs.map((tab) => {
          const isActive = tab.id === activeTabId

          return (
            <div
              key={tab.id}
              onClick={() => activateTab(tab.id)}
              onMouseDown={(e) => e.stopPropagation()}
              className={`group relative flex items-center gap-2 px-3 py-1 rounded-t-md cursor-pointer text-xs transition-all max-w-[220px] min-w-[120px] h-full border-t border-x ${
                isActive
                  ? 'bg-background text-foreground font-medium border-border shadow-sm'
                  : 'bg-muted/30 text-muted-foreground hover:bg-muted/80 border-transparent'
              }`}
            >
              {/* Status Icon */}
              <div className="shrink-0 flex items-center justify-center w-4 h-4">
                {tab.isLoading ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />
                ) : tab.isIncognito ? (
                  <span aria-label={t('browser.tab.newPrivateTab')}>
                    <EyeOff className="w-3.5 h-3.5 text-primary" />
                  </span>
                ) : tab.favicon ? (
                  <img src={tab.favicon} alt="" className="w-3.5 h-3.5 rounded-sm" />
                ) : (
                  <img src="/msp.png" alt="" className="w-3.5 h-3.5 rounded-full object-contain" />
                )}
              </div>

              {/* Titel */}
              <span className="truncate flex-1">
                {tab.title || t('browser.tab.newTab')}
              </span>

              {/* Pin Indicator */}
              {tab.isPinned && (
                <Pin className="w-3 h-3 text-muted-foreground shrink-0 rotate-45" />
              )}

              {/* Schließen Button */}
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  closeTab(tab.id)
                }}
                className="opacity-0 group-hover:opacity-100 hover:bg-muted-foreground/20 p-0.5 rounded transition-all shrink-0"
                aria-label={`${t('browser.tab.closeTab')} (Strg+W)`}
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          )
        })}

        {/* Neuer Tab Buttons */}
        <div
          onMouseDown={(e) => e.stopPropagation()}
          className="flex items-center gap-0.5 pl-1 shrink-0"
        >
          <button
            onClick={() => createTab()}
            className="p-1 hover:bg-muted rounded-md text-muted-foreground hover:text-foreground transition-colors"
            aria-label={`${t('browser.tab.newTab')} (Strg+T)`}
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => createTab('about:blank', true)}
            className="p-1 hover:bg-muted rounded-md text-muted-foreground hover:text-primary transition-colors"
            aria-label={t('browser.tab.newPrivateTab')}
          >
            <EyeOff className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Drag-Spacer */}
      <div
        data-tauri-drag-region
        onMouseDown={handleDragStart}
        onDoubleClick={handleToggleMaximize}
        className="flex-1 min-w-[20px] h-full cursor-default"
      />

      {/* Native-style Fenstertasten (Minimieren, Maximieren, Schließen) */}
      <div
        onMouseDown={(e) => e.stopPropagation()}
        className="flex items-center h-full shrink-0 -mr-2"
      >
        <button
          onClick={handleMinimize}
          className="h-full px-3 hover:bg-muted text-muted-foreground hover:text-foreground transition-colors flex items-center justify-center"
          aria-label={t('browser.window.minimize')}
        >
          <Minus className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={handleToggleMaximize}
          className="h-full px-3 hover:bg-muted text-muted-foreground hover:text-foreground transition-colors flex items-center justify-center"
          aria-label={t('browser.window.maximize')}
        >
          <Square className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={handleClose}
          className="h-full px-3 hover:bg-status-destructive hover:text-white text-muted-foreground transition-colors flex items-center justify-center"
          aria-label={t('browser.window.close')}
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  )
}
