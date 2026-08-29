import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Car,
  Check,
  CircleDashed,
  Cloud,
  CloudRain,
  ExternalLink,
  Globe2,
  Newspaper,
  Satellite,
  Share2,
  Thermometer,
  Wind,
  X,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { AiRegionalAnalysis, AiSatelliteLayer } from '@/api/ai'
import { Button } from '@/Singra/UI'
import { MapTilerDetailMap } from './MapTilerDetailMap'
import { hasRegionalCoordinates } from './regionalAnalysis'
import type { RegionalFocus } from '../voice/useSprachsitzung'

type TabType = 'overview' | 'satellite' | 'news' | 'social' | 'traffic' | 'weather'

export interface NewsItem {
  id: string
  title: string
  source: string
  timeAgo: string
  category: string
  url?: string
  snippet?: string
}

type RawNewsItem = Partial<NewsItem> & {
  description?: string
  content?: string
  published_date?: string
}

function plainNewsText(value?: unknown): string {
  return (typeof value === 'string' ? value : '')
    .replace(/<[^>]*>/g, ' ')
    .replace(/\s+/g, ' ')
    .replace(/\s+([,.;:!?])/g, '$1')
    .trim()
}

function safeExternalUrl(value?: string): string | undefined {
  if (!value) return undefined
  try {
    const url = new URL(value)
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.toString() : undefined
  } catch {
    return undefined
  }
}

interface RegionalInfoPanelProps {
  data: AiRegionalAnalysis | null
  news?: NewsItem[]
  loading?: boolean
  focus?: RegionalFocus | null
  onClose: () => void
}

/**
 * Modulares Kommandozentren-Panel für regionale Satelliten-, Umwelt- und
 * Aufklärungsdaten mit interaktiven Reitern (Übersicht, Satellit, Nachrichten, Wetter etc.).
 */
export function RegionalInfoPanel({ data, news, loading, focus, onClose }: RegionalInfoPanelProps) {
  if (loading && !data) return <RegionalInfoLoading />
  if (!data || !hasRegionalCoordinates(data)) return loading ? <RegionalInfoLoading /> : null

  return <RegionalInfoContent data={data} news={news} focus={focus} onClose={onClose} />
}

function RegionalInfoLoading() {
  const { t } = useTranslation()
  return (
    <aside
      className="flex h-full w-full flex-col space-y-4 rounded-2xl border border-outline-variant/30 bg-surface-container-low p-4 animate-pulse"
      aria-label={t('ai.geo.panelTitle', 'Regionale Analyse')}
    >
      <div className="h-6 w-3/4 rounded-lg bg-surface-container-highest" />
      <div className="h-10 rounded-xl bg-surface-container-highest" />
      <div className="h-36 rounded-xl bg-surface-container-highest" />
      <div className="h-44 rounded-xl bg-surface-container-highest" />
    </aside>
  )
}

function RegionalInfoContent({ data, news, focus, onClose }: Omit<RegionalInfoPanelProps, 'data' | 'loading'> & { data: AiRegionalAnalysis }) {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<TabType>('overview')
  const [mapTilerAvailable, setMapTilerAvailable] = useState(true)
  const tabRefs = useRef<Record<TabType, HTMLButtonElement | null>>({
    overview: null, satellite: null, news: null, social: null, traffic: null, weather: null,
  })
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!focus) return
    setActiveTab(focus.tab)
    panelRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
  }, [focus])

  const { location, country, coordinates, weather, satellite } = data
  const traffic = data.traffic
  const publicPosts = data.public_posts
  const firstScene = satellite?.scenes?.[0]
  const previewImg = firstScene?.preview_url

  // Sentinel liefert Szenen und Metadaten. Ohne echte Sentinel-Vorschau wird
  // kein anderes Kartenbild als vermeintliche Satellitenszene ausgegeben.
  const layersMap = satellite?.layers || satellite?.scenes?.[0]?.layers
  const availableLayers: AiSatelliteLayer[] = useMemo(() => {
    if (layersMap && Object.keys(layersMap).length > 0) {
      return Object.values(layersMap)
    }
    if (!previewImg) return []
    return [
      {
        id: 'sentinel_scene',
        name: firstScene?.mission || 'Satellitenszene',
        url: previewImg,
        resolution: 'gemäß Szene',
        mission: firstScene?.mission || 'Sentinel',
        description: t('ai.geo.sceneMetadataDescription', 'Aufnahmezeit und Bewölkung stehen in den Szenenmetadaten.'),
      },
    ]
  }, [layersMap, previewImg, firstScene?.mission, t])

