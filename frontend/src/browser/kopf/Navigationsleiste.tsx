/**
 * Zurück, Vor, Neu laden, die Adresszeile, das Schild, der Stern und der
 * Screenshot, dahinter je nach Einstellung das Menü oder die Symbole der
 * Module (`Modulmenue.tsx`). Neu laden, Schild, Stern und Screenshot lassen
 * sich ausblenden.
 *
 * Kurzinfos stehen hier über den Knöpfen (`lage="oben"`): darunter beginnt die
 * Seite, und eine Webview liegt immer über der Oberfläche.
 */
import { forwardRef, useRef, useState } from 'react'
import { ArrowLeft, ArrowLeftRight, ArrowRight, Columns2, RotateCw, ShieldCheck, ShieldOff, Star, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Ankerfenster, Kurzinfo, Switch } from '@/Singra/UI'

import { useEinstellungenStore } from '../services/einstellungenStore'

import { schildPausiert, seitenHost, useGeraetKonfig } from '../services/geraetKonfig'
import { istIntern, istWebseite } from '../services/intern'
import { useAktiverTab, useSplitZustand, useTabsStore } from '../services/tabsStore'
import { useVerlaufStore } from '../services/verlaufStore'
import { Adresszeile, type AdresszeileGriff } from './Adresszeile'
import { AufnahmeKnopf } from '../aufnahme/AufnahmeKnopf'
import { erweiterungenMoeglich } from '../erweiterungen/erweiterungen'
import { ErweiterungenKnopf } from '../erweiterungen/ErweiterungenKnopf'
import { Knopf, KNOPF } from './knopf'
import { Modulmenue, ModuleOben } from './Modulmenue'

export function Schild() {
  const { t } = useTranslation()
  const tab = useAktiverTab()
  const konfig = useGeraetKonfig((s) => s.konfig)
  const aendern = useGeraetKonfig((s) => s.aendern)
  const aktion = useTabsStore((s) => s.aktion)
  const anker = useRef<HTMLButtonElement>(null)
  const [offen, setOffen] = useState(false)

  const host = tab ? seitenHost(tab.url) : null
  const global = konfig?.schild_aktiv ?? true
  const ausnahmen = konfig?.schild_ausnahmen ?? []
  const pausiert = host ? schildPausiert(host, ausnahmen) : false
  const an = global && !pausiert
  const summe = (tab?.werbung ?? 0) + (tab?.tracker ?? 0)
  const name = an ? t('browser.schild.knopf', { anzahl: summe }) : t('browser.schild.knopfAus')

  const umschalten = async (schuetzen: boolean) => {
    if (!host) return
    // Die Ausnahme steht so, wie der Mensch sie sieht: für genau diesen Host.
    const neu = schuetzen ? ausnahmen.filter((a) => !(host === a || host.endsWith(`.${a}`))) : [...ausnahmen, host]
    await aendern({ schild_ausnahmen: neu })
    aktion('neu_laden')
  }

  return (
    <>
      <Kurzinfo text={name} lage="oben" seite="ende">
        <button
          ref={anker}
          type="button"
          onClick={() => setOffen((o) => !o)}
          aria-label={name}
          aria-expanded={offen}
          aria-haspopup="dialog"
          className={`${KNOPF} relative w-auto gap-1 px-2`}
        >
          {an ? <ShieldCheck className="h-4 w-4 text-primary" aria-hidden="true" /> : <ShieldOff className="h-4 w-4" aria-hidden="true" />}
          {an && summe > 0 && <span className="text-label-sm tabular-nums text-on-surface" aria-hidden="true">{summe}</span>}
        </button>
      </Kurzinfo>
      <Ankerfenster offen={offen} onSchliessen={() => setOffen(false)} anker={anker} label={t('browser.schild.titel')} ausrichtung="ende" className="w-72">
        <p className="text-title-sm text-on-surface">{t('browser.schild.titel')}</p>
        {host && <p className="mt-0.5 truncate text-body-sm text-on-surface-variant">{host}</p>}
        <dl className="mt-3 grid grid-cols-2 gap-2">
          <div className="rounded-md bg-surface-container p-2">
            <dt className="text-label-sm text-on-surface-variant">{t('browser.schild.werbung')}</dt>
            <dd className="text-title-md tabular-nums">{tab?.werbung ?? 0}</dd>
          </div>
          <div className="rounded-md bg-surface-container p-2">
            <dt className="text-label-sm text-on-surface-variant">{t('browser.schild.tracker')}</dt>
            <dd className="text-title-md tabular-nums">{tab?.tracker ?? 0}</dd>
          </div>
        </dl>
        {!global ? (
          <p className="mt-3 text-body-sm text-on-surface-variant">{t('browser.schild.globalAus')}</p>
        ) : host ? (
          <div className="mt-3 flex items-center justify-between gap-3">
            <span id="msb-schild-seite" className="text-body-sm">
              {t('browser.schild.aufDieserSeite')}
            </span>
            <Switch aria-labelledby="msb-schild-seite" checked={!pausiert} onCheckedChange={(v) => void umschalten(v)} />
          </div>
        ) : null}
        <p className="mt-3 text-label-sm text-on-surface-variant">{t('browser.schild.hinweis')}</p>
      </Ankerfenster>
    </>
  )
}

