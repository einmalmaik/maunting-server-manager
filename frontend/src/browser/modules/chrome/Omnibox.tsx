import { useState, useEffect, useRef } from 'react'
import {
  Lock,
  Search,
  Star,
  ChevronDown,
  Globe,
  X,
} from 'lucide-react'
import { useBrowserStore } from '../../services/browserStore'
import { SEARCH_ENGINES } from '../../services/searchEngines'
import { ShieldBadge } from './ShieldBadge'

export function Omnibox() {
  const {
    tabs,
    activeTabId,
    searchEngine,
    setSearchEngine,
    navigateActiveTab,
    isBookmarked,
    addBookmark,
    removeBookmark,
  } = useBrowserStore()

  const activeTab = tabs.find((t) => t.id === activeTabId)
  const [inputValue, setInputValue] = useState('')
  const [isFocused, setIsFocused] = useState(false)
  const [showEngineMenu, setShowEngineMenu] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const engineMenuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!isFocused) {
      if (activeTab?.url === 'about:blank' || !activeTab?.url) {
        setInputValue('')
      } else {
        setInputValue(activeTab.url)
      }
    }
  }, [activeTab?.url, isFocused])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (engineMenuRef.current && !engineMenuRef.current.contains(e.target as Node)) {
        setShowEngineMenu(false)
      }
    }
    if (showEngineMenu) {
      document.addEventListener('mousedown', handleClickOutside)
    }
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [showEngineMenu])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      navigateActiveTab(inputValue)
      inputRef.current?.blur()
    } else if (e.key === 'Escape') {
      setInputValue(activeTab?.url === 'about:blank' ? '' : activeTab?.url || '')
      inputRef.current?.blur()
    }
  }

  const currentEngine = SEARCH_ENGINES[searchEngine] || SEARCH_ENGINES.google
  const bookmarked = activeTab?.url ? isBookmarked(activeTab.url) : false
  const isHttps = activeTab?.url?.startsWith('https://')

  const toggleBookmark = () => {
    if (!activeTab?.url || activeTab.url.startsWith('about:')) return
    if (bookmarked) {
      removeBookmark(activeTab.url)
    } else {
      addBookmark(activeTab.url, activeTab.title)
    }
  }

  return (
    <div className="relative flex-1 max-w-3xl mx-2 flex items-center">
      <div
        className={`flex items-center w-full h-8 px-2.5 rounded-lg border text-xs transition-all ${
          isFocused
            ? 'bg-background border-primary ring-2 ring-primary/20 shadow-sm'
            : 'bg-muted/50 hover:bg-muted/80 border-border/60'
        }`}
      >
        {/* Suchmaschinen-Wähler */}
        <div className="relative shrink-0 mr-1.5" ref={engineMenuRef}>
          <button
            onClick={() => setShowEngineMenu(!showEngineMenu)}
            className="flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
            title={`Aktuelle Suchmaschine: ${currentEngine.name} (Klicken zum Wechseln)`}
          >
            <span className="text-sm leading-none">{currentEngine.icon}</span>
            <ChevronDown className="w-3 h-3 opacity-60" />
          </button>

          {showEngineMenu && (
            <div className="absolute left-0 mt-2 w-56 bg-popover/95 backdrop-blur-md border border-border rounded-xl shadow-xl py-1 z-50 animate-scale-in">
              <div className="px-3 py-1.5 text-label-sm font-semibold text-muted-foreground uppercase tracking-wider">
                Suchmaschine wählen
              </div>
              {Object.values(SEARCH_ENGINES).map((engine) => (
                <button
                  key={engine.id}
                  onClick={() => {
                    setSearchEngine(engine.id)
                    setShowEngineMenu(false)
                  }}
                  className={`w-full flex items-center gap-2.5 px-3 py-2 text-left text-xs transition-colors hover:bg-muted ${
                    engine.id === searchEngine ? 'bg-primary/10 text-primary font-medium' : 'text-foreground'
                  }`}
                >
                  <span className="text-base">{engine.icon}</span>
                  <div className="truncate">
                    <div className="font-medium leading-tight">{engine.name}</div>
                    <div className="text-label-sm text-muted-foreground truncate">{engine.description}</div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* SSL-Icon oder Such-Icon */}
        <div className="shrink-0 text-muted-foreground mr-1.5">
          {activeTab?.url?.startsWith('about:') ? (
            <Search className="w-3.5 h-3.5 opacity-60" />
          ) : isHttps ? (
            <span title="Sichere HTTPS-Verbindung">
              <Lock className="w-3.5 h-3.5 text-status-success" />
            </span>
          ) : (
            <Globe className="w-3.5 h-3.5 opacity-60" />
          )}
        </div>

        {/* Eingabefeld */}
        <input
          ref={inputRef}
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onFocus={() => {
            setIsFocused(true)
            inputRef.current?.select()
          }}
          onBlur={() => setIsFocused(false)}
          onKeyDown={handleKeyDown}
          placeholder={`Mit ${currentEngine.name} suchen oder Adresse eingeben...`}
          className="w-full bg-transparent border-none outline-none text-foreground placeholder:text-muted-foreground/60 select-text font-normal"
        />

        {/* Leeren Button bei Eingabe */}
        {inputValue && isFocused && (
          <button
            onClick={() => setInputValue('')}
            className="p-0.5 rounded hover:bg-muted-foreground/20 text-muted-foreground mr-1"
          >
            <X className="w-3 h-3" />
          </button>
        )}

        {/* Lesezeichen-Stern */}
        {activeTab?.url && !activeTab.url.startsWith('about:') && (
          <button
            onClick={toggleBookmark}
            className={`p-1 rounded hover:bg-muted mr-1 transition-colors ${
              bookmarked ? 'text-status-warning' : 'text-muted-foreground hover:text-foreground'
            }`}
            title={bookmarked ? 'Lesezeichen entfernen' : 'Lesezeichen hinzufügen'}
          >
            <Star className={`w-3.5 h-3.5 ${bookmarked ? 'fill-current' : ''}`} />
          </button>
        )}

        {/* Adblock- & Tracker-Schild */}
        <ShieldBadge />
      </div>
    </div>
  )
}
