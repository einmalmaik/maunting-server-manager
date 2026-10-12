/**
 * Die Leiste am Handy, unten wie bei Chrome mit Adressleiste unten:
 * Adresszeile, Schild, Tabs und Menü. Zurück ist die Zurück-Taste des
 * Systems; Vor, Neu laden, Lesezeichen und die Suche in der Seite stehen im
 * Menü (`Modulmenue` mit `handy`). Darunter liegt die Gestenleiste
 * (`--msm-unten-sicher`), die die Leiste freilässt.
 */
import { forwardRef, useState } from 'react'
import { RotateCw, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { useEinstellungenStore } from '../services/einstellungenStore'
import { istIntern } from '../services/intern'
import { useAktiverTab, useTabsStore } from '../services/tabsStore'
import type { AdresszeileGriff } from './Adresszeile'
import { Knopf } from './knopf'
import { Modulmenue } from './Modulmenue'
import { Schild } from './Navigationsleiste'
import { TabUebersicht } from './TabUebersicht'

export const HandyLeiste = forwardRef<AdresszeileGriff>(function HandyLeiste(_, _adresszeile) {
  const { t } = useTranslation()
  const tab = useAktiverTab()
  const aktion = useTabsStore((s) => s.aktion)
  const anzahl = useTabsStore((s) => s.tabs.length)
  const schildAus = useEinstellungenStore((s) => s.ausgeblendet.includes('schild'))
  const [uebersicht, setUebersicht] = useState(false)

  return (
    <div className="shrink-0 border-t border-outline-variant/30 bg-surface-container pb-[var(--msm-unten-sicher)]">
      <div className="flex h-14 items-center justify-around px-2">
        {!schildAus && <Schild />}
        {tab?.laedt ? (
          <Knopf name={t('browser.nav.anhalten')} onClick={() => aktion('anhalten')}>
            <X className="h-4 w-4" aria-hidden="true" />
          </Knopf>
        ) : (
          <Knopf name={t('browser.nav.neuLaden')} onClick={() => aktion('neu_laden')} disabled={!tab?.url || istIntern(tab.url)}>
            <RotateCw className="h-4 w-4" aria-hidden="true" />
          </Knopf>
        )}
        <Knopf name={t('browser.tabs.anzahl', { anzahl })} onClick={() => setUebersicht(true)} aria-haspopup="dialog" seite="ende">
          <span aria-hidden="true" className="flex h-5 min-w-5 items-center justify-center rounded-md border-2 border-current px-0.5 text-label-sm font-semibold tabular-nums">
            {anzahl > 99 ? '99+' : anzahl}
          </span>
        </Knopf>
        <Modulmenue handy />
      </div>
      <TabUebersicht offen={uebersicht} onSchliessen={() => setUebersicht(false)} />
    </div>
  )
})
