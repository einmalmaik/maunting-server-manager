/**
 * Die Einträge in der Navigationsleiste, wenn keine Leiste neben der Seite
 * steht: hinter einem Menüknopf (☰) als Raster mit Namen, oder als kleine
 * Symbole direkt in der Leiste (`leiste` in den Einstellungen).
 *
 * Am Handy (`handy`) steht darüber, was dort keinen eigenen Knopf hat: Vor,
 * Neu laden, Lesezeichen und die Suche in der Seite, wie im Menü von Chrome.
 */
import { useRef, useState } from 'react'
import { ArrowRight, Menu, RotateCw, Search, Star, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Ankerfenster } from '@/Singra/UI'

import { useLeistenEintraege, useLeistenZahlen, useLeistenZiele } from '../leiste/eintraege'
import { EINSTELLUNGEN_EINTRAG, KOPPELN_EINTRAG, type Leisteneintrag } from '../leiste/module'
import { istIntern, istWebseite } from '../services/intern'
import { istGekoppelt, useSitzung } from '../services/sitzung'
import { useSuche } from '../services/suche'
import { useAktiverTab, useTabsStore } from '../services/tabsStore'
import { useVerlaufStore } from '../services/verlaufStore'
import { Knopf, Zahl } from './knopf'

interface Ziel {
  schluessel: string
  eintrag: Pick<Leisteneintrag, 'symbol' | 'name'>
  offen: boolean
  zahl: number
  oeffnen: () => void
}

/** Alles, was Menü und Symbole zeigen, samt Koppeln und Einstellungen am Ende. */
function useZiele(): Ziel[] {
  const stand = useSitzung((s) => s.stand)
  const eintraege = useLeistenEintraege()
  const zahlen = useLeistenZahlen()
  const ziele = useLeistenZiele()
  return [
    ...eintraege.map((e) => ({ schluessel: e.id, eintrag: e, offen: ziele.offen === e.id, zahl: zahlen[e.id] ?? 0, oeffnen: () => ziele.panel(e.id) })),
    ...(!istGekoppelt(stand) && stand !== 'pruefen'
      ? [{ schluessel: 'koppeln', eintrag: KOPPELN_EINTRAG, offen: ziele.offen === 'koppeln', zahl: 0, oeffnen: () => ziele.panel('koppeln') }]
      : []),
    { schluessel: 'einstellungen', eintrag: EINSTELLUNGEN_EINTRAG, offen: ziele.einstellungenOffen, zahl: 0, oeffnen: ziele.einstellungen },
  ]
}

export function ModuleOben() {
  const { t } = useTranslation()
  return (
    <div role="group" aria-label={t('browser.leiste.name')} className="flex shrink-0 items-center">
      {useZiele().map(({ schluessel, eintrag, offen, zahl, oeffnen }) => {
        const Symbol = eintrag.symbol
        const name = t(eintrag.name)
        return (
          <Knopf key={schluessel} name={name} beschriftung={zahl ? t('browser.leiste.mitZahl', { name, zahl }) : name} onClick={oeffnen} aktiv={offen} seite="ende">
            <Symbol className={`h-4 w-4 ${offen ? 'text-primary' : ''}`} aria-hidden="true" />
            {zahl > 0 && <Zahl wert={zahl} />}
          </Knopf>
        )
      })}
    </div>
  )
}

