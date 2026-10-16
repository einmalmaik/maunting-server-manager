/**
 * Windows Snap-Assist Overlay für geteilte Ansichten (Split-Viewer).
 *
 * Erkennt das Ziehen von Tabs oder Adressen an die vier Fensterränder
 * (links, rechts, oben, unten) und rendert eine fließend einfliegende Vorschaufläche.
 * Beim Loslassen (Drop) dockt der Tab in der gewählten Ausrichtung an.
 */
import { useEffect, useState } from 'react'
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { entpackeTabTransfer, holeAktivenTabDrag, lauscheTabZiehNachrichten, type TabZiehNachricht } from '../services/tabTransfer'
import { useTabsStore, type SplitRichtung } from '../services/tabsStore'

export type SnapKante = 'links' | 'rechts' | 'oben' | 'unten'

interface SnapZonenOverlayProps {
  containerRef: React.RefObject<HTMLDivElement | null>
}

export function SnapZonenOverlay({ containerRef }: SnapZonenOverlayProps) {
  const { t } = useTranslation()
  const [aktiveKante, setAktiveKante] = useState<SnapKante | null>(null)
  const [dragAktiv, setDragAktiv] = useState(false)

  const aktivId = useTabsStore((s) => s.aktivId)
  const tabs = useTabsStore((s) => s.tabs)
  const splitAktivieren = useTabsStore((s) => s.splitAktivieren)
  const neuerTab = useTabsStore((s) => s.neuerTab)

  const ermittleKanteAusKoordinaten = (clientX: number, clientY: number): SnapKante | null => {
    const el = containerRef.current
    if (!el) return null
    const rect = el.getBoundingClientRect()

    const x = clientX - rect.left
    const y = clientY - rect.top
    const w = rect.width
    const h = rect.height

    if (w <= 0 || h <= 0) return null

    // Schwellenwerte: Mindestens 80px oder 15 % der jeweiligen Kantenbreite
    const schwelleX = Math.max(80, w * 0.15)
    const schwelleY = Math.max(80, h * 0.15)

    if (x <= schwelleX) return 'links'
    if (x >= w - schwelleX) return 'rechts'
    if (y <= schwelleY) return 'oben'
    if (y >= h - schwelleY) return 'unten'

    return null
  }

  // 1. Hören auf plattformübergreifende Tab-Zieh-Nachrichten (Pointer-Drag)
  useEffect(() => {
    return lauscheTabZiehNachrichten((nachricht: TabZiehNachricht) => {
      if (nachricht.art === 'ziehen') {
        setDragAktiv(true)
        if (!containerRef.current) return
        const clientX = typeof nachricht.clientX === 'number' ? nachricht.clientX : nachricht.screenX - window.screenX
        const clientY = typeof nachricht.clientY === 'number' ? nachricht.clientY : nachricht.screenY - window.screenY
        const kante = ermittleKanteAusKoordinaten(clientX, clientY)
        setAktiveKante(kante)
      } else {
        setDragAktiv(false)
        setAktiveKante(null)
      }
    })
  }, [containerRef])

  // 2. Hören auf HTML5 Drag-and-Drop Events
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const onDragOver = (e: DragEvent) => {
      const geteilt = holeAktivenTabDrag()
      const hatTab = e.dataTransfer?.types.includes('application/x-msb-tab') ||
        e.dataTransfer?.types.includes('text/msb-tab') ||
        Boolean(geteilt)

      if (!hatTab) return
      e.preventDefault()
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'

      setDragAktiv(true)
      const kante = ermittleKanteAusKoordinaten(e.clientX, e.clientY)
      setAktiveKante(kante)
    }

    const onDragLeave = (e: DragEvent) => {
      // Wenn der Zeiger den Container komplett verlässt
      if (!container.contains(e.relatedTarget as Node)) {
        setAktiveKante(null)
        setDragAktiv(false)
      }
    }

    const onDrop = (e: DragEvent) => {
      e.preventDefault()
      const kante = ermittleKanteAusKoordinaten(e.clientX, e.clientY)
      setDragAktiv(false)
      setAktiveKante(null)

      if (!kante) return

      let zielTabId: string | null = null
      let url: string | null = null

      const transfer = entpackeTabTransfer(e.dataTransfer)
      if (transfer) {
        zielTabId = transfer.tabId
        url = transfer.url
      } else {
        const geteilt = holeAktivenTabDrag()
        if (geteilt) {
          zielTabId = geteilt.tabId
          url = geteilt.url
        }
      }

      if (!zielTabId && !url) return

      // Wenn der Tab noch nicht in unserer Tabliste existiert (z. B. aus anderem Fenster), legen wir ihn an
      let effektiveTabId = zielTabId
      const existiert = tabs.some((t) => t.id === zielTabId)
      if (!existiert && url) {
        effektiveTabId = neuerTab(url, { hintergrund: true })
      }

      if (!effektiveTabId || effektiveTabId === aktivId) return

      const richtung: SplitRichtung = kante === 'links' || kante === 'rechts' ? 'horizontal' : 'vertikal'
      splitAktivieren(effektiveTabId, richtung, 0.5)

      // Bei Kante 'links' oder 'oben' vertauschen, damit der neue Tab links/oben landet
      if (kante === 'links' || kante === 'oben') {
        useTabsStore.getState().splitTauschen()
      }
    }

    container.addEventListener('dragover', onDragOver)
    container.addEventListener('dragleave', onDragLeave)
    container.addEventListener('drop', onDrop)

    return () => {
      container.removeEventListener('dragover', onDragOver)
      container.removeEventListener('dragleave', onDragLeave)
      container.removeEventListener('drop', onDrop)
    }
  }, [containerRef, tabs, aktivId, splitAktivieren, neuerTab])

  if (!aktiveKante && !dragAktiv) return null

  // Geometrie und Styling für die einfliegende Vorschaufläche
  const vorschauKlassen: Record<SnapKante, string> = {
    links: 'left-2 top-2 bottom-2 w-[calc(50%-12px)]',
    rechts: 'right-2 top-2 bottom-2 w-[calc(50%-12px)]',
    oben: 'left-2 right-2 top-2 h-[calc(50%-12px)]',
    unten: 'left-2 right-2 bottom-2 h-[calc(50%-12px)]',
  }

  const kantenTexte: Record<SnapKante, string> = {
    links: t('browser.split.snapLinks', { defaultValue: 'Links andocken' }),
    rechts: t('browser.split.snapRechts', { defaultValue: 'Rechts andocken' }),
    oben: t('browser.split.snapOben', { defaultValue: 'Oben andocken' }),
    unten: t('browser.split.snapUnten', { defaultValue: 'Unten andocken' }),
  }

  const kantenIcons: Record<SnapKante, React.ReactNode> = {
    links: <ArrowLeft className="h-6 w-6 text-primary animate-pulse" />,
    rechts: <ArrowRight className="h-6 w-6 text-primary animate-pulse" />,
    oben: <ArrowUp className="h-6 w-6 text-primary animate-pulse" />,
    unten: <ArrowDown className="h-6 w-6 text-primary animate-pulse" />,
  }

  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 z-50 overflow-hidden"
    >
      {/* Sanftes Rand-Glow an den 4 Kanten */}
      <div className="absolute inset-0 border border-primary/20 bg-primary/[0.02]" />

      {/* Die animierte Windows Snap-Vorschaufläche */}
      {aktiveKante && (
        <div
          className={`absolute flex flex-col items-center justify-center gap-2 rounded-2xl border-2 border-primary/80 bg-primary/20 shadow-2xl backdrop-blur-md transition-all duration-200 ease-out animate-scale-in ${vorschauKlassen[aktiveKante]}`}
        >
          <div className="flex h-12 w-12 items-center justify-center rounded-full border border-primary/40 bg-surface/80 shadow-md">
            {kantenIcons[aktiveKante]}
          </div>
          <span className="rounded-md bg-surface/90 px-3 py-1 text-label-md font-semibold text-on-surface shadow-sm">
            {kantenTexte[aktiveKante]}
          </span>
        </div>
      )}
    </div>
  )
}
