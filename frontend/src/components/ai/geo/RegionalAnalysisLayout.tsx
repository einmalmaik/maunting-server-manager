import { ReactNode, useEffect } from 'react'

import type { AiRegionalAnalysis } from '@/api/ai'
import { GlobeViewer } from './GlobeViewer'
import { RegionalInfoPanel, type NewsItem } from './RegionalInfoPanel'

interface RegionalAnalysisLayoutProps {
  children: ReactNode
  active: boolean
  data: AiRegionalAnalysis | null
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
      className={`flex h-full w-full min-h-0 flex-1 ${
        active
          ? 'flex-col lg:flex-row gap-3 overflow-hidden p-1 sm:p-2'
          : 'flex-col overflow-hidden'
      } transition-all duration-300 ease-out`}
    >
      {/* Linke Spalte (Chat oder Voice-Container) */}
      <div
        className={`flex flex-col min-h-0 h-full w-full ${
          active
            ? 'lg:w-[380px] xl:w-[420px] shrink-0 rounded-2xl border border-outline-variant/30 bg-surface-container-lowest/50'
            : 'flex-1'
        } overflow-hidden transition-all duration-300`}
      >
        {children}
      </div>

      {/* Mittlere Spalte: 3D-Globus */}
      {active && (
        <>
          <div className="flex flex-1 min-h-[320px] lg:min-h-0 h-full rounded-2xl overflow-hidden shadow-sm">
            <GlobeViewer
              data={data}
              latitude={coords?.latitude}
              longitude={coords?.longitude}
              locationName={data?.location}
              bbox={coords?.bbox}
              className="h-full w-full"
            />
          </div>

          {/* Rechte Spalte: Informationspanel */}
          <div className="flex flex-col min-h-0 h-full w-full lg:w-[340px] xl:w-[380px] shrink-0">
            <RegionalInfoPanel data={data} news={news} loading={loading} onClose={onClose} />
          </div>
        </>
      )}
    </div>
  )
}