/** Die Knöpfe über dem Raster am Handy; jeder schließt das Menü. */
function HandyAktionen({ schliessen }: { schliessen: () => void }) {
  const { t } = useTranslation()
  const tab = useAktiverTab()
  const aktion = useTabsStore((s) => s.aktion)
  const lesezeichen = useVerlaufStore((s) => s.lesezeichen)
  const umschalten = useVerlaufStore((s) => s.lesezeichenUmschalten)
  const suchen = useSuche((s) => s.oeffnen)
  const webseite = !!tab && istWebseite(tab.url)
  const gemerkt = webseite && lesezeichen.some((l) => l.url === tab!.url)
  const und = (tun: () => void) => () => {
    schliessen()
    tun()
  }
  return (
    <div className="mb-2 flex items-center justify-around border-b border-outline-variant pb-2">
      <Knopf name={t('browser.nav.vor')} onClick={und(() => aktion('vor'))} disabled={!tab?.vor}>
        <ArrowRight className="h-5 w-5" aria-hidden="true" />
      </Knopf>
      <Knopf
        name={gemerkt ? t('browser.nav.lesezeichenEntfernen') : t('browser.nav.lesezeichenSetzen')}
        onClick={und(() => tab && umschalten(tab.url, tab.titel))}
        disabled={!webseite}
        aktiv={gemerkt}
      >
        <Star className={`h-5 w-5 ${gemerkt ? 'fill-primary text-primary' : ''}`} aria-hidden="true" />
      </Knopf>
      <Knopf name={t('browser.suche.feld')} onClick={und(suchen)} disabled={!webseite}>
        <Search className="h-5 w-5" aria-hidden="true" />
      </Knopf>
      {tab?.laedt ? (
        <Knopf name={t('browser.nav.anhalten')} onClick={und(() => aktion('anhalten'))}>
          <X className="h-5 w-5" aria-hidden="true" />
        </Knopf>
      ) : (
        <Knopf name={t('browser.nav.neuLaden')} onClick={und(() => aktion('neu_laden'))} disabled={!tab?.url || istIntern(tab.url)}>
          <RotateCw className="h-5 w-5" aria-hidden="true" />
        </Knopf>
      )}
    </div>
  )
}

export function Modulmenue({ handy = false }: { handy?: boolean }) {
  const { t } = useTranslation()
  const anker = useRef<HTMLButtonElement>(null)
  const [offen, setOffen] = useState(false)
  const ziele = useZiele()
  const summe = ziele.reduce((n, z) => n + z.zahl, 0)
  const name = t('browser.leiste.menue')

  return (
    <>
      <Knopf
        ref={anker}
        name={name}
        beschriftung={summe ? t('browser.leiste.mitZahl', { name, zahl: summe }) : name}
        onClick={() => setOffen((o) => !o)}
        aria-expanded={offen}
        aria-haspopup="dialog"
        seite="ende"
      >
        <Menu className="h-4 w-4" aria-hidden="true" />
        {summe > 0 && <Zahl wert={summe} />}
      </Knopf>
      <Ankerfenster offen={offen} onSchliessen={() => setOffen(false)} anker={anker} label={name} ausrichtung="ende" className="w-80 p-2">
        {handy && <HandyAktionen schliessen={() => setOffen(false)} />}
        <ul className="grid grid-cols-3 gap-1">
          {ziele.map(({ schluessel, eintrag, offen: istOffen, zahl, oeffnen }) => {
            const Symbol = eintrag.symbol
            const titel = t(eintrag.name)
            return (
              <li key={schluessel}>
                <button
                  type="button"
                  aria-pressed={istOffen}
                  aria-label={zahl ? t('browser.leiste.mitZahl', { name: titel, zahl }) : undefined}
                  onClick={() => {
                    setOffen(false)
                    oeffnen()
                  }}
                  className="group flex w-full flex-col items-center gap-1.5 rounded-lg px-1 py-2 text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface"
                >
                  <span
                    className={`relative flex h-10 w-10 items-center justify-center rounded-full ${
                      istOffen ? 'bg-primary/15 text-primary' : 'bg-surface-container text-on-surface group-hover:text-on-surface'
                    }`}
                  >
                    <Symbol className="h-5 w-5" aria-hidden="true" />
                    {zahl > 0 && <Zahl wert={zahl} />}
                  </span>
                  <span className="w-full truncate text-center text-label-sm">{titel}</span>
                </button>
              </li>
            )
          })}
        </ul>
      </Ankerfenster>
    </>
  )
}
