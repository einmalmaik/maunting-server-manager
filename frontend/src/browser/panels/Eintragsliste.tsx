/**
 * Eine Liste von Seiten (Lesezeichen, Verlauf) mit Suche. Ein Klick öffnet die
 * Seite im vorderen Tab, mit gedrückter Strg-Taste oder der mittleren Taste in
 * einem neuen.
 */
import { useState, type ReactNode } from 'react'
import { Globe, Search, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Input, Kurzinfo } from '@/Singra/UI'
import { Zustandsflaeche } from '@/Singra/UI/Zustandsflaeche'

import { useTabsStore } from '../services/tabsStore'
import type { Eintrag } from '../services/verlaufStore'

export function seiteOeffnen(url: string, neuerTab: boolean) {
  const tabs = useTabsStore.getState()
  if (neuerTab) tabs.neuerTab(url, { hintergrund: true })
  else tabs.oeffnen(url)
}

interface Props {
  eintraege: Eintrag[]
  leer: string
  entfernen: (e: Eintrag) => void
  /** Eine Zeile über einem Eintrag, etwa das Datum im Verlauf. */
  ueberschrift?: (e: Eintrag, vorher: Eintrag | undefined) => ReactNode
  kopf?: ReactNode
}

export function Eintragsliste({ eintraege, leer, entfernen, ueberschrift, kopf }: Props) {
  const { t } = useTranslation()
  const [suche, setSuche] = useState('')
  const s = suche.trim().toLowerCase()
  const sichtbar = s ? eintraege.filter((e) => e.url.toLowerCase().includes(s) || e.titel.toLowerCase().includes(s)) : eintraege
  // Mehr zeichnet niemand durch; die Suche findet den Rest.
  const gezeigt = sichtbar.slice(0, 300)

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 flex-col gap-2 p-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 z-10 h-4 w-4 -translate-y-1/2 text-on-surface-variant" aria-hidden="true" />
          <Input value={suche} onChange={(e) => setSuche(e.target.value)} aria-label={t('browser.liste.suchen')} placeholder={t('browser.liste.suchen')} className="pl-9" />
        </div>
        {kopf}
      </div>
      {gezeigt.length === 0 ? (
        <Zustandsflaeche art="leer" text={s ? t('browser.liste.keineTreffer') : leer} ansagen={!!s} />
      ) : (
        <ul className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {gezeigt.map((e, i) => (
            <li key={`${e.url}-${e.zeit}`}>
              {ueberschrift?.(e, gezeigt[i - 1])}
              <div className="group flex items-center gap-1 rounded-md hover:bg-surface-container-high">
                <button
                  type="button"
                  onClick={(ev) => seiteOeffnen(e.url, ev.ctrlKey || ev.metaKey)}
                  onAuxClick={(ev) => ev.button === 1 && seiteOeffnen(e.url, true)}
                  className="flex min-w-0 flex-1 items-center gap-3 px-2 py-2 text-left"
                >
                  <Globe className="h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-body-sm text-on-surface">{e.titel || e.url}</span>
                    <span className="block truncate text-label-sm text-on-surface-variant">{e.url}</span>
                  </span>
                </button>
                <Kurzinfo text={t('browser.liste.entfernen')} seite="ende">
                  <button
                    type="button"
                    onClick={() => entfernen(e)}
                    aria-label={t('browser.liste.entfernenName', { titel: e.titel || e.url })}
                    className="mr-1 flex h-7 w-7 items-center justify-center rounded-md text-on-surface-variant opacity-0 hover:bg-surface-container-highest hover:text-status-destructive focus-visible:opacity-100 group-hover:opacity-100"
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                </Kurzinfo>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
