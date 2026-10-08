/**
 * Zurück, Vor, Neu laden, die Adresszeile, das Schild und der Stern.
 *
 * Kurzinfos stehen hier über den Knöpfen (`lage="oben"`): darunter beginnt die
 * Seite, und eine Webview liegt immer über der Oberfläche.
 */
import { forwardRef, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, RotateCw, ShieldCheck, ShieldOff, Star, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Ankerfenster, Kurzinfo, Switch } from '@/Singra/UI'

import { schildPausiert, seitenHost, useGeraetKonfig } from '../services/geraetKonfig'
import { useAktiverTab, useTabsStore } from '../services/tabsStore'
import { useVerlaufStore } from '../services/verlaufStore'
import { Adresszeile, type AdresszeileGriff } from './Adresszeile'

const KNOPF =
  'flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface disabled:opacity-40 disabled:hover:bg-transparent'

function Knopf({ name, onClick, disabled, children, aktiv }: { name: string; onClick: () => void; disabled?: boolean; children: React.ReactNode; aktiv?: boolean }) {
  return (
    <Kurzinfo text={name} lage="oben">
      <button type="button" onClick={onClick} disabled={disabled} aria-label={name} aria-pressed={aktiv} className={KNOPF}>
        {children}
      </button>
    </Kurzinfo>
  )
}

function Schild() {
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

  const webseite = !!tab?.url.startsWith('http')
  const gemerkt = webseite && lesezeichen.some((l) => l.url === tab!.url)

  return (
    <div className="flex h-12 shrink-0 items-center gap-1 border-b border-outline-variant bg-surface-container px-2">
      <Knopf name={t('browser.nav.zurueck')} onClick={() => aktion('zurueck')} disabled={!tab?.zurueck}>
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
      </Knopf>
      <Knopf name={t('browser.nav.vor')} onClick={() => aktion('vor')} disabled={!tab?.vor}>
        <ArrowRight className="h-4 w-4" aria-hidden="true" />
      </Knopf>
      {tab?.laedt ? (
        <Knopf name={t('browser.nav.anhalten')} onClick={() => aktion('anhalten')}>
          <X className="h-4 w-4" aria-hidden="true" />
        </Knopf>
      ) : (
        <Knopf name={t('browser.nav.neuLaden')} onClick={() => aktion('neu_laden')} disabled={!tab?.url}>
          <RotateCw className="h-4 w-4" aria-hidden="true" />
        </Knopf>
      )}
      <Adresszeile ref={adresszeile} />
      <Schild />
      <Knopf
        name={gemerkt ? t('browser.nav.lesezeichenEntfernen') : t('browser.nav.lesezeichenSetzen')}
        onClick={() => tab && umschalten(tab.url, tab.titel)}
        disabled={!webseite}
        aktiv={gemerkt}
      >
        <Star className={`h-4 w-4 ${gemerkt ? 'fill-primary text-primary' : ''}`} aria-hidden="true" />
      </Knopf>
    </div>
  )
})
