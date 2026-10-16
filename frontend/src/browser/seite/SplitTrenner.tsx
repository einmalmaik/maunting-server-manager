/**
 * Interaktiver Split-Trenner (Divider) zwischen zwei Webviews.
 *
 * Unterstützt stufenloses Verschieben per Maus-Drag, Touch-Geste sowie
 * Tastatursteuerung (Pfeiltasten & WASD). Doppelklick setzt das Verhältnis auf 50:50.
 *
 * Implementiert nach modern-web-guidance (Pointer Capture, touch-action: none,
 * -webkit-user-select: none, ARIA role="separator").
 */
import { useEffect, useRef, useState } from 'react'
import { ArrowLeftRight, ArrowUpDown, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { useTabsStore, type SplitRichtung } from '../services/tabsStore'

interface SplitTrennerProps {
  richtung: SplitRichtung
  anteil: number
  onAnteilAendern: (neuerAnteil: number) => void
  containerRef: React.RefObject<HTMLDivElement | null>
}

export function SplitTrenner({ richtung, anteil, onAnteilAendern, containerRef }: SplitTrennerProps) {
  const { t } = useTranslation()
  const trennerRef = useRef<HTMLDivElement>(null)
  const [istAmZiehen, setIstAmZiehen] = useState(false)
  const [hover, setHover] = useState(false)

  const splitTauschen = useTabsStore((s) => s.splitTauschen)
  const splitRichtungUmschalten = useTabsStore((s) => s.splitRichtungUmschalten)
  const splitBeenden = useTabsStore((s) => s.splitBeenden)

  const istHorizontal = richtung === 'horizontal'

  const berechneAnteilAusPointer = (clientX: number, clientY: number) => {
    const container = containerRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    if (istHorizontal) {
      if (rect.width <= 0) return
      const relX = clientX - rect.left
      const neuerAnteil = Math.max(0.2, Math.min(0.8, relX / rect.width))
      onAnteilAendern(neuerAnteil)
    } else {
      if (rect.height <= 0) return
      const relY = clientY - rect.top
      const neuerAnteil = Math.max(0.2, Math.min(0.8, relY / rect.height))
      onAnteilAendern(neuerAnteil)
    }
  }

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    // Wenn auf einen der Aktionsknöpfe geklickt wurde, kein Drag starten
    if ((e.target as HTMLElement).closest('button')) return

    e.preventDefault()
    setIstAmZiehen(true)
    try {
      e.currentTarget.setPointerCapture(e.pointerId)
    } catch {}
  }

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!istAmZiehen) return
    berechneAnteilAusPointer(e.clientX, e.clientY)
  }

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!istAmZiehen) return
    setIstAmZiehen(false)
    try {
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId)
      }
    } catch {}
  }

  const onLostPointerCapture = () => {
    setIstAmZiehen(false)
  }

  const onDoubleClick = (e: React.MouseEvent) => {
    e.preventDefault()
    // Doppelklick setzt das Verhältnis exakt auf 50:50 zurück
    onAnteilAendern(0.5)
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const schritt = e.shiftKey ? 0.05 : 0.02
    let neu = anteil

    if (istHorizontal) {
      if (e.key === 'ArrowLeft' || e.key === 'a' || e.key === 'A') {
        neu = Math.max(0.2, anteil - schritt)
        e.preventDefault()
      } else if (e.key === 'ArrowRight' || e.key === 'd' || e.key === 'D') {
        neu = Math.min(0.8, anteil + schritt)
        e.preventDefault()
      }
    } else {
      if (e.key === 'ArrowUp' || e.key === 'w' || e.key === 'W') {
        neu = Math.max(0.2, anteil - schritt)
        e.preventDefault()
      } else if (e.key === 'ArrowDown' || e.key === 's' || e.key === 'S') {
        neu = Math.min(0.8, anteil + schritt)
        e.preventDefault()
      }
    }

    if (e.key === 'Home' || e.key === '0') {
      neu = 0.5
      e.preventDefault()
    }

    if (neu !== anteil) {
      onAnteilAendern(Math.round(neu * 100) / 100)
    }
  }

  // Mauszeiger während des Ziehens fixieren
  useEffect(() => {
    if (!istAmZiehen) return
    const prevCursor = document.body.style.cursor
    const prevUserSelect = document.body.style.userSelect
    document.body.style.cursor = istHorizontal ? 'col-resize' : 'row-resize'
    document.body.style.userSelect = 'none'
    return () => {
      document.body.style.cursor = prevCursor
      document.body.style.userSelect = prevUserSelect
    }
  }, [istAmZiehen, istHorizontal])

  const prozent = Math.round(anteil * 100)

  return (
    <div
      ref={trennerRef}
      role="separator"
      tabIndex={0}
      aria-orientation={istHorizontal ? 'vertical' : 'horizontal'}
      aria-valuenow={prozent}
      aria-valuemin={20}
      aria-valuemax={80}
      aria-label={t('browser.split.trennerLabel', {
        prozent,
        defaultValue: `Trenner für geteilte Ansicht (${prozent} %)`,
      })}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onLostPointerCapture={onLostPointerCapture}
      onDoubleClick={onDoubleClick}
      onKeyDown={onKeyDown}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className={`group relative z-30 flex shrink-0 items-center justify-center select-none outline-none focus-visible:ring-2 focus-visible:ring-primary bg-surface-container ${
        istHorizontal
          ? 'h-full w-2 cursor-col-resize touch-none'
          : 'w-full h-2 cursor-row-resize touch-none'
      }`}
      style={{
        WebkitUserSelect: 'none',
        userSelect: 'none',
        touchAction: 'none',
      }}
    >
      {/* Sichtbare Trennlinie (dezent im Ruhezustand, leuchtend bei Hover/Drag) */}
      <div
        className={`rounded-full transition-colors duration-150 ${
          istHorizontal ? 'h-full w-1' : 'w-full h-1'
        } ${
          istAmZiehen || hover
            ? 'bg-primary shadow-sm shadow-primary/40'
            : 'bg-outline-variant/40 hover:bg-primary/70'
        }`}
      />

      {/* Schwebe-Leiste mit Schnell-Aktionen in der Mitte des Trenners */}
      <div
        className={`absolute z-40 flex items-center gap-1 rounded-full border border-outline-variant/40 bg-surface-container-high/95 p-1 shadow-lg backdrop-blur-md transition-all duration-200 ${
          hover || istAmZiehen ? 'opacity-100 scale-100' : 'opacity-0 scale-95 pointer-events-none'
        }`}
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={splitTauschen}
          aria-label={t('browser.split.tauschen', { defaultValue: 'Seiten tauschen' })}
          className="flex h-6 w-6 items-center justify-center rounded-full text-on-surface hover:bg-surface-container-highest transition-colors"
        >
          {istHorizontal ? <ArrowLeftRight className="h-3.5 w-3.5" /> : <ArrowUpDown className="h-3.5 w-3.5" />}
        </button>
        <button
          type="button"
          onClick={splitRichtungUmschalten}
          aria-label={
            istHorizontal
              ? t('browser.split.vertikalDrehen', { defaultValue: 'Übereinander anordnen' })
              : t('browser.split.horizontalDrehen', { defaultValue: 'Nebeneinander anordnen' })
          }
          className="flex h-6 w-6 items-center justify-center rounded-full text-on-surface hover:bg-surface-container-highest transition-colors"
        >
          {istHorizontal ? <ArrowUpDown className="h-3.5 w-3.5" /> : <ArrowLeftRight className="h-3.5 w-3.5" />}
        </button>
        <button
          type="button"
          onClick={splitBeenden}
          aria-label={t('browser.split.schliessen', { defaultValue: 'Geteilte Ansicht beenden' })}
          className="flex h-6 w-6 items-center justify-center rounded-full text-on-surface hover:bg-error/20 hover:text-error transition-colors"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  )
}
