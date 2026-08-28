import { ReactNode, useEffect } from 'react'

import type { AiRegionalAnalysis } from '@/api/ai'
import { GlobeViewer } from './GlobeViewer'
import { RegionalInfoPanel, type NewsItem } from './RegionalInfoPanel'

interface RegionalAnalysisLayoutProps {
  children: ReactNode
  active: boolean
  data: AiRegionalAnalysis | null
  locationName?: string | null
  news?: NewsItem[]
  loading?: boolean
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
          ? 'h-[calc(100dvh-5.5rem)] flex-col gap-3 overflow-y-auto overscroll-contain p-1 sm:p-2 lg:flex-row lg:overflow-hidden'
          : 'h-full flex-col overflow-hidden'
      } transition-all duration-300 ease-out`}
    >
      {/* Linke Spalte (Chat oder Voice-Container) */}
      <div
        className={`order-2 flex min-h-0 w-full flex-col ${
          active
            ? 'shrink-0 rounded-2xl border border-outline-variant/30 bg-surface-container-lowest/50 lg:order-1 lg:h-full lg:w-[380px] xl:w-[420px]'
            : 'order-1 h-full flex-1'
        } overflow-hidden transition-all duration-300`}
      >
        {children}
      </div>

      {/* Mittlere Spalte: 3D-Globus */}
      {active && (
        <>
          <div className="order-1 flex h-[52dvh] min-h-[360px] w-full shrink-0 overflow-hidden rounded-2xl shadow-sm sm:h-[58dvh] lg:order-2 lg:h-full lg:min-h-0 lg:flex-1">
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
          <div className="order-3 flex w-full shrink-0 flex-col lg:order-3 lg:h-full lg:min-h-0 lg:w-[340px] xl:w-[380px]">
            <RegionalInfoPanel data={data} news={news} loading={loading} onClose={onClose} />
          </div>
        </>
      )}
    </div>
  )
}
