import { X } from 'lucide-react'
import { useBrowserStore } from '../../services/browserStore'
import { MessengerDrawer } from './MessengerDrawer'
import { SingraDrawer } from './SingraDrawer'
import { VaultDrawer } from './VaultDrawer'
import { SettingsView } from '../settings/SettingsView'

export function SidebarContainer() {
  const { openDrawer, setOpenDrawer, history, bookmarks, removeBookmark, navigateActiveTab, clearHistory } =
    useBrowserStore()

  if (openDrawer === 'none') return null

  return (
    <aside className="w-80 sm:w-96 bg-card/95 backdrop-blur-xl border-r border-border flex flex-col h-full shadow-2xl z-20 shrink-0">
      {/* Oberer Titelbalken */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-muted/40 select-none">
        <span className="text-xs font-semibold text-foreground uppercase tracking-wider">
          {openDrawer === 'messenger' && 'MSM Messenger'}
          {openDrawer === 'ai' && 'Singra KI-Begleiter'}
          {openDrawer === 'vault' && 'Passworttresor'}
          {openDrawer === 'settings' && 'Browser-Einstellungen'}
          {openDrawer === 'bookmarks' && 'Lesezeichen'}
          {openDrawer === 'history' && 'Verlauf'}
          {openDrawer === 'downloads' && 'Downloads'}
        </span>
        <button
          onClick={() => setOpenDrawer('none')}
          className="p-1 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
          title="Seitenleiste schließen (Esc)"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Inhalt je nach Drawer */}
      <div className="flex-1 overflow-hidden flex flex-col">
        {openDrawer === 'messenger' && <MessengerDrawer />}
        {openDrawer === 'ai' && <SingraDrawer />}
        {openDrawer === 'vault' && <VaultDrawer />}
        {openDrawer === 'settings' && <SettingsView />}

        {/* Lesezeichen Liste */}
        {openDrawer === 'bookmarks' && (
          <div className="flex-1 overflow-y-auto p-3 space-y-2 text-xs">
            {bookmarks.length === 0 ? (
              <p className="text-muted-foreground text-center py-8">Keine Lesezeichen vorhanden</p>
            ) : (
              bookmarks.map((bm) => (
                <div
                  key={bm.id}
                  onClick={() => navigateActiveTab(bm.url)}
                  className="p-2.5 rounded-xl bg-muted/30 hover:bg-muted/70 cursor-pointer border border-border/40 flex items-center justify-between group"
                >
                  <div className="truncate pr-2">
                    <div className="font-medium text-foreground truncate">{bm.title}</div>
                    <div className="text-label-sm text-muted-foreground truncate">{bm.url}</div>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      removeBookmark(bm.url)
                    }}
                    className="opacity-0 group-hover:opacity-100 p-1 text-muted-foreground hover:text-status-destructive transition-all"
                    title="Lesezeichen löschen"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))
            )}
          </div>
        )}

        {/* Verlaufs Liste */}
        {openDrawer === 'history' && (
          <div className="flex-1 flex flex-col h-full overflow-hidden">
            <div className="p-2.5 border-b border-border flex justify-end">
              <button
                onClick={clearHistory}
                className="text-label-sm text-status-destructive hover:underline font-medium"
              >
                Verlauf leeren
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-3 space-y-2 text-xs">
              {history.length === 0 ? (
                <p className="text-muted-foreground text-center py-8">Verlauf ist leer</p>
              ) : (
                history.map((h) => (
                  <div
                    key={h.id}
                    onClick={() => navigateActiveTab(h.url)}
                    className="p-2 rounded-lg hover:bg-muted/60 cursor-pointer border border-transparent hover:border-border transition-colors"
                  >
                    <div className="font-medium text-foreground truncate">{h.title}</div>
                    <div className="text-label-sm text-muted-foreground truncate">{h.url}</div>
                  </div>
                ))
              )}
            </div>
          </div>
        )}

        {/* Downloads Liste */}
        {openDrawer === 'downloads' && (
          <div className="p-4 text-center text-xs text-muted-foreground">
            Keine aktiven Downloads
          </div>
        )}
      </div>
    </aside>
  )
}