function formatSafeDate(val: string | null | undefined, unavailableText: string, opts?: Intl.DateTimeFormatOptions): string {
  if (!val) return unavailableText
  const d = new Date(val)
  if (isNaN(d.getTime())) return unavailableText
  return d.toLocaleDateString(undefined, opts || { day: '2-digit', month: '2-digit' })
}

  const activeLayer = availableLayers[0]

  // Nachrichtenfeed: Text bleibt reiner Text. Fremde HTML/XML-Fragmente werden
  // nicht als Inhalt oder Markup in die Oberfläche übernommen.
  const newsList: NewsItem[] = useMemo(() => {
    const rawNews: RawNewsItem[] = news && news.length > 0 ? news : (data.news || [])
    return rawNews.map((item, index): NewsItem => ({
      id: item.id || item.url || `news-${index}`,
      title: plainNewsText(item.title),
      source: plainNewsText(item.source),
      timeAgo: plainNewsText(item.timeAgo || item.published_date),
      category: plainNewsText(item.category),
      snippet: plainNewsText(item.snippet || item.description || item.content),
      url: safeExternalUrl(item.url),
    }))
  }, [data.news, news])

  const tabs: { id: TabType; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
    { id: 'overview', label: t('ai.geo.tabs.overview', 'Übersicht'), icon: Globe2 },
    { id: 'satellite', label: t('ai.geo.tabs.satellite', 'Satellit'), icon: Satellite },
    { id: 'news', label: t('ai.geo.tabs.news', 'Nachrichten'), icon: Newspaper },
    { id: 'social', label: t('ai.geo.tabs.social', 'Soziale Medien'), icon: Share2 },
    { id: 'traffic', label: t('ai.geo.tabs.traffic', 'Verkehr'), icon: Car },
    { id: 'weather', label: t('ai.geo.tabs.weather', 'Wetter'), icon: Cloud },
  ]

  const selectTab = (tab: TabType) => setActiveTab(tab)
  const handleTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length
    const nextTab = tabs[nextIndex]
    selectTab(nextTab.id)
    tabRefs.current[nextTab.id]?.focus()
  }

  return (
    <aside
      className="flex h-full w-full flex-col overflow-hidden rounded-2xl border border-outline-variant/30 bg-surface-container-low text-on-surface"
      aria-label={t('ai.geo.panelTitle', 'Regionale Analyse')}
    >
      {/* Kopfbereich mit serverseitig bestätigtem Ort */}
      <div className="flex items-start justify-between gap-3 border-b border-outline-variant/30 p-4 shrink-0 bg-surface-container-lowest/60">
        <div className="space-y-1 min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="font-headline text-lg font-bold text-on-surface leading-tight truncate">
              {location}
            </h2>
          </div>
          <div className="flex items-center gap-2 text-xs text-on-surface-variant">
            <span className="font-medium text-primary">{country || t('ai.geo.region', 'Region')}</span>
            <span>•</span>
            <span>
              {Math.abs(coordinates.latitude).toFixed(4)}° {coordinates.latitude >= 0 ? 'N' : 'S'},{' '}
              {Math.abs(coordinates.longitude).toFixed(4)}° {coordinates.longitude >= 0 ? 'E' : 'W'}
            </span>
          </div>
        </div>

        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onClose}
          aria-label={t('ai.geo.close', 'Schließen')}
          className="h-8 w-8 p-0 shrink-0 text-on-surface-variant hover:text-on-surface"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>

      {/* 2. Reiterleiste (Tabs) */}
      <div role="tablist" aria-label={t('ai.geo.tabsLabel', 'Analysebereiche')} className="flex items-center gap-1 border-b border-outline-variant/20 px-3 py-2 overflow-x-auto no-scrollbar shrink-0 bg-surface-container-lowest/30">
        {tabs.map((tab, index) => {
          const Icon = tab.icon
          const isActive = activeTab === tab.id
          return (
            <button
              key={tab.id}
              type="button"
              ref={(element) => { tabRefs.current[tab.id] = element }}
              role="tab"
              id={`regional-tab-${tab.id}`}
              aria-selected={isActive}
              aria-controls={`regional-panel-${tab.id}`}
              tabIndex={isActive ? 0 : -1}
              onClick={() => selectTab(tab.id)}
              onKeyDown={(event) => handleTabKeyDown(event, index)}
              className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors whitespace-nowrap ${
                isActive
                  ? 'bg-primary text-on-primary shadow-sm font-semibold'
                  : 'text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface'
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              <span>{tab.label}</span>
            </button>
          )
        })}
      </div>

      {/* 3. Reiterinhalte (Scrollbar-Container) */}
      <div ref={panelRef} role="tabpanel" id={`regional-panel-${activeTab}`} aria-labelledby={`regional-tab-${activeTab}`} tabIndex={0} className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* TAB: ÜBERSICHT */}
        {activeTab === 'overview' && (
          <div className="space-y-4">
            {/* Satellitenbild-Vorschaukarte mit Layer-Umschaltung */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold uppercase tracking-wider text-on-surface-variant">
                  {t('ai.geo.satelliteData', 'Satellitendaten')}
                </span>
                <span className="text-[11px] text-primary font-medium">{t('ai.geo.mapSource', 'MapTiler-Karte')}</span>
              </div>

              {/* Sentinel beschreibt Szenen; die MapTiler-Karte bleibt unverändert. */}
              <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar pb-1">
                {availableLayers.map((layer) => (
                  <span
                    key={layer.id}
                    className="rounded-lg border border-outline-variant/30 bg-surface-container-high/60 px-2.5 py-1 text-[11px] font-medium text-on-surface-variant whitespace-nowrap"
                  >
                    {layer.name}
                  </span>
                ))}
              </div>

              <div className="relative aspect-video overflow-hidden rounded-xl border border-outline-variant/30 bg-surface-container-lowest">
                {mapTilerAvailable ? (
                  <MapTilerDetailMap
                    latitude={coordinates.latitude}
                    longitude={coordinates.longitude}
                    locationName={location}
                    zoom={12}
                    onUnavailable={() => setMapTilerAvailable(false)}
                  />
                ) : (
                  <div className="flex h-full flex-col items-center justify-center p-6 text-center text-on-surface-variant">
                    <Satellite className="h-7 w-7 text-primary/70" aria-hidden="true" />
                    <p className="mt-2 text-xs font-medium text-on-surface">{t('ai.geo.mapUnavailableTitle', 'Karte nicht verfügbar')}</p>
                    <p className="mt-1 text-xs">{t('ai.geo.mapUnavailableBody', 'MapTiler ist für diese Instanz nicht eingerichtet oder derzeit nicht erreichbar.')}</p>
                  </div>
                )}
                <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-center justify-between gap-2 bg-gradient-to-t from-surface-container-lowest/95 to-transparent px-3 pb-2 pt-7 text-[10px] text-on-surface">
                  <span>{t('ai.geo.mapSource', 'MapTiler-Karte')}</span>
                  <span>{formatSafeDate(firstScene?.datetime, t('ai.geo.captureTimeUnknown', 'Aufnahmezeit unbekannt'))}</span>
                </div>
              </div>
            </div>

            {/* Aktuelle Nachrichten Feed */}
            <div className="space-y-2.5">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold uppercase tracking-wider text-on-surface-variant">
                  {t('ai.geo.currentNews', 'Aktuelle Nachrichten')}
                </span>
                <span className="text-[11px] text-primary">{t('ai.geo.newsCount', { count: newsList.length, defaultValue: '{{count}} Berichte' })}</span>
              </div>

              <div className="space-y-2">
                {newsList.length === 0 && (
                  <div className="rounded-xl border border-dashed border-outline-variant/30 bg-surface-container-lowest/70 p-3 text-center">
                    <p className="text-xs font-medium text-on-surface">{t('ai.geo.newsUnavailableTitle', 'Keine Nachrichten verfügbar')}</p>
                    <p className="mt-1 text-xs text-on-surface-variant">{t('ai.geo.newsUnavailableBody', 'Für diese Region ist keine Nachrichtenquelle eingerichtet.')}</p>
                  </div>
                )}
                {newsList.map((item) => (
                  <div
                    key={item.id}
                    className="rounded-xl border border-outline-variant/20 bg-surface-container-lowest/70 p-3 space-y-1.5 transition-colors hover:border-primary/40"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="rounded-md bg-primary/10 px-2 py-0.5 text-[10px] font-semibold text-primary border border-primary/20">
                        {item.category}
                      </span>
                      <span className="text-[11px] text-on-surface-variant">{item.timeAgo}</span>
                    </div>
                    <h4 className="text-xs font-semibold text-on-surface leading-snug">
                      {item.title}
                    </h4>
                    {item.snippet && (
                      <p className="text-[11px] text-on-surface-variant line-clamp-2">
                        {item.snippet}
                      </p>
                    )}
                    <div className="flex items-center justify-between pt-1 text-[10px] text-on-surface-variant">
                      <span>{item.source}</span>
                      {item.url && (
                        <a
                          href={item.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-1 text-primary hover:underline"
                        >
                          <span>{t('ai.geo.readMore', 'Quelle')}</span>
                          <ExternalLink className="h-2.5 w-2.5" />
                        </a>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Wetter & Umwelt Kompakt */}
            {weather && (
              <div className="rounded-xl border border-outline-variant/20 bg-surface-container-lowest/70 p-3.5 space-y-2.5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Thermometer className="h-4 w-4 text-primary" />
                    <span className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                      {t('ai.geo.weather', 'Wetter & Klima')}
                    </span>
                  </div>
                  <span className="text-xs font-medium text-primary">{weather.condition}</span>
                </div>
                <div className="flex items-baseline gap-2">
                  <span className="text-2xl font-bold text-on-surface">
                    {Math.round(weather.temperature_celsius)}°C
                  </span>
                  <span className="text-xs text-on-surface-variant">
                    ({t('ai.geo.apparentTemp', { temp: Math.round(weather.apparent_temperature_celsius) })})
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-2 pt-1 text-xs text-on-surface-variant">
                  <div className="flex items-center gap-1.5">
                    <Wind className="h-3.5 w-3.5 text-primary" />
                    <span>{t('ai.geo.windSpeed', { speed: weather.wind_speed_kmh })}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Cloud className="h-3.5 w-3.5 text-primary" />
                    <span>{t('ai.geo.humidity', { percent: weather.humidity_percent })}</span>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* TAB: SATELLIT */}
        {activeTab === 'satellite' && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('ai.geo.scenes', 'Satelliten-Layer & Szenen')}
              </span>
              <span className="rounded-full border border-primary/20 bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                {t('ai.geo.sceneMetadata', 'Szenenmetadaten')}
              </span>
            </div>

            {mapTilerAvailable && data.coordinates && (
              <div className="relative aspect-video overflow-hidden rounded-xl border border-primary/25 bg-surface-container-lowest">
                <MapTilerDetailMap
                  latitude={data.coordinates.latitude}
                  longitude={data.coordinates.longitude}
                  locationName={data.location}
                  zoom={13}
                  onUnavailable={() => setMapTilerAvailable(false)}
                />
              </div>
            )}

            {/* Layer-Karten */}
            <div className="space-y-3">
              {availableLayers.map((layer) => {
                const isPrimary = layer.id === activeLayer?.id
                return (
                  <div
                    key={layer.id}
                    className={`space-y-3 rounded-xl border p-3.5 text-xs transition-all ${
                      isPrimary
                        ? 'border-primary/40 bg-surface-container-high/80 shadow-sm'
                        : 'border-outline-variant/20 bg-surface-container-lowest/80 hover:border-outline-variant/40'
                    }`}
                  >
                    <div className="flex items-center justify-between font-medium">
                      <span className="text-on-surface font-semibold">{layer.name}</span>
                      <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-mono text-primary">
                        {layer.resolution || '10m'}
                      </span>
                    </div>

                    {layer.description && (
                      <p className="text-[11px] text-on-surface-variant leading-relaxed">
                        {layer.description}
                      </p>
                    )}

                    {/* MapTiler ist die einzige Bildfläche, sobald es verfügbar
                        ist. Sentinel bleibt als präzise Mess-/Szenenquelle
                        darunter, ohne ein zweites, unscharfes Bild zu zeigen. */}
                    {!mapTilerAvailable && (
                      <p className="rounded-lg border border-outline-variant/20 bg-surface-container-lowest/60 p-2.5 text-[11px] leading-relaxed text-on-surface-variant">
                        {t('ai.geo.sceneAvailableWithoutMap', 'Szenenmetadaten sind verfügbar. Die interaktive Karte benötigt eine erreichbare MapTiler-Konfiguration.')}
                      </p>
                    )}

                    {layer.url && (
                      <div className="flex justify-end pt-1">
                        <a
                          href={layer.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-1 text-[11px] text-primary hover:underline"
                        >
                          <span>{t('ai.geo.openFullScene', 'HD-Export')}</span>
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      </div>
                    )}
                  </div>
                )
              })}

              {/* Zusätzliche CDSE Szenen falls geladen */}
              {satellite?.scenes && satellite.scenes.length > 0 && (
                <div className="pt-2 space-y-2">
                  <span className="text-[11px] font-semibold text-on-surface-variant uppercase tracking-wider">
                    {t('ai.geo.overflightMetadata', 'Copernicus-CDSE-Überflugsdaten')}
                  </span>
                  {satellite.scenes.map((scene) => (
                    <div key={scene.id} className="rounded-lg border border-outline-variant/20 bg-surface-container-lowest/60 p-2.5 text-[11px] space-y-1">
                      <div className="flex justify-between font-medium text-on-surface">
                        <span>{scene.mission}</span>
                        <span>{formatSafeDate(scene.datetime, t('ai.geo.captureTimeUnknown', 'Aufnahmezeit unbekannt'), { day: 'numeric', month: 'short', year: 'numeric' })}</span>
                      </div>
                      {typeof scene.cloud_cover_percent === 'number' && (
                        <div className="text-on-surface-variant flex justify-between text-[10px]">
                          <span>{t('ai.geo.cloudCover', 'Bewölkung')}</span>
                          <span>{scene.cloud_cover_percent}%</span>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* TAB: NACHRICHTEN */}
        {activeTab === 'news' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between text-xs">
              <span className="font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('ai.geo.newsAndWeb', 'Nachrichten & Lageberichte')}
              </span>
              <span className="text-[11px] text-primary">{t('ai.geo.newsCount', { count: newsList.length, defaultValue: '{{count}} Einträge' })}</span>
            </div>

            {newsList.length === 0 && (
              <div className="rounded-xl border border-dashed border-outline-variant/30 bg-surface-container-lowest/80 p-4 text-center">
                <Newspaper className="mx-auto h-5 w-5 text-on-surface-variant/60" aria-hidden="true" />
                <p className="mt-2 text-xs font-medium text-on-surface">{t('ai.geo.newsUnavailableTitle', 'Keine Nachrichten verfügbar')}</p>
                <p className="mt-1 text-xs leading-relaxed text-on-surface-variant">
                  Für diese Region ist keine Nachrichtenquelle eingerichtet. Es werden keine Lageberichte geschätzt.
                </p>
              </div>
            )}

            {newsList.map((item) => (
              <div
                key={item.id}
                className={`rounded-xl border bg-surface-container-lowest/80 p-3.5 space-y-2 transition-colors hover:border-primary/40 ${
                  focus?.tab === 'news' && focus.source && item.url === focus.source
                    ? 'border-primary/70 ring-1 ring-primary/40 shadow-[0_0_20px_hsl(var(--primary)/0.2)]'
                    : 'border-outline-variant/20'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="rounded-md bg-primary/10 px-2 py-0.5 text-[10px] font-semibold text-primary border border-primary/20">
                    {item.category}
                  </span>
                  <span className="text-[11px] text-on-surface-variant">{item.timeAgo}</span>
                </div>
                <h4 className="text-xs font-semibold text-on-surface leading-snug">
                  {item.title}
                </h4>
                {item.snippet && (
                  <p className="text-xs text-on-surface-variant leading-relaxed">
                    {item.snippet}
                  </p>
                )}
                <div className="flex items-center justify-between pt-1 text-[11px] text-on-surface-variant">
                  <span>{item.source}</span>
                  {item.url && (
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 text-primary hover:underline"
                    >
                      <span>{t('ai.geo.readArticle', 'Artikel lesen')}</span>
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* TAB: SOZIALE MEDIEN */}
        {activeTab === 'social' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between text-xs">
              <span className="font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('ai.geo.socialMedia', 'Soziale Medien & Trends')}
              </span>
              <span className="rounded-full border border-outline-variant/30 bg-surface-container-high px-2 py-0.5 text-[11px] font-medium text-on-surface-variant">
                {publicPosts?.status === 'available'
                  ? t('ai.geo.publicPostsUntrusted', 'Öffentliche, unbestätigte Hinweise')
                  : t('ai.geo.publicPostsUnavailableBadge', 'Derzeit nicht verfügbar')}
              </span>
            </div>
            {publicPosts?.status === 'available' && (
              <p className="rounded-lg border border-warning/25 bg-warning/10 px-3 py-2 text-xs leading-relaxed text-on-surface-variant">
                {t('ai.geo.publicPostsNotice', 'Beiträge sind öffentliche, unbestätigte Hinweise und keine Lagebewertung.')}
              </p>
            )}
            {publicPosts?.reddit.length ? (
              <SocialPostList title={t('ai.geo.reddit', 'Reddit')} posts={publicPosts.reddit} type="reddit" highlightedSource={focus?.tab === 'social' ? focus.source : undefined} />
            ) : null}
            {publicPosts?.bluesky.length ? (
              <SocialPostList title={t('ai.geo.bluesky', 'Bluesky')} posts={publicPosts.bluesky} type="bluesky" highlightedSource={focus?.tab === 'social' ? focus.source : undefined} />
            ) : null}
            {(!publicPosts || publicPosts.status === 'unavailable' || (publicPosts.reddit.length === 0 && publicPosts.bluesky.length === 0)) && (
              <RegionalEmptyState
                title={t('ai.geo.socialUnavailableTitle', 'Keine öffentlichen Beiträge verfügbar')}
                body={t('ai.geo.socialUnavailableBody', 'Für diese Region sind derzeit keine öffentlichen Beiträge verfügbar.')}
              />
            )}
          </div>
        )}

        {/* TAB: VERKEHR */}
        {activeTab === 'traffic' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between text-xs">
              <span className="font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('ai.geo.trafficStatus', 'Verkehr & Bewegung')}
              </span>
              <span className="rounded-full border border-outline-variant/30 bg-surface-container-high px-2 py-0.5 text-[11px] font-medium text-on-surface-variant">
                {traffic?.status === 'available'
                  ? t('ai.geo.tomTomTraffic', 'TomTom-Verkehr')
                  : traffic?.status === 'not_configured'
                    ? t('ai.geo.trafficNotConfiguredBadge', 'Nicht eingerichtet')
                    : t('ai.geo.trafficUnavailableBadge', 'Derzeit nicht verfügbar')}
              </span>
            </div>
            {traffic?.status === 'available' ? <TrafficDetails traffic={traffic} /> : (
              <RegionalEmptyState
                title={traffic?.status === 'not_configured'
                  ? t('ai.geo.trafficNotConfiguredTitle', 'Verkehrsquelle nicht eingerichtet')
                  : t('ai.geo.trafficUnavailableTitle', 'Verkehrsdaten derzeit nicht verfügbar')}
                body={traffic?.status === 'not_configured'
                  ? t('ai.geo.trafficNotConfiguredBody', 'Für diese Instanz ist keine TomTom-Verkehrsquelle eingerichtet.')
                  : traffic?.reason
                    ? t(`ai.geo.trafficReasons.${traffic.reason}`, 'TomTom-Verkehrsdaten sind für diese Region derzeit nicht verfügbar.')
                    : t('ai.geo.trafficUnavailableBody', 'TomTom-Verkehrsdaten sind für diese Region derzeit nicht verfügbar.')}
              />
            )}
          </div>
        )}

        {/* TAB: WETTER */}
        {activeTab === 'weather' && weather && (
          <div className="space-y-3">
            <div className="rounded-xl border border-outline-variant/20 bg-surface-container-lowest/80 p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Thermometer className="h-5 w-5 text-primary" />
                  <span className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                    {t('ai.geo.weatherDetails', 'Detaillierte Wetterdaten')}
                  </span>
                </div>
                <span className="text-xs font-semibold text-primary">{weather.condition}</span>
              </div>

              <div className="flex items-baseline gap-3">
                <span className="text-3xl font-extrabold text-on-surface">
                  {Math.round(weather.temperature_celsius)}°C
                </span>
                <span className="text-xs text-on-surface-variant">
                  {t('ai.geo.apparentTemp', { temp: Math.round(weather.apparent_temperature_celsius) })}
                </span>
              </div>

              <div className="grid grid-cols-2 gap-3 pt-2 text-xs text-on-surface-variant border-t border-outline-variant/20">
                <div className="flex items-center gap-2">
                  <Wind className="h-4 w-4 text-primary" />
                  <div>
                    <div className="text-[10px] text-on-surface-variant/70">Windgeschwindigkeit</div>
                    <div className="font-medium text-on-surface">{weather.wind_speed_kmh} km/h</div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Cloud className="h-4 w-4 text-primary" />
                  <div>
                    <div className="text-[10px] text-on-surface-variant/70">Luftfeuchtigkeit</div>
                    <div className="font-medium text-on-surface">{weather.humidity_percent}%</div>
                  </div>
                </div>
                {typeof weather.precipitation_mm === 'number' && (
                  <div className="flex items-center gap-2 col-span-2">
                    <CloudRain className="h-4 w-4 text-primary" />
                    <div>
                      <div className="text-[10px] text-on-surface-variant/70">Niederschlag</div>
                      <div className="font-medium text-on-surface">{weather.precipitation_mm} mm</div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* 4. Fußzeile mit Rückkehr-Button */}
      <div className="p-3 border-t border-outline-variant/30 shrink-0 bg-surface-container-lowest/60">
        <Button
          type="button"
          variant="secondary"
          className="w-full justify-center text-xs py-2"
          onClick={onClose}
        >
          {t('ai.geo.backToChat', 'Zurück zum Chat')}
        </Button>
      </div>
    </aside>
  )
}

function RegionalEmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-xl border border-dashed border-outline-variant/30 bg-surface-container-lowest/80 p-4 text-center">
      <CircleDashed className="mx-auto h-5 w-5 text-on-surface-variant/60" aria-hidden="true" />
      <p className="mt-2 text-xs font-medium text-on-surface">{title}</p>
      <p className="mt-1 text-xs leading-relaxed text-on-surface-variant">{body}</p>
    </div>
  )
}

function SocialPostList({ title, posts, type, highlightedSource }: {
  title: string
  posts: Array<{ title: string; snippet: string; url: string }> | Array<{ author: string; text: string; url: string }>
  type: 'reddit' | 'bluesky'
  highlightedSource?: string
}) {
  const { t } = useTranslation()
  return (
    <section aria-label={title} className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">{title}</h3>
      {posts.map((post) => {
        const isReddit = type === 'reddit'
        const heading = isReddit ? (post as { title: string }).title : (post as { author: string }).author
        const content = isReddit ? (post as { snippet: string }).snippet : (post as { text: string }).text
        return (
          <article key={post.url} className={`rounded-xl border bg-surface-container-lowest/80 p-3 space-y-1.5 ${
            highlightedSource === post.url
              ? 'border-primary/70 ring-1 ring-primary/40 shadow-[0_0_20px_hsl(var(--primary)/0.2)]'
              : 'border-outline-variant/20'
          }`}>
            <h4 className="text-xs font-semibold leading-snug text-on-surface">{heading}</h4>
            <p className="text-xs leading-relaxed text-on-surface-variant">{content}</p>
            <a href={post.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline">
              <span>{t('ai.geo.openPublicPost', 'Beitrag öffnen')}</span>
              <ExternalLink className="h-3 w-3" aria-hidden="true" />
            </a>
          </article>
        )
      })}
    </section>
  )
}

function TrafficDetails({ traffic }: { traffic: NonNullable<AiRegionalAnalysis['traffic']> }) {
  const { t } = useTranslation()
  const metrics = [
    { label: t('ai.geo.currentSpeed', 'Aktuelle Geschwindigkeit'), value: traffic.current_speed_kmh, suffix: 'km/h' },
    { label: t('ai.geo.freeFlowSpeed', 'Freie Geschwindigkeit'), value: traffic.free_flow_speed_kmh, suffix: 'km/h' },
    { label: t('ai.geo.currentTravelTime', 'Aktuelle Fahrzeit'), value: traffic.current_travel_time_seconds, suffix: 's' },
    { label: t('ai.geo.freeFlowTravelTime', 'Freie Fahrzeit'), value: traffic.free_flow_travel_time_seconds, suffix: 's' },
    { label: t('ai.geo.confidence', 'TomTom-Konfidenz'), value: traffic.confidence, suffix: '' },
  ].filter((metric): metric is { label: string; value: number; suffix: string } => typeof metric.value === 'number')

  return (
    <div className="space-y-3">
      {traffic.road_closure === true && (
        <p className="rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs font-medium text-on-surface">
          {t('ai.geo.roadClosure', 'TomTom meldet eine Straßensperrung im abgefragten Bereich.')}
        </p>
      )}
      {metrics.length > 0 ? (
        <div className="grid grid-cols-2 gap-2">
          {metrics.map((metric) => (
            <div key={metric.label} className="rounded-xl border border-outline-variant/20 bg-surface-container-lowest/80 p-3">
              <p className="text-[10px] uppercase tracking-wider text-on-surface-variant">{metric.label}</p>
              <p className="mt-1 text-lg font-semibold text-on-surface">{metric.value}{metric.suffix && <> <span className="text-xs font-medium text-on-surface-variant">{metric.suffix}</span></>}</p>
            </div>
          ))}
        </div>
      ) : <RegionalEmptyState title={t('ai.geo.trafficNoMetricsTitle', 'Keine aktuellen Messwerte')} body={t('ai.geo.trafficNoMetricsBody', 'TomTom hat für diese Abfrage keine Messwerte geliefert.')} />}
      {traffic.road_closure === false && (
        <p className="flex items-center gap-2 text-xs text-on-surface-variant"><Check className="h-3.5 w-3.5 text-success" aria-hidden="true" />{t('ai.geo.noRoadClosure', 'Keine Straßensperrung gemeldet.')}</p>
      )}
    </div>
  )
}
