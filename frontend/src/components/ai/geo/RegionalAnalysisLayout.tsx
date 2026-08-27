import { ReactNode } from 'react'

import type { AiRegionalAnalysis } from '@/api/ai'
import { GlobeViewer } from './GlobeViewer'
import { RegionalInfoPanel } from './RegionalInfoPanel'

interface RegionalAnalysisLayoutProps {
  children: ReactNode
  active: boolean
  data: AiRegionalAnalysis | null
  loading?: boolean
  onClose: () => void
}

/**
 * Layout-Orchestrierer für die regionale Analyseansicht.
 *
 * Normalzustand: Chat nimmt 100 % der Breite ein.
 * Analysezustand:
 * - Chat verschiebt sich nach links
 * - 3D-Globus öffnet sich zentral
 * - Rechtes Informationspanel fährt ein
 */
export function RegionalAnalysisLayout({
  children,
  active,
  data,
  loading,
  onClose,
}: RegionalAnalysisLayoutProps) {
  const coords = data?.coordinates

  return (
    <div className={`flex h-full w-full min-h-0 flex-1 ${active ? 'flex-col lg:flex-row gap-3 overflow-hidden p-2 sm:p-3' : 'flex-col overflow-hidden'} transition-all duration-300 ease-out`}>
      {/* Chat-Container: Behält immer dieselbe Identität im React-Baum */}
      <div className={`flex flex-col min-h-0 h-full w-full ${active ? 'lg:w-[380px] xl:w-[420px] shrink-0 rounded-2xl border border-outline-variant/30 bg-surface-container-lowest/50' : 'flex-1'} overflow-hidden transition-all duration-300`}>
        {children}
      </div>

      {/* Mittlere Spalte: 3D-Globus */}
      {active && (
        <>
          <div className="flex flex-1 min-h-[300px] lg:min-h-0 h-full rounded-2xl overflow-hidden shadow-sm">
            <GlobeViewer
              latitude={coords?.latitude}
              longitude={coords?.longitude}
              locationName={data?.location}
              bbox={coords?.bbox}
              className="h-full w-full"
            />
          </div>

          {/* Rechte Spalte: Informationspanel */}
          <div className="flex flex-col min-h-0 h-full w-full lg:w-[320px] xl:w-[360px] shrink-0">
            <RegionalInfoPanel data={data} loading={loading} onClose={onClose} />
          </div>
        </>
      )}
    </div>
  )
}
