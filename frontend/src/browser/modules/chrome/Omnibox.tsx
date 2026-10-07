import { useState, useEffect, useRef } from 'react'
import {
  Lock,
  Search,
  Star,
  ChevronDown,
  X,
  CheckCircle2,
  AlertTriangle,
  ShieldCheck,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useBrowserStore } from '../../services/browserStore'
import { SEARCH_ENGINES } from '../../services/searchEngines'
import { ShieldBadge } from './ShieldBadge'
import { getSearchEngineIcon } from '../newtab/searchEngineIcons'

export function Omnibox() {
  const { t } = useTranslation()
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
  const [showCertMenu, setShowCertMenu] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const engineMenuRef = useRef<HTMLDivElement>(null)
  const certMenuRef = useRef<HTMLDivElement>(null)

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
      if (certMenuRef.current && !certMenuRef.current.contains(e.target as Node)) {
        setShowCertMenu(false)
      }
    }
    if (showEngineMenu || showCertMenu) {
      document.addEventListener('mousedown', handleClickOutside)
    }
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [showEngineMenu, showCertMenu])

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

  let hostname = ''
  try {
    if (activeTab?.url && !activeTab.url.startsWith('about:')) {
      hostname = new URL(activeTab.url).hostname
    }
  } catch {
    hostname = activeTab?.url || ''
  }

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
            aria-label={t('browser.omnibox.currentEngine', { engine: currentEngine.name })}
          >
            <span className="text-primary flex items-center">{getSearchEngineIcon(searchEngine, 'w-3.5 h-3.5')}</span>
            <ChevronDown className="w-3 h-3 opacity-60" />
          </button>

          {showEngineMenu && (
            <div className="absolute left-0 mt-2 w-56 bg-popover/95 backdrop-blur-md border border-border rounded-xl shadow-xl py-1 z-50 animate-scale-in">
              <div className="px-3 py-1.5 text-label-sm font-semibold text-muted-foreground uppercase tracking-wider">
                {t('browser.omnibox.selectEngine')}
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
                  <div className="w-4 h-4 flex items-center justify-center shrink-0 text-primary">
                    {getSearchEngineIcon(engine.id, 'w-4 h-4')}
                  </div>
                  <div className="truncate">
                    <div className="font-medium leading-tight">{engine.name}</div>
                    <div className="text-label-sm text-muted-foreground truncate">{engine.description}</div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* SSL- / Zertifikats-Icon mit interaktivem Status-Popover */}
        <div className="relative shrink-0 mr-1.5" ref={certMenuRef}>
          {activeTab?.url?.startsWith('about:') ? (
            <div className="p-1 text-muted-foreground/60">
              <Search className="w-3.5 h-3.5" />
            </div>
          ) : (
            <button
              onClick={() => setShowCertMenu(!showCertMenu)}
              className="p-1 rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors flex items-center"
              aria-label={isHttps ? t('browser.certificate.connectionSecure') : t('browser.certificate.connectionInsecure')}
            >
              {isHttps ? (
                <Lock className="w-3.5 h-3.5 text-status-success" />
              ) : (
                <AlertTriangle className="w-3.5 h-3.5 text-status-warning" />
              )}
            </button>
          )}

          {showCertMenu && !activeTab?.url?.startsWith('about:') && (
            <div className="absolute left-0 mt-2 w-80 bg-popover/95 backdrop-blur-md border border-border rounded-xl shadow-2xl p-4 z-50 text-foreground animate-scale-in">
              <div className="flex items-start gap-2.5 pb-3 border-b border-border">
                {isHttps ? (
                  <div className="w-8 h-8 rounded-lg bg-status-success/15 text-status-success flex items-center justify-center shrink-0 mt-0.5">
                    <ShieldCheck className="w-4 h-4" />
                  </div>
                ) : (
                  <div className="w-8 h-8 rounded-lg bg-status-warning/15 text-status-warning flex items-center justify-center shrink-0 mt-0.5">
                    <AlertTriangle className="w-4 h-4" />
                  </div>
                )}
                <div className="min-w-0 flex-1">
                  <div className="font-semibold text-xs leading-tight">
                    {isHttps
                      ? t('browser.certificate.connectionSecure')
                      : t('browser.certificate.connectionInsecure')}
                  </div>
                  <div className="text-label-sm text-muted-foreground truncate mt-0.5 font-mono">
                    {hostname}
                  </div>
                </div>
              </div>

              <div className="py-2.5 space-y-2.5 text-xs">
                <div className="flex items-start gap-2">
                  <CheckCircle2 className="w-3.5 h-3.5 text-status-success shrink-0 mt-0.5" />
                  <div>
                    <div className="font-medium text-foreground">
                      {isHttps ? t('browser.certificate.certValid') : t('browser.certificate.connectionInsecure')}
                    </div>
                    <div className="text-label-sm text-muted-foreground leading-relaxed">
                      {isHttps
                        ? t('browser.certificate.certValidDesc')
                        : 'Unverschlüsselte Verbindung — Daten können im Netzwerk mitgelesen werden.'}
                    </div>
                  </div>
                </div>

                <div className="flex items-start gap-2">
                  <CheckCircle2 className="w-3.5 h-3.5 text-status-success shrink-0 mt-0.5" />
                  <div>
                    <div className="font-medium text-foreground">
                      {t('browser.certificate.protocolTls')}
                    </div>
                    <div className="text-label-sm text-muted-foreground leading-relaxed">
                      {isHttps
                        ? t('browser.certificate.protocolTlsDesc')
                        : 'Keine TLS-Verschlüsselung.'}
                    </div>
                  </div>
                </div>

                <div className="flex items-start gap-2">
                  <CheckCircle2 className="w-3.5 h-3.5 text-status-success shrink-0 mt-0.5" />
                  <div>
                    <div className="font-medium text-foreground">
                      {t('browser.certificate.cookies')}
                    </div>
                    <div className="text-label-sm text-muted-foreground leading-relaxed">
                      {t('browser.certificate.cookiesDesc')}
                    </div>
                  </div>
                </div>
              </div>
            </div>
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
          placeholder={t('browser.omnibox.searchOrEnter', { engine: currentEngine.name })}
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
            aria-label={bookmarked ? t('browser.omnibox.bookmarkRemove') : t('browser.omnibox.bookmarkAdd')}
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
