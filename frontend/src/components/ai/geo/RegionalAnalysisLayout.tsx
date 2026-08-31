import { ReactNode, useEffect, useState } from 'react'
import { Globe2, MessageSquare, Satellite, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { AiRegionalAnalysis } from '@/api/ai'
import { Button } from '@/Singra/UI'
import { GlobeViewer } from './GlobeViewer'
import { RegionalInfoPanel, type NewsItem } from './RegionalInfoPanel'
import type { RegionalFocus } from '../voice/useSprachsitzung'

interface RegionalAnalysisLayoutProps {
  children: ReactNode
  active: boolean
  data: AiRegionalAnalysis | null
  locationName?: string | null
  news?: NewsItem[]
  loading?: boolean
  regionalFocus?: RegionalFocus | null
  onClose: () => void
}

type MobileTab = 'chat' | 'globe' | 'info'

/**
 * Layout-Orchestrierer für das KI-Kommandozentrum (Chat & Voice).
 *
 * Desktop: 3-Spalten-Layout (Chat/Voice links, 3D-Globus mittig, RegionalInfoPanel rechts).
 * Mobile: Reaktionsschnelle Tab-Navigation (Chat, 3D-Globus, Satellitendaten), damit
 * jeder Bereich in voller Höhe scrollbar und interaktiv bedienbar bleibt.
 */
export function RegionalAnalysisLayout({
  children,
  active,
  data,
  locationName,
  news,
  loading,
  regionalFocus,
  onClose,
}: RegionalAnalysisLayoutProps) {
  const { t } = useTranslation()
  const coords = data?.coordinates
  const [mobileTab, setMobileTab] = useState<MobileTab>('globe')

  // Bei speziellem Fokus (z.B. Satelliten-Layer, News, Wetter) mobil direkt zum Infopanel schalten
  useEffect(() => {
    if (!regionalFocus) return
    if (['overview', 'satellite', 'news', 'social', 'traffic', 'weather'].includes(regionalFocus.tab)) {
      setMobileTab('info')
    }
  }, [regionalFocus])

  // Bei neuem Kamerafokus mobil auf den Globus schalten
  useEffect(() => {
    if (data?.camera?.command_id) {
      setMobileTab('globe')
    }
  }, [data?.camera?.command_id])

  // Blendet bei aktiver 3-Spalten-Kommandozentrale die Sidebar aus, um vollen Platz zu bieten
  useEffect(() => {
    if (active) {
      window.dispatchEvent(new CustomEvent('msm:toggle-sidebar', { detail: { hidden: true } }))
    } else {
      window.dispatchEvent(new CustomEvent('msm:toggle-sidebar', { detail: { hidden: false } }))
    }
    return () => {
      window.dispatchEvent(new CustomEvent('msm:toggle-sidebar', { detail: { hidden: false } }))
    }
  }, [active])

  if (!active) {
    return (
      <div className="flex h-full w-full min-h-0 flex-1 flex-col overflow-hidden">
        {children}
      </div>
    )
  }

  return (
    <div className="flex h-full w-full min-h-0 flex-1 flex-col overflow-hidden p-1 sm:p-2 transition-all duration-300 ease-out">
      {/* ── Mobil-Kopfzeile (< lg): Umschalter zwischen Chat, Globus und Satellitendaten ── */}
      <div className="flex shrink-0 items-center justify-between gap-1 rounded-xl border border-outline-variant/30 bg-surface-container-low/95 p-1 mb-2 shadow-sm backdrop-blur-md lg:hidden">
        <div className="flex items-center gap-1 overflow-x-auto no-scrollbar py-0.5">
          <button
            type="button"
            onClick={() => setMobileTab('chat')}
            aria-pressed={mobileTab === 'chat'}
            className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors whitespace-nowrap ${
              mobileTab === 'chat'
                ? 'bg-primary text-on-primary shadow-sm font-semibold'
                : 'text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface'
            }`}
          >
            <MessageSquare className="h-3.5 w-3.5" />
            <span>{t('ai.geo.mobileTabs.chat', 'Chat / Sprache')}</span>
          </button>

          <button
            type="button"
            onClick={() => setMobileTab('globe')}
            aria-pressed={mobileTab === 'globe'}
            className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors whitespace-nowrap ${
              mobileTab === 'globe'
                ? 'bg-primary text-on-primary shadow-sm font-semibold'
                : 'text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface'
            }`}
          >
            <Globe2 className="h-3.5 w-3.5" />
            <span>{t('ai.geo.mobileTabs.globe', '3D-Globus')}</span>
          </button>

          <button
            type="button"
            onClick={() => setMobileTab('info')}
            aria-pressed={mobileTab === 'info'}
            className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors whitespace-nowrap ${
              mobileTab === 'info'
                ? 'bg-primary text-on-primary shadow-sm font-semibold'
                : 'text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface'
            }`}
          >
            <Satellite className="h-3.5 w-3.5" />
            <span>{t('ai.geo.mobileTabs.satellite', 'Satellit & Info')}</span>
          </button>
        </div>

        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onClose}
          aria-label={t('ai.geo.close', 'Schließen')}
          title={t('ai.geo.close', 'Schließen')}
          className="h-8 shrink-0 px-2 rounded-lg text-xs font-medium text-on-surface-variant hover:text-status-danger hover:bg-status-danger/10 border border-outline-variant/30 flex items-center gap-1 transition-colors"
        >
          <X className="h-4 w-4" />
          <span className="hidden sm:inline">{t('ai.geo.close', 'Schließen')}</span>
        </Button>
      </div>

      {/* ── Mobile Inhaltsansicht (< lg): Dauerhaft gemountet, um Scrollposition & Eingabe zu bewahren ── */}
      <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden lg:hidden">
        <div className={`h-full min-h-0 w-full flex-1 flex-col overflow-hidden rounded-2xl border border-outline-variant/30 bg-surface-container-lowest/50 ${mobileTab === 'chat' ? 'flex' : 'hidden'}`}>
          {children}
        </div>

        <div className={`h-full min-h-0 w-full flex-1 overflow-hidden rounded-2xl shadow-sm ${mobileTab === 'globe' ? 'flex' : 'hidden'}`}>
          <GlobeViewer
            data={data}
            latitude={coords?.latitude}
            longitude={coords?.longitude}
            locationName={locationName ?? data?.location}
            bbox={coords?.bbox}
            className="h-full w-full"
          />
        </div>

        <div className={`h-full min-h-0 w-full flex-1 flex-col overflow-hidden ${mobileTab === 'info' ? 'flex' : 'hidden'}`}>
          <RegionalInfoPanel
            data={data}
            news={news}
            loading={loading}
            focus={regionalFocus}
            onClose={onClose}
          />
        </div>
      </div>

      {/* ── Desktop 3-Spalten-Kommandozentrale (>= lg) ── */}
      <div className="hidden h-full min-h-0 w-full flex-1 gap-3 overflow-hidden lg:flex lg:flex-row">
        {/* Linke Spalte: Chat- / Sprachtranskript */}
        <div className="flex h-full min-h-0 w-[380px] xl:w-[420px] shrink-0 flex-col overflow-hidden rounded-2xl border border-outline-variant/30 bg-surface-container-lowest/50">
          {children}
        </div>

        {/* Mittlere Spalte: 3D-Globus */}
        <div className="flex h-full min-h-0 flex-1 overflow-hidden rounded-2xl shadow-sm">
          <GlobeViewer
            data={data}
            latitude={coords?.latitude}
            longitude={coords?.longitude}
            locationName={locationName ?? data?.location}
            bbox={coords?.bbox}
            className="h-full w-full"
          />
        </div>

        {/* Rechte Spalte: Informationspanel */}
        <div className="flex h-full min-h-0 w-[340px] xl:w-[380px] shrink-0 flex-col overflow-hidden">
          <RegionalInfoPanel
            data={data}
            news={news}
            loading={loading}
            focus={regionalFocus}
            onClose={onClose}
          />
        </div>
      </div>
    </div>
  )
}
