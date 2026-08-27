import { Cloud, CloudRain, Compass, ExternalLink, Globe, Satellite, Thermometer, Wind, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { AiRegionalAnalysis } from '@/api/ai'
import { Button } from '@/Singra/UI'

interface RegionalInfoPanelProps {
  data: AiRegionalAnalysis | null
  loading?: boolean
  onClose: () => void
}

/**
 * Modulares rechtes Informationspanel für regionale Analyseergebnisse.
 * Zeigt Satellitendaten, Wetter und Umweltparameter strukturiert an.
 */
export function RegionalInfoPanel({ data, loading, onClose }: RegionalInfoPanelProps) {
  const { t } = useTranslation()

  if (loading && !data) {
    return (
      <div className="flex h-full w-full flex-col p-4 space-y-4 animate-pulse bg-surface-container-low border border-outline-variant/30 rounded-2xl">
        <div className="h-6 w-3/4 bg-surface-container-highest rounded-lg" />
        <div className="h-24 bg-surface-container-highest rounded-xl" />
        <div className="h-36 bg-surface-container-highest rounded-xl" />
      </div>
    )
  }

  if (!data) return null

  const { location, country, coordinates, weather, satellite } = data

  return (
    <aside
      className="flex h-full w-full flex-col overflow-y-auto rounded-2xl border border-outline-variant/30 bg-surface-container-low p-4 text-on-surface space-y-4"
      aria-label={t('ai.geo.panelTitle')}
    >
      {/* Kopfbereich mit Schließen-Knopf */}
      <div className="flex items-start justify-between gap-2 border-b border-outline-variant/30 pb-3">
        <div className="space-y-0.5">
          <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-primary">
            <Compass className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{country || t('ai.geo.region')}</span>
          </div>
          <h2 className="font-headline text-lg font-bold text-on-surface leading-snug">
            {location}
          </h2>
          <p className="text-xs text-on-surface-variant">
            {coordinates.latitude.toFixed(4)}° N, {coordinates.longitude.toFixed(4)}° E
          </p>
        </div>

        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onClose}
          aria-label={t('ai.geo.close')}
          className="h-8 w-8 p-0 shrink-0 text-on-surface-variant hover:text-on-surface"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>

      {/* Wetter-Sektion (Open-Meteo) */}
      {weather && (
        <section className="rounded-xl border border-outline-variant/20 bg-surface-container-lowest/60 p-3.5 space-y-2.5" aria-labelledby="geo-weather-title">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Thermometer className="h-4 w-4 text-amber-400" aria-hidden="true" />
              <h3 id="geo-weather-title" className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('ai.geo.weather')}
              </h3>
            </div>
            <span className="text-xs font-medium text-primary">
              {weather.condition}
            </span>
          </div>

          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-bold tracking-tight text-on-surface">
              {Math.round(weather.temperature_celsius)}°C
            </span>
            <span className="text-xs text-on-surface-variant">
              (Gefühlt {Math.round(weather.apparent_temperature_celsius)}°C)
            </span>
          </div>

          <div className="grid grid-cols-2 gap-2 pt-1 text-xs text-on-surface-variant">
            <div className="flex items-center gap-1.5">
              <Wind className="h-3.5 w-3.5 text-sky-400" aria-hidden="true" />
              <span>{weather.wind_speed_kmh} km/h Wind</span>
            </div>
            <div className="flex items-center gap-1.5">
              <Cloud className="h-3.5 w-3.5 text-indigo-400" aria-hidden="true" />
              <span>{weather.humidity_percent}% Luftfeuchte</span>
            </div>
            {typeof weather.precipitation_mm === 'number' && weather.precipitation_mm > 0 && (
              <div className="flex items-center gap-1.5 col-span-2 text-primary">
                <CloudRain className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{weather.precipitation_mm} mm Niederschlag</span>
              </div>
            )}
          </div>
        </section>
      )}

      {/* Satelliten-Sektion (Copernicus / Sentinel-2) */}
      {satellite?.available && satellite.scenes.length > 0 && (
        <section className="rounded-xl border border-outline-variant/20 bg-surface-container-lowest/60 p-3.5 space-y-3" aria-labelledby="geo-satellite-title">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Satellite className="h-4 w-4 text-teal-400" aria-hidden="true" />
              <h3 id="geo-satellite-title" className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('ai.geo.satelliteData')}
              </h3>
            </div>
            <span className="rounded-full bg-teal-500/10 px-2 py-0.5 text-[11px] font-medium text-teal-400 border border-teal-500/20">
              Copernicus CDSE
            </span>
          </div>

          {satellite.scenes.map((scene) => (
            <div key={scene.id} className="space-y-2 rounded-lg border border-outline-variant/10 bg-surface-container-low/40 p-2.5 text-xs">
              <div className="flex items-center justify-between font-medium">
                <span className="text-on-surface">{scene.mission}</span>
                <span className="text-on-surface-variant">
                  {scene.datetime ? new Date(scene.datetime).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' }) : 'Aktuell'}
                </span>
              </div>

              {typeof scene.cloud_cover_percent === 'number' && (
                <div className="space-y-1">
                  <div className="flex justify-between text-[11px] text-on-surface-variant">
                    <span>Bewölkung</span>
                    <span>{scene.cloud_cover_percent}%</span>
                  </div>
                  <div className="h-1.5 w-full rounded-full bg-surface-container-highest overflow-hidden">
                    <div
                      className="h-full bg-teal-400 rounded-full"
                      style={{ width: `${Math.min(100, scene.cloud_cover_percent)}%` }}
                    />
                  </div>
                </div>
              )}

              {scene.preview_url && (
                <a
                  href={scene.preview_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1 text-[11px] text-primary hover:underline pt-1"
                >
                  <ExternalLink className="h-3 w-3" />
                  <span>Vorschau öffnen</span>
                </a>
              )}
            </div>
          ))}
        </section>
      )}

      {/* Fußzeile mit Rückkehr-Button */}
      <div className="pt-2">
        <Button
          type="button"
          variant="secondary"
          className="w-full justify-center text-xs"
          onClick={onClose}
        >
          {t('ai.geo.backToChat')}
        </Button>
      </div>
    </aside>
  )
}
