/**
 * Suchen in der Seite (Strg+F), als Leiste unter der Adresszeile. Die
 * Suchleiste von Edge ist aus (`SuppressDefaultFindDialog`); gesucht wird mit
 * der Suche der WebView2, die alle Treffer markiert und zählt.
 */
import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronUp, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Input, Kurzinfo } from '@/Singra/UI'

import { nativ } from '../services/nativ'
import { useSuche } from '../services/suche'
import { useAktiverTab } from '../services/tabsStore'

const KNOPF =
  'flex h-8 w-8 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface disabled:opacity-40 max-md:h-11 max-md:w-11'

export function Suchleiste() {
  const { t } = useTranslation()
  const offen = useSuche((s) => s.offen)
  const anstoss = useSuche((s) => s.anstoss)
  const schliessen = useSuche((s) => s.schliessen)
  const tab = useAktiverTab()
  const [begriff, setBegriff] = useState('')
  const feld = useRef<HTMLInputElement>(null)
  const id = tab?.id

  useEffect(() => {
    if (offen) feld.current?.select()
  }, [offen, anstoss])

  // Neuer Begriff: neue Suche. Ein anderer Tab oder Schließen beendet sie.
  useEffect(() => {
    if (!offen || !id || !tab?.nativDa) return
    if (begriff) void nativ.tabSuchen(id, 'start', begriff).catch(() => null)
    else void nativ.tabSuchen(id, 'ende').catch(() => null)
    return () => void nativ.tabSuchen(id, 'ende').catch(() => null)
  }, [offen, id, begriff, tab?.nativDa])

  if (!offen) return null
  const treffer = tab?.treffer
  const anzahl = begriff && treffer ? treffer.anzahl : 0
  // Die WebView2 zählt den aktiven Treffer ab 1 (0: noch keiner).
  const stand = begriff ? t('browser.suche.stand', { aktuell: anzahl ? Math.max(1, treffer!.aktuell) : 0, anzahl }) : ''
  const springen = (richtung: 'weiter' | 'zurueck') => id && void nativ.tabSuchen(id, richtung).catch(() => null)

  return (
    <div role="search" className="flex shrink-0 items-center gap-2 border-b border-outline-variant bg-surface-container-low px-3 py-1.5">
      <div className="w-full max-w-sm">
        <Input
          ref={feld}
          aria-label={t('browser.suche.feld')}
          placeholder={t('browser.suche.platzhalter')}
          value={begriff}
          onChange={(e) => setBegriff(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              springen(e.shiftKey ? 'zurueck' : 'weiter')
            } else if (e.key === 'Escape') {
              e.preventDefault()
              schliessen()
            }
          }}
        />
      </div>
      <span className="min-w-16 text-label-sm tabular-nums text-on-surface-variant" aria-live="polite">
        {stand}
      </span>
      <Kurzinfo text={t('browser.suche.zurueck')} lage="oben">
        <button type="button" className={KNOPF} aria-label={t('browser.suche.zurueck')} disabled={!anzahl} onClick={() => springen('zurueck')}>
          <ChevronUp className="h-4 w-4" aria-hidden="true" />
        </button>
      </Kurzinfo>
      <Kurzinfo text={t('browser.suche.weiter')} lage="oben">
        <button type="button" className={KNOPF} aria-label={t('browser.suche.weiter')} disabled={!anzahl} onClick={() => springen('weiter')}>
          <ChevronDown className="h-4 w-4" aria-hidden="true" />
        </button>
      </Kurzinfo>
      <Kurzinfo text={t('browser.suche.schliessen')} lage="oben">
        <button type="button" className={KNOPF} aria-label={t('browser.suche.schliessen')} onClick={schliessen}>
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </Kurzinfo>
    </div>
  )
}
