/**
 * Die Auswahl der Suchmaschine, auf der Startseite und in den Einstellungen.
 * Oben steht die Werkseinstellung mit dem Namen der Suche, die sie gerade
 * meint; die MSM-Suche steht nur gekoppelt in der Liste.
 */
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown } from 'lucide-react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { useAnkerLage } from '@/Singra/UI/Ankerlage'
import { SuchSymbol } from '../marken'
import { useEinstellungenStore, useWirksameSuche } from './einstellungenStore'
import { SUCHMASCHINEN, suchmaschine, wirksameSuche, type Suchmaschine, type SuchmaschinenId } from './searchEngines'
import { istGekoppelt, useSitzung } from './sitzung'

export const STANDARD = 'standard'

/** Fremde Suchen heißen wie ihre Marke, die eigene in der App-Sprache. */
export function suchName(maschine: Suchmaschine, t: TFunction): string {
  if (maschine.id === 'msm') return t('browser.einstellungen.msmSuche')
  return maschine.name
}

export function useSuchwahl() {
  const { t } = useTranslation()
  const gewaehlt = useEinstellungenStore((s) => s.suchmaschine)
  const setzen = useEinstellungenStore((s) => s.setzen)
  const gekoppelt = useSitzung((s) => istGekoppelt(s.stand))
  const standard = suchmaschine(wirksameSuche(null, gekoppelt))
  const angeboten = SUCHMASCHINEN
  const optionen = [
    { value: STANDARD, label: t('browser.einstellungen.suchStandard', { name: suchName(standard, t) }), icon: <SuchSymbol maschine={standard} /> },
    ...angeboten.map((s) => ({ value: s.id as string, label: suchName(s, t), icon: <SuchSymbol maschine={s} /> })),
  ]
  const normiert = gewaehlt === 'msm' ? 'searxng' : gewaehlt
  return {
    wert: angeboten.some((s) => s.id === normiert) ? (normiert as string) : STANDARD,
    optionen,
    aendern: (wert: string) => setzen({ suchmaschine: wert === STANDARD ? null : (wert as SuchmaschinenId) }),
  }
}

/**
 * Kompakter Suchmaschinen-Auswahlknopf nur mit dem Logo der aktuellen Suche.
 * Integriert direkt in die Suchleiste / Adresszeile; Klick öffnet das Dropdown.
 */
export function SuchwahlKnopf({
  ariaLabel,
  kompakt = false,
  className = '',
}: {
  ariaLabel?: string
  kompakt?: boolean
  className?: string
}) {
  const { t } = useTranslation()
  const suchwahl = useSuchwahl()
  const maschine = suchmaschine(useWirksameSuche())
  const [offen, setOffen] = useState(false)
  const ankerRef = useRef<HTMLButtonElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const lage = useAnkerLage(offen, ankerRef, popupRef, { abstand: 4, seite: 'unten', ausrichtung: 'start' })

  useEffect(() => {
    if (!offen) return
    const onClick = (e: MouseEvent) => {
      const target = e.target as Node
      if (
        ankerRef.current && !ankerRef.current.contains(target) &&
        popupRef.current && !popupRef.current.contains(target)
      ) {
        setOffen(false)
      }
    }
    const onEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        setOffen(false)
        ankerRef.current?.focus()
      }
    }
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onEscape, true)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onEscape, true)
    }
  }, [offen])

  return (
    <>
      <button
        ref={ankerRef}
        type="button"
        onClick={() => setOffen((o) => !o)}
        aria-label={ariaLabel ?? t('browser.start.suchmaschine')}
        aria-haspopup="listbox"
        aria-expanded={offen}
        className={`flex items-center gap-1 rounded-full text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 transition-colors ${
          kompakt ? 'h-7 px-1.5' : 'h-8 pl-2 pr-1.5'
        } ${className}`}
      >
        <span className="flex h-4 w-4 shrink-0 items-center justify-center">
          <SuchSymbol maschine={maschine} />
        </span>
        <ChevronDown className={`h-3 w-3 opacity-60 transition-transform ${offen ? 'rotate-180' : ''}`} aria-hidden="true" />
      </button>

      {offen &&
        createPortal(
          <div
            ref={popupRef}
            style={lage}
            data-msm-dropdown-menu=""
            className="flex min-w-[210px] max-w-[280px] flex-col overflow-hidden rounded-xl border border-outline-variant bg-surface-container-high py-1 shadow-panel z-50 animate-fade-in"
          >
            <div className="px-3 py-1.5 text-label-sm font-semibold text-on-surface-variant border-b border-outline-variant/40">
              {t('browser.start.suchmaschine')}
            </div>
            <ul role="listbox" aria-label={t('browser.start.suchmaschine')} className="max-h-64 overflow-y-auto p-1">
              {suchwahl.optionen.map((opt) => {
                const aktiv = opt.value === suchwahl.wert
                return (
                  <li key={opt.value}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={aktiv}
                      onClick={() => {
                        suchwahl.aendern(opt.value)
                        setOffen(false)
                        ankerRef.current?.focus()
                      }}
                      className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-sm transition-colors ${
                        aktiv
                          ? 'bg-primary/10 text-primary font-medium'
                          : 'text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface'
                      }`}
                    >
                      <span className="flex h-4 w-4 shrink-0 items-center justify-center">{opt.icon}</span>
                      <span className="min-w-0 flex-1 truncate text-label-md">{opt.label}</span>
                      {aktiv && <Check className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />}
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>,
          document.body,
        )}
    </>
  )
}
