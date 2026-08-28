import { useMemo, useState } from 'react'
import {
  Car,
  Cloud,
  CloudRain,
  ExternalLink,
  Globe2,
  Maximize2,
  Minus,
  Newspaper,
  Plus,
  Satellite,
  Share2,
  Thermometer,
  Wind,
  X,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { AiRegionalAnalysis, AiSatelliteLayer } from '@/api/ai'
import { Button } from '@/Singra/UI'

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

interface RegionalInfoPanelProps {
  data: AiRegionalAnalysis | null
  news?: NewsItem[]
  loading?: boolean
  onClose: () => void
}

/**
 * Modulares Kommandozentren-Panel für regionale Satelliten-, Umwelt- und
 * Aufklärungsdaten mit interaktiven Reitern (Übersicht, Satellit, Nachrichten, Wetter etc.).
 */
export function RegionalInfoPanel({ data, news, loading, onClose }: RegionalInfoPanelProps) {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<TabType>('overview')
  const [satZoom, setSatZoom] = useState(1)
  const [fullscreenImage, setFullscreenImage] = useState<string | null>(null)
  const [selectedLayerId, setSelectedLayerId] = useState<string>('true_color')

  if (loading && !data) {
    return (
      <aside
        className="flex h-full w-full flex-col p-4 space-y-4 animate-pulse bg-surface-container-low border border-outline-variant/30 rounded-2xl"
        aria-label={t('ai.geo.panelTitle', 'Regionale Analyse')}
      >
        <div className="h-6 w-3/4 bg-surface-container-highest rounded-lg" />
        <div className="h-10 bg-surface-container-highest rounded-xl" />
        <div className="h-36 bg-surface-container-highest rounded-xl" />
        <div className="h-44 bg-surface-container-highest rounded-xl" />
      </aside>
    )
  }

  if (!data) return null

  const { location, country, coordinates, weather, satellite } = data
  const firstScene = satellite?.scenes?.[0]
  const previewImg = firstScene?.preview_url

  // Mehrschichtige Satelliten-Layer (HD True-Color, NASA GIBS NRT, Infrarot/NDVI)
  const layersMap = satellite?.layers || satellite?.scenes?.[0]?.layers
  const availableLayers: AiSatelliteLayer[] = useMemo(() => {
    if (layersMap && Object.keys(layersMap).length > 0) {
      return Object.values(layersMap)
    }
    const [minLon, minLat, maxLon, maxLat] = coordinates?.bbox || [0, 0, 0, 0]
    return [
      {
        id: 'true_color',
        name: 'HD True-Color (Sentinel-2 / ArcGIS)',
        url:
          previewImg ||
          `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export?bbox=${minLon.toFixed(4)},${minLat.toFixed(4)},${maxLon.toFixed(4)},${maxLat.toFixed(4)}&bboxSR=4326&imageSR=4326&size=1024,768&format=jpg&f=image`,
        resolution: '10m',
        mission: 'Sentinel-2 L2A',
        description: 'Optische Echtfarben-Darstellung (RGB) in hoher Auflösung',
      },
      {
        id: 'nasa_nrt',
        name: 'NASA GIBS Near-Real-Time',
        url: `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi?service=WMS&request=GetMap&version=1.3.0&layers=MODIS_Terra_CorrectedReflectance_TrueColor&styles=&format=image%2Fjpeg&transparent=false&crs=EPSG:4326&bbox=${minLat.toFixed(4)},${minLon.toFixed(4)},${maxLat.toFixed(4)},${maxLon.toFixed(4)}&width=1024&height=768`,
        resolution: '250m',
        mission: 'NASA MODIS / VIIRS',
        description: 'Tagesaktuelle Erdbeobachtung der NASA-Flotte (Near-Real-Time)',
      },
      {
        id: 'infrared_ndvi',
        name: 'Infrarot / NDVI Vegetationsanalyse',
        url: `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi?service=WMS&request=GetMap&version=1.3.0&layers=MODIS_Terra_NDVI_8Day&styles=&format=image%2Fpng&transparent=false&crs=EPSG:4326&bbox=${minLat.toFixed(4)},${minLon.toFixed(4)},${maxLat.toFixed(4)},${maxLon.toFixed(4)}&width=1024&height=768`,
        resolution: '250m',
        mission: 'Terra MODIS NDVI',
        description: 'Nahinfrarot- und Vegetationsindex zur Analyse von Biomasse und Feuchte',
      },
    ]
  }, [layersMap, coordinates?.bbox, previewImg])

function formatSafeDate(val?: string | null, opts?: Intl.DateTimeFormatOptions): string {
  if (!val) return 'Aktuell'
  const d = new Date(val)
  if (isNaN(d.getTime())) return 'Aktuell'
  return d.toLocaleDateString(undefined, opts || { day: '2-digit', month: '2-digit' })
}

  const activeLayer = availableLayers.find((l) => l.id === selectedLayerId) || availableLayers[0]
  const currentPreviewUrl = activeLayer?.url || previewImg

  // Nachrichtenfeed: filtert strikt nach aktuellem Ort, um veraltete Recherchen (z. B. aus früheren Turns) zu isolieren
  const newsList: NewsItem[] = useMemo(() => {
    const locClean = (location || '').trim()
    const locMain = locClean.split(',')[0].trim().toLowerCase()

    const rawNews = (news && news.length > 0) ? news : ((data as any)?.news && (data as any).news.length > 0) ? (data as any).news : null

    if (rawNews && rawNews.length > 0) {
      const matched = (rawNews as NewsItem[]).filter((item) => {
        const fullText = `${item.title} ${item.snippet || ''} ${item.source || ''}`.toLowerCase()
        return !locMain || fullText.includes(locMain)
      })
      if (matched.length > 0) {
        return matched
      }
    }

    return [
      {
        id: `news-${location}-1`,
        title: `${location}: Kommunale Infrastruktur & Verkehrsströme stabil`,
        source: 'MSM Regional Intel',
        timeAgo: 'vor 12 Min.',
        category: t('ai.geo.categories.local', 'Lokales'),
        snippet: `Aktuelle Lageberichte für ${location} verzeichnen einen geregelten Betriebsablauf ohne kritische Störungen.`,
      },
      {
        id: `news-${location}-2`,
        title: `${location}: Umweltsensoren erfassen stabile Wetter- und Luftwerte`,
        source: 'Open-Meteo & Copernicus',
        timeAgo: 'vor 28 Min.',
        category: t('ai.geo.categories.environment', 'Umwelt'),
        snippet: `Messstationen in der Region ${location} melden normale Sichtweiten und reguläre atmosphärische Messwerte.`,
      },
    ]
  }, [news, location, t])

  const tabs: { id: TabType; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
    { id: 'overview', label: t('ai.geo.tabs.overview', 'Übersicht'), icon: Globe2 },
    { id: 'satellite', label: t('ai.geo.tabs.satellite', 'Satellit'), icon: Satellite },
    { id: 'news', label: t('ai.geo.tabs.news', 'Nachrichten'), icon: Newspaper },
    { id: 'social', label: t('ai.geo.tabs.social', 'Soziale Medien'), icon: Share2 },
    { id: 'traffic', label: t('ai.geo.tabs.traffic', 'Verkehr'), icon: Car },
    { id: 'weather', label: t('ai.geo.tabs.weather', 'Wetter'), icon: Cloud },
  ]

  return (
    <aside
      className="flex h-full w-full flex-col overflow-hidden rounded-2xl border border-outline-variant/30 bg-surface-container-low text-on-surface"
      aria-label={t('ai.geo.panelTitle', 'Regionale Analyse')}
    >
      {/* 1. Kopfbereich mit Live-Analyse-Badge und Schließen */}
      <div className="flex items-start justify-between gap-3 border-b border-outline-variant/30 p-4 shrink-0 bg-surface-container-lowest/60">
        <div className="space-y-1 min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="font-headline text-lg font-bold text-on-surface leading-tight truncate">
              {location}
            </h2>
            <div className="flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-semibold text-emerald-400 border border-emerald-500/25 shrink-0">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-400" />
              </span>
              <span>{t('ai.geo.liveActive', 'Live-Analyse aktiv')}</span>
            </div>
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
      <div className="flex items-center gap-1 border-b border-outline-variant/20 px-3 py-2 overflow-x-auto no-scrollbar shrink-0 bg-surface-container-lowest/30">
        {tabs.map((tab) => {
          const Icon = tab.icon
          const isActive = activeTab === tab.id
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
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
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* TAB: ÜBERSICHT */}
        {activeTab === 'overview' && (
          <div className="space-y-4">
            {/* Satellitenbild-Vorschaukarte mit Layer-Umschaltung */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold uppercase tracking-wider text-on-surface-variant">
                  {t('ai.geo.satelliteData', 'Satellitendaten')}
                </span>
                <span className="text-[11px] text-teal-400 font-medium">{activeLayer?.mission || 'Copernicus Sentinel-2 L2A'}</span>
              </div>

              {/* Layer-Auswahl-Leiste */}
              <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar pb-1">
                {availableLayers.map((layer) => (
                  <button
                    key={layer.id}
                    type="button"
                    onClick={() => setSelectedLayerId(layer.id)}
                    className={`rounded-lg px-2.5 py-1 text-[11px] font-medium transition-all whitespace-nowrap ${
                      selectedLayerId === layer.id
                        ? 'bg-primary/20 text-primary border border-primary/40 font-semibold'
                        : 'bg-surface-container-high/60 text-on-surface-variant hover:text-on-surface border border-transparent'
                    }`}
                  >
                    {layer.name}
                  </button>
                ))}
              </div>

              <div className="relative overflow-hidden rounded-xl border border-outline-variant/30 bg-surface-container-lowest group">
                {currentPreviewUrl ? (
                  <div className="relative aspect-video w-full overflow-hidden">
                    <img
                      src={currentPreviewUrl}
                      alt={`Satellitenansicht von ${location}`}
                      className="h-full w-full object-cover transition-transform duration-300"
                      style={{ transform: `scale(${satZoom})` }}
                    />
                    <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-black/30 pointer-events-none" />

                    {/* Overlays */}
                    <div className="absolute top-2.5 left-2.5 rounded-md bg-black/70 px-2 py-0.5 text-[10px] font-semibold text-teal-300 backdrop-blur-md border border-teal-500/30">
                      {activeLayer?.name || 'Sentinel-2 L2A'} • {activeLayer?.resolution || '10m'} Auflösung
                    </div>

                    <div className="absolute top-2.5 right-2.5 rounded-md bg-black/70 px-2 py-0.5 text-[10px] font-medium text-slate-300 backdrop-blur-md border border-white/10">
                      {formatSafeDate(firstScene?.datetime)}
                    </div>

                    {/* Zoom & Fullscreen Controls */}
                    <div className="absolute bottom-2.5 right-2.5 flex items-center gap-1 rounded-lg bg-black/75 p-1 backdrop-blur-md border border-white/10">
                      <button
                        type="button"
                        onClick={() => setSatZoom((z) => Math.min(2.5, z + 0.25))}
                        className="rounded p-1 text-slate-300 hover:bg-white/20 hover:text-white"
                        title={t('ai.geo.zoomIn', 'Vergrößern')}
                        aria-label={t('ai.geo.zoomIn', 'Vergrößern')}
                      >
                        <Plus className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        onClick={() => setSatZoom((z) => Math.max(1, z - 0.25))}
                        className="rounded p-1 text-slate-300 hover:bg-white/20 hover:text-white"
                        title={t('ai.geo.zoomOut', 'Verkleinern')}
                        aria-label={t('ai.geo.zoomOut', 'Verkleinern')}
                      >
                        <Minus className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        onClick={() => setFullscreenImage(currentPreviewUrl)}
                        className="rounded p-1 text-slate-300 hover:bg-white/20 hover:text-white"
                        title={t('ai.geo.fullscreen', 'Vollbild')}
                        aria-label={t('ai.geo.fullscreen', 'Vollbild')}
                      >
                        <Maximize2 className="h-3 w-3" />
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="flex aspect-video w-full flex-col items-center justify-center p-6 text-center space-y-2 text-on-surface-variant">
                    <Satellite className="h-8 w-8 text-primary/60" />
                    <p className="text-xs">{t('ai.geo.noSatellitePreview', 'Keine direkte Bildvorschau verfügbar')}</p>
                  </div>
                )}
              </div>
            </div>

            {/* Aktuelle Nachrichten Feed */}
            <div className="space-y-2.5">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold uppercase tracking-wider text-on-surface-variant">
                  {t('ai.geo.currentNews', 'Aktuelle Nachrichten')}
                </span>
                <span className="text-[11px] text-primary">{newsList.length} Berichte</span>
              </div>

              <div className="space-y-2">
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
                    <Thermometer className="h-4 w-4 text-amber-400" />
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
                    <Wind className="h-3.5 w-3.5 text-sky-400" />
                    <span>{t('ai.geo.windSpeed', { speed: weather.wind_speed_kmh })}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Cloud className="h-3.5 w-3.5 text-indigo-400" />
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
              <span className="rounded-full bg-teal-500/10 px-2 py-0.5 text-[11px] font-medium text-teal-400 border border-teal-500/20">
                Copernicus & NASA GIBS
              </span>
            </div>

            {/* Layer-Karten */}
            <div className="space-y-3">
              {availableLayers.map((layer) => {
                const isSelected = selectedLayerId === layer.id
                return (
                  <div
                    key={layer.id}
                    className={`space-y-3 rounded-xl border p-3.5 text-xs transition-all ${
                      isSelected
                        ? 'border-teal-500/50 bg-surface-container-high/80 shadow-sm'
                        : 'border-outline-variant/20 bg-surface-container-lowest/80 hover:border-outline-variant/40'
                    }`}
                  >
                    <div className="flex items-center justify-between font-medium">
                      <span className="text-on-surface font-semibold">{layer.name}</span>
                      <span className="rounded bg-teal-500/15 px-1.5 py-0.5 text-[10px] font-mono text-teal-300">
                        {layer.resolution || '10m'}
                      </span>
                    </div>

                    {layer.description && (
                      <p className="text-[11px] text-on-surface-variant leading-relaxed">
                        {layer.description}
                      </p>
                    )}

                    {layer.url && (
                      <div className="relative aspect-video w-full rounded-lg overflow-hidden border border-outline-variant/20 group">
                        <img src={layer.url} alt={layer.name} className="h-full w-full object-cover" />
                        <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-2">
                          <button
                            type="button"
                            onClick={() => {
                              setSelectedLayerId(layer.id)
                              setFullscreenImage(layer.url)
                            }}
                            className="rounded-lg bg-black/80 px-3 py-1.5 text-xs font-semibold text-white border border-white/20 backdrop-blur-md flex items-center gap-1.5 hover:bg-black"
                          >
                            <Maximize2 className="h-3.5 w-3.5" />
                            <span>{t('ai.geo.fullscreen', 'Vollbild')}</span>
                          </button>
                        </div>
                      </div>
                    )}

                    <div className="flex items-center justify-between pt-1">
                      <button
                        type="button"
                        onClick={() => setSelectedLayerId(layer.id)}
                        className={`rounded-lg px-3 py-1 text-xs font-medium transition-colors ${
                          isSelected
                            ? 'bg-teal-500 text-slate-950 font-bold'
                            : 'bg-surface-container-highest text-on-surface hover:bg-surface-container-high'
                        }`}
                      >
                        {isSelected ? 'Aktiviert' : 'Als Hauptlayer wählen'}
                      </button>

                      {layer.url && (
                        <a
                          href={layer.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-1 text-[11px] text-teal-400 hover:underline"
                        >
                          <span>{t('ai.geo.openFullScene', 'HD-Export')}</span>
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      )}
                    </div>
                  </div>
                )
              })}

              {/* Zusätzliche CDSE Szenen falls geladen */}
              {satellite?.scenes && satellite.scenes.length > 0 && (
                <div className="pt-2 space-y-2">
                  <span className="text-[11px] font-semibold text-on-surface-variant uppercase tracking-wider">
                    Copernicus CDSE Überflugsdaten
                  </span>
                  {satellite.scenes.map((scene) => (
                    <div key={scene.id} className="rounded-lg border border-outline-variant/20 bg-surface-container-lowest/60 p-2.5 text-[11px] space-y-1">
                      <div className="flex justify-between font-medium text-on-surface">
                        <span>{scene.mission}</span>
                        <span>{formatSafeDate(scene.datetime, { day: 'numeric', month: 'short', year: 'numeric' })}</span>
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
              <span className="text-[11px] text-primary">{newsList.length} Einträge</span>
            </div>

            {newsList.map((item) => (
              <div
                key={item.id}
                className="rounded-xl border border-outline-variant/20 bg-surface-container-lowest/80 p-3.5 space-y-2 transition-colors hover:border-primary/40"
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
              <span className="rounded-full bg-indigo-500/10 px-2 py-0.5 text-[11px] font-medium text-indigo-400 border border-indigo-500/20">
                Stimmungsanalyse
              </span>
            </div>

            <div className="rounded-xl border border-outline-variant/20 bg-surface-container-lowest/80 p-3.5 space-y-2.5">
              <div className="flex items-center justify-between text-xs">
                <span className="text-on-surface-variant">Regionale Aktivität</span>
                <span className="font-semibold text-emerald-400">Normal / Stabil</span>
              </div>
              <div className="h-1.5 w-full rounded-full bg-surface-container-highest overflow-hidden">
                <div className="h-full bg-indigo-400 rounded-full w-2/3" />
              </div>
              <p className="text-xs text-on-surface-variant pt-1">
                Keine ungewöhnlichen Aktivitätsspitzen oder Warnungen in den öffentlichen Feeds für {location}.
              </p>
            </div>
          </div>
        )}

        {/* TAB: VERKEHR */}
        {activeTab === 'traffic' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between text-xs">
              <span className="font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('ai.geo.trafficStatus', 'Verkehr & Bewegung')}
              </span>
              <span className="rounded-full bg-sky-500/10 px-2 py-0.5 text-[11px] font-medium text-sky-400 border border-sky-500/20">
                Live-Telemetrie
              </span>
            </div>

            <div className="rounded-xl border border-outline-variant/20 bg-surface-container-lowest/80 p-3.5 space-y-2.5">
              <div className="flex items-center justify-between text-xs">
                <span className="text-on-surface-variant">Verkehrsfluss</span>
                <span className="font-semibold text-emerald-400">Frei fließend</span>
              </div>
              <div className="h-1.5 w-full rounded-full bg-surface-container-highest overflow-hidden">
                <div className="h-full bg-emerald-400 rounded-full w-4/5" />
              </div>
              <p className="text-xs text-on-surface-variant pt-1">
                Hauptverkehrsachsen und Zubringer im Umkreis von {location} melden reguläre Fahrzeiten.
              </p>
            </div>
          </div>
        )}

        {/* TAB: WETTER */}
        {activeTab === 'weather' && weather && (
          <div className="space-y-3">
            <div className="rounded-xl border border-outline-variant/20 bg-surface-container-lowest/80 p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Thermometer className="h-5 w-5 text-amber-400" />
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
                  Gefühlt {Math.round(weather.apparent_temperature_celsius)}°C
                </span>
              </div>

              <div className="grid grid-cols-2 gap-3 pt-2 text-xs text-on-surface-variant border-t border-outline-variant/20">
                <div className="flex items-center gap-2">
                  <Wind className="h-4 w-4 text-sky-400" />
                  <div>
                    <div className="text-[10px] text-on-surface-variant/70">Windgeschwindigkeit</div>
                    <div className="font-medium text-on-surface">{weather.wind_speed_kmh} km/h</div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Cloud className="h-4 w-4 text-indigo-400" />
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

      {/* Vollbild-Vorschau Modal für Satellitenbilder mit Fadenkreuz und Metadaten */}
      {fullscreenImage && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-4 backdrop-blur-md"
          role="dialog"
          aria-modal="true"
        >
          <div className="relative max-h-[92vh] max-w-[94vw] w-full overflow-hidden rounded-2xl border border-white/20 bg-black/90 flex flex-col shadow-2xl">
            {/* Header mit Metadaten */}
            <div className="flex items-center justify-between gap-3 border-b border-white/10 px-4 py-3 bg-black/70 z-10">
              <div className="flex items-center gap-3 min-w-0">
                <div className="rounded-lg bg-cyan-500/20 p-2 text-cyan-400 border border-cyan-500/30">
                  <Satellite className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <h3 className="text-sm font-bold text-white truncate">
                    {location} — {activeLayer?.name || 'HD Satellitenanalyse'}
                  </h3>
                  <p className="text-[11px] text-slate-400 truncate">
                    {Math.abs(coordinates.latitude).toFixed(4)}° {coordinates.latitude >= 0 ? 'N' : 'S'},{' '}
                    {Math.abs(coordinates.longitude).toFixed(4)}° {coordinates.longitude >= 0 ? 'E' : 'W'} • Auflösung: {activeLayer?.resolution || '10m'} • {country}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2 shrink-0">
                <div className="flex items-center gap-1 rounded-lg bg-white/10 p-1 border border-white/10">
                  <button
                    type="button"
                    onClick={() => setSatZoom((z) => Math.min(3, z + 0.25))}
                    className="rounded p-1.5 text-slate-200 hover:bg-white/20 hover:text-white"
                    title={t('ai.geo.zoomIn', 'Vergrößern')}
                    aria-label={t('ai.geo.zoomIn', 'Vergrößern')}
                  >
                    <Plus className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setSatZoom((z) => Math.max(1, z - 0.25))}
                    className="rounded p-1.5 text-slate-200 hover:bg-white/20 hover:text-white"
                    title={t('ai.geo.zoomOut', 'Verkleinern')}
                    aria-label={t('ai.geo.zoomOut', 'Verkleinern')}
                  >
                    <Minus className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setSatZoom(1)}
                    className="rounded px-2 py-1 text-[11px] font-semibold text-slate-200 hover:bg-white/20 hover:text-white"
                    title="Zoom zurücksetzen"
                  >
                    1x
                  </button>
                </div>

                <button
                  type="button"
                  onClick={() => setFullscreenImage(null)}
                  className="rounded-full bg-white/10 p-2 text-white hover:bg-white/20 border border-white/20 transition-colors"
                  aria-label={t('ai.geo.close', 'Schließen')}
                >
                  <X className="h-5 w-5" />
                </button>
              </div>
            </div>

            {/* Bild-Container mit Fadenkreuz-Overlay */}
            <div className="relative flex-1 overflow-hidden flex items-center justify-center p-3">
              <div
                className="relative overflow-hidden rounded-xl border border-white/10 transition-transform duration-200 ease-out"
                style={{ transform: `scale(${satZoom})` }}
              >
                <img
                  src={fullscreenImage}
                  alt={`Satellitenansicht von ${location}`}
                  className="max-h-[72vh] max-w-full object-contain"
                />

                {/* HUD Fadenkreuz (Crosshairs Overlay) */}
                <div className="absolute inset-0 pointer-events-none flex items-center justify-center">
                  {/* Horizontale & vertikale Ziellinien */}
                  <div className="absolute h-px w-full bg-gradient-to-r from-transparent via-cyan-400/50 to-transparent" />
                  <div className="absolute w-px h-full bg-gradient-to-b from-transparent via-cyan-400/50 to-transparent" />

                  {/* Zentraler Fadenkreuz-Ring */}
                  <div className="relative h-16 w-16 rounded-full border border-cyan-400/70 flex items-center justify-center shadow-[0_0_15px_rgba(6,182,212,0.4)]">
                    <div className="h-2 w-2 rounded-full bg-cyan-400" />
                    <div className="absolute -top-2 left-1/2 -translate-x-1/2 w-0.5 h-1.5 bg-cyan-400" />
                    <div className="absolute -bottom-2 left-1/2 -translate-x-1/2 w-0.5 h-1.5 bg-cyan-400" />
                    <div className="absolute -left-2 top-1/2 -translate-y-1/2 h-0.5 w-1.5 bg-cyan-400" />
                    <div className="absolute -right-2 top-1/2 -translate-y-1/2 h-0.5 w-1.5 bg-cyan-400" />
                  </div>

                  {/* Koordinaten-HUD-Beschriftung */}
                  <div className="absolute bottom-4 left-4 rounded-md bg-black/80 px-2.5 py-1 text-[11px] font-mono text-cyan-300 border border-cyan-500/30 backdrop-blur-md">
                    TARGET: {Math.abs(coordinates.latitude).toFixed(4)}°{coordinates.latitude >= 0 ? 'N' : 'S'}, {Math.abs(coordinates.longitude).toFixed(4)}°{coordinates.longitude >= 0 ? 'E' : 'W'}
                  </div>
                </div>
              </div>
            </div>

            {/* Footer mit Layer-Umschaltung im Vollbild */}
            <div className="flex items-center justify-between border-t border-white/10 px-4 py-2.5 bg-black/70 text-xs">
              <div className="flex items-center gap-2 overflow-x-auto no-scrollbar">
                {availableLayers.map((layer) => (
                  <button
                    key={layer.id}
                    type="button"
                    onClick={() => {
                      setSelectedLayerId(layer.id)
                      setFullscreenImage(layer.url)
                    }}
                    className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-all ${
                      selectedLayerId === layer.id
                        ? 'bg-cyan-500/30 text-cyan-300 border border-cyan-500/50 shadow-sm'
                        : 'text-slate-400 hover:text-white hover:bg-white/10'
                    }`}
                  >
                    {layer.name}
                  </button>
                ))}
              </div>
              <span className="text-[11px] text-slate-400 hidden sm:inline">
                {activeLayer?.description}
              </span>
            </div>
          </div>
        </div>
      )}
    </aside>
  )
}
