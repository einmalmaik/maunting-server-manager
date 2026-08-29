import { ReactNode, useEffect } from 'react'

import type { AiRegionalAnalysis } from '@/api/ai'
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

/**
 * Layout-Orchestrierer für das 3-Spalten-KI-Kommandozentrum (Chat & Voice).
 *
 * Normalzustand: Chat oder Sprachblase nimmt die volle Breite ein.
 * Analysezustand:
 * - Linke Spalte: Chat- / Sprachtranskript mit aktiven Prozessen
 * - Mittlere Spalte: 3D-Globus mit Zielort-Fokussierung & Live-Metriken
 * - Rechte Spalte: RegionalInfoPanel mit Reitern (Übersicht, Satellit, News, Wetter)
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
  const coords = data?.coordinates

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

  return (
    <div
      className={`flex w-full min-h-0 flex-1 ${
        active
          ? 'h-[calc(100dvh-5.5rem)] flex-col gap-3 overflow-hidden p-1 sm:p-2 lg:flex-row'
          : 'h-full flex-col overflow-hidden'
      } transition-all duration-300 ease-out`}
    >
      {/* Linke Spalte (Chat oder Voice-Container) */}
      <div
        className={`order-2 flex min-h-0 w-full flex-col ${
          active
            ? 'order-1 h-[34dvh] shrink-0 rounded-2xl border border-outline-variant/30 bg-surface-container-lowest/50 lg:order-1 lg:h-full lg:w-[380px] xl:w-[420px]'
            : 'order-1 h-full flex-1'
        } overflow-hidden transition-all duration-300`}
      >
        {children}
      </div>

      {/* Mittlere Spalte: 3D-Globus */}
      {active && (
        <>
          <div className="order-2 flex h-[30dvh] min-h-[240px] w-full shrink-0 overflow-hidden rounded-2xl shadow-sm sm:h-[34dvh] lg:order-2 lg:h-full lg:min-h-0 lg:flex-1">
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
          <div className="order-3 flex min-h-0 flex-1 w-full flex-col lg:h-full lg:flex-none lg:w-[340px] xl:w-[380px]">
            <RegionalInfoPanel data={data} news={news} loading={loading} focus={regionalFocus} onClose={onClose} />
          </div>
        </>
      )}
    </div>
  )
}
