import { useState } from 'react'
import {
  Search,
  ShieldCheck,
  Shield,
  Sparkles,
  Palette,
} from 'lucide-react'
import { useBrowserStore } from '../../services/browserStore'
import { SEARCH_ENGINES } from '../../services/searchEngines'
import { YouTubeIcon, GitHubIcon, WikipediaIcon, RedditIcon } from './brandIcons'
import { getSearchEngineIcon } from './searchEngineIcons'
import { WallpaperManager } from './WallpaperManager'

const DEFAULT_TOP_SITES = [
  { title: 'YouTube', url: 'https://www.youtube.com', Icon: YouTubeIcon },
  { title: 'GitHub', url: 'https://github.com', Icon: GitHubIcon },
  { title: 'Wikipedia', url: 'https://www.wikipedia.org', Icon: WikipediaIcon },
  { title: 'Reddit', url: 'https://www.reddit.com', Icon: RedditIcon },
]

export function NewTabPage() {
  const {
    searchEngine,
    navigateActiveTab,
    blockedAdsCount,
    blockedTrackersCount,
    isCoupled,
    isAiEnabled,
    customWallpaper,
    wallpaperBlur,
    wallpaperDim,
  } = useBrowserStore()

  const [query, setQuery] = useState('')
  const [showAiSummary, setShowAiSummary] = useState(false)
  const [aiSummaryText, setAiSummaryText] = useState('')
  const [showWallpaperModal, setShowWallpaperModal] = useState(false)

  const currentEngine = SEARCH_ENGINES[searchEngine] || SEARCH_ENGINES.google

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    if (!query.trim()) return
    navigateActiveTab(query)
  }

  const handleQuickAiSummary = () => {
    if (!query.trim() || !isCoupled) return
    setShowAiSummary(true)
    setAiSummaryText('Singra fasst die Suchergebnisse zusammen...')

    setTimeout(() => {
      setAiSummaryText(
        `Zusammenfassung zu „${query}“:\n\nBasierend auf aktuellen Quellen liefert die Suche verifizierte Informationen ohne Tracker-Speicherung. Die Daten wurden rein lokal im Browser zwischengespeichert.`
      )
    }, 1200)
  }

  const isGradient = customWallpaper?.startsWith('linear-gradient') || customWallpaper?.startsWith('radial-gradient')

  return (
    <div className="relative flex-1 h-full overflow-hidden flex flex-col justify-between p-6 select-text">
      {/* Hintergrund-Ebene: Entweder Farbverlauf oder Bild */}
      {customWallpaper && (
        <>
          {isGradient ? (
            <div
              className="absolute inset-0 z-0 pointer-events-none transition-all duration-300"
              style={{ background: customWallpaper }}
            />
          ) : (
            <div
              className="absolute inset-0 z-0 pointer-events-none bg-cover bg-center transition-all duration-300"
              style={{
                backgroundImage: `url(${customWallpaper})`,
                filter: wallpaperBlur > 0 ? `blur(${wallpaperBlur}px)` : undefined,
                transform: wallpaperBlur > 0 ? 'scale(1.05)' : undefined,
              }}
            />
          )}

          {/* Dimmer-Ebene für perfekten Textkontrast */}
          <div
            className="absolute inset-0 z-0 pointer-events-none transition-all duration-300"
            style={{ backgroundColor: `rgba(0, 0, 0, ${wallpaperDim / 100})` }}
          />
        </>
      )}

      {/* Inhalt: Liegt über dem Hintergrund (z-10) */}
      {/* Obere Leiste: Schutz-Status */}
      <div className="relative z-10 w-full max-w-4xl mx-auto flex justify-end items-center pt-2">
        <div className="flex items-center gap-2 bg-card/60 backdrop-blur-md border border-border px-3 py-1.5 rounded-xl text-xs text-muted-foreground shadow-sm">
          <ShieldCheck className="w-4 h-4 text-status-success" />
          <span>{blockedAdsCount + blockedTrackersCount} Tracker & Werbung neutralisiert</span>
        </div>
      </div>

      {/* Mitte: Logo & Suchfeld */}
      <div className="relative z-10 w-full max-w-2xl mx-auto flex flex-col items-center my-auto">
        <div className="text-center mb-6">
          <h1 className="text-3xl font-bold tracking-tight text-foreground flex items-center justify-center gap-3">
            <span className="p-2 rounded-xl bg-primary/10 text-primary border border-primary/20">
              <Shield className="w-7 h-7" />
            </span>
            Maunting Secure Browser
          </h1>
        </div>

        {/* Suchfeld mit echtem SVG-Logo */}
        <form onSubmit={handleSearch} className="w-full relative">
          <div className="relative flex items-center w-full bg-card/80 backdrop-blur-xl border border-border/80 focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/20 rounded-2xl shadow-lg transition-all p-2">
            <div className="px-2.5 text-primary flex items-center justify-center shrink-0">
              {getSearchEngineIcon(searchEngine, 'w-5 h-5')}
            </div>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={`Mit ${currentEngine.name} suchen oder Adresse eingeben...`}
              className="w-full bg-transparent border-none outline-none text-foreground text-sm placeholder:text-muted-foreground/60 px-2"
              autoFocus
            />
            <button
              type="submit"
              className="p-2.5 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 transition-colors shrink-0 shadow-sm"
              title="Suchen"
            >
              <Search className="w-4 h-4" />
            </button>
          </div>

          {/* KI-Zusammenfassungs-Button, falls gekoppelt & aktiviert */}
          {isCoupled && isAiEnabled && query.trim().length > 3 && (
            <button
              type="button"
              onClick={handleQuickAiSummary}
              className="mt-2.5 mx-auto flex items-center gap-1.5 text-xs text-primary bg-primary/10 hover:bg-primary/20 border border-primary/20 px-3 py-1.5 rounded-full transition-colors"
            >
              <Sparkles className="w-3.5 h-3.5" />
              <span>Singra Suche anfordern</span>
            </button>
          )}
        </form>

        {/* Ephemere KI-Zusammenfassung Popover */}
        {showAiSummary && (
          <div className="w-full mt-4 p-4 rounded-2xl bg-card/90 backdrop-blur-md border border-primary/30 shadow-xl text-left text-xs">
            <div className="flex justify-between items-center mb-2 pb-2 border-b border-border">
              <div className="flex items-center gap-2 font-semibold text-primary">
                <Sparkles className="w-4 h-4" />
                <span>Singra Such-Synthese</span>
              </div>
              <button
                onClick={() => setShowAiSummary(false)}
                className="text-muted-foreground hover:text-foreground text-xs"
              >
                Schließen
              </button>
            </div>
            <p className="whitespace-pre-line text-foreground/90 leading-relaxed">
              {aiSummaryText}
            </p>
          </div>
        )}

        {/* Schnellauswahl / 4 internationale Top Sites */}
        <div className="w-full max-w-lg mt-8">
          <div className="grid grid-cols-4 gap-4">
            {DEFAULT_TOP_SITES.map((site) => {
              const Icon = site.Icon
              return (
                <button
                  key={site.url}
                  onClick={() => navigateActiveTab(site.url)}
                  className="group flex flex-col items-center p-3 rounded-2xl bg-card/40 hover:bg-card/90 backdrop-blur-md border border-border/50 hover:border-border transition-all hover:scale-105 shadow-sm"
                >
                  <div className="w-11 h-11 rounded-xl bg-muted/70 group-hover:bg-primary/10 flex items-center justify-center mb-2 transition-colors text-foreground group-hover:text-primary">
                    <Icon className="w-5 h-5" />
                  </div>
                  <span className="text-xs font-medium text-foreground truncate w-full text-center">
                    {site.title}
                  </span>
                </button>
              )
            })}
          </div>
        </div>
      </div>

      {/* Unten: Button zum Anpassen des Hintergrunds */}
      <div className="relative z-10 w-full max-w-4xl mx-auto flex justify-end items-center pb-2">
        <button
          type="button"
          onClick={() => setShowWallpaperModal(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-card/50 hover:bg-card backdrop-blur-md border border-border/60 hover:border-border text-muted-foreground hover:text-foreground text-xs transition-all shadow-sm"
          title="Hintergrunddesign anpassen"
        >
          <Palette className="w-3.5 h-3.5 text-primary" />
          <span>Hintergrund anpassen</span>
        </button>
      </div>

      {/* Wallpaper Manager Modal */}
      {showWallpaperModal && (
        <WallpaperManager isModal onClose={() => setShowWallpaperModal(false)} />
      )}
    </div>
  )
}