export const Navigationsleiste = forwardRef<AdresszeileGriff>(function Navigationsleiste(_, adresszeile) {
  const { t } = useTranslation()
  const tab = useAktiverTab()
  const aktion = useTabsStore((s) => s.aktion)
  const lesezeichen = useVerlaufStore((s) => s.lesezeichen)
  const umschalten = useVerlaufStore((s) => s.lesezeichenUmschalten)

  const ausgeblendet = useEinstellungenStore((s) => s.ausgeblendet)
  const leiste = useEinstellungenStore((s) => s.leiste)

  const webseite = !!tab && istWebseite(tab.url)
  const gemerkt = webseite && lesezeichen.some((l) => l.url === tab!.url)

  const { split, primaerTab, sekundaerTab } = useSplitZustand()
  const splitFokusSetzen = useTabsStore((s) => s.splitFokusSetzen)
  const splitTauschen = useTabsStore((s) => s.splitTauschen)
  const splitBeenden = useTabsStore((s) => s.splitBeenden)

  return (
    <div className="flex h-12 shrink-0 items-center gap-1 bg-surface-container px-2">
      <Knopf name={t('browser.nav.zurueck')} onClick={() => aktion('zurueck')} disabled={!tab?.zurueck && !tab?.vorher}>
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
      </Knopf>
      <Knopf name={t('browser.nav.vor')} onClick={() => aktion('vor')} disabled={!tab?.vor}>
        <ArrowRight className="h-4 w-4" aria-hidden="true" />
      </Knopf>
      {tab?.laedt ? (
        <Knopf name={t('browser.nav.anhalten')} onClick={() => aktion('anhalten')}>
          <X className="h-4 w-4" aria-hidden="true" />
        </Knopf>
      ) : ausgeblendet.includes('neuLaden') ? null : (
        <Knopf name={t('browser.nav.neuLaden')} onClick={() => aktion('neu_laden')} disabled={!tab?.url || istIntern(tab.url)}>
          <RotateCw className="h-4 w-4" aria-hidden="true" />
        </Knopf>
      )}
      <Adresszeile ref={adresszeile} />
      {split && (
        <div className="flex shrink-0 items-center gap-0.5 rounded-lg border border-outline-variant/40 bg-surface-container-high/70 p-0.5">
          <button
            type="button"
            onClick={() => splitFokusSetzen('primaer')}
            title={t('browser.split.fokusPrimaer', { defaultValue: 'Linke Hälfte steuern' })}
            className={`flex items-center gap-1 rounded px-2 py-1 text-label-xs font-medium transition-colors ${
              split.aktiveHaelfte === 'primaer'
                ? 'bg-primary text-on-primary shadow-sm'
                : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest/60'
            }`}
          >
            <Columns2 className="h-3 w-3" />
            <span className="hidden xl:inline max-w-[80px] truncate">{primaerTab?.titel || 'Links'}</span>
            <span className="xl:hidden">1</span>
          </button>
          <button
            type="button"
            onClick={() => splitFokusSetzen('sekundaer')}
            title={t('browser.split.fokusSekundaer', { defaultValue: 'Rechte Hälfte steuern' })}
            className={`flex items-center gap-1 rounded px-2 py-1 text-label-xs font-medium transition-colors ${
              split.aktiveHaelfte === 'sekundaer'
                ? 'bg-primary text-on-primary shadow-sm'
                : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest/60'
            }`}
          >
            <Columns2 className="h-3 w-3" />
            <span className="hidden xl:inline max-w-[80px] truncate">{sekundaerTab?.titel || 'Rechts'}</span>
            <span className="xl:hidden">2</span>
          </button>
          <button
            type="button"
            onClick={splitTauschen}
            title={t('browser.split.tauschen', { defaultValue: 'Seiten tauschen' })}
            className="flex h-6 w-6 items-center justify-center rounded text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest transition-colors"
          >
            <ArrowLeftRight className="h-3 w-3" />
          </button>
          <button
            type="button"
            onClick={splitBeenden}
            title={t('browser.split.schliessen', { defaultValue: 'Geteilte Ansicht beenden' })}
            className="flex h-6 w-6 items-center justify-center rounded text-on-surface-variant hover:text-error hover:bg-error/10 transition-colors"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      )}
      {!ausgeblendet.includes('schild') && <div className="hidden sm:flex shrink-0"><Schild /></div>}
      {!ausgeblendet.includes('stern') && (
        <div className="hidden md:flex shrink-0">
          <Knopf
            name={gemerkt ? t('browser.nav.lesezeichenEntfernen') : t('browser.nav.lesezeichenSetzen')}
            onClick={() => tab && umschalten(tab.url, tab.titel)}
            disabled={!webseite}
            aktiv={gemerkt}
          >
            <Star className={`h-4 w-4 ${gemerkt ? 'fill-primary text-primary' : ''}`} aria-hidden="true" />
          </Knopf>
        </div>
      )}
      {!ausgeblendet.includes('screenshot') && <div className="hidden lg:flex shrink-0"><AufnahmeKnopf /></div>}
      {erweiterungenMoeglich() && !ausgeblendet.includes('erweiterungen') && <div className="hidden lg:flex shrink-0"><ErweiterungenKnopf /></div>}
      {leiste === 'oben' && <ModuleOben />}
      {leiste === 'menue' && <Modulmenue />}
    </div>
  )
})
