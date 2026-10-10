/**
 * Die Startseite eines neuen Tabs: Suche, Schnellzugriffe, Widgets,
 * Hintergrund, darunter die Statistik des Schilds. Kacheln, Widgets und
 * Statistik stehen unter `start/`.
 *
 * Wie bei Brave bleibt der Hintergrund stehen, während die Seite rollt; je
 * weiter es nach unten geht, desto mehr legt sich eine unscharfe Fläche
 * darüber (`--msb-rollen`, gesetzt ohne neues Rendern je Bild).
 *
 * Am Handy gibt es ein Suchfeld, die Leiste unten; hier steht dann nur die
 * Wahl der Suchmaschine. Ein zweites Feld daneben war schmal und tat dasselbe.
 */
import { useRef, useState } from 'react'
import { ChevronDown, EyeOff, Search, SlidersHorizontal } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, Dropdown, Input } from '@/Singra/UI'

import { istAndroid } from '../services/plattform'
import { useEinstellungenStore, useWirksameSuche, type Hintergrund } from '../services/einstellungenStore'
import { suchmaschine } from '../services/searchEngines'
import { suchName, useSuchwahl } from '../services/suchwahl'
import { useTabsStore } from '../services/tabsStore'
import { Schnellzugriffe } from './start/Schnellzugriffe'
import { bewegungReduziert, Statistik } from './start/SchildStatistik'
import { WidgetAngebot } from './start/WidgetAngebot'
import { Widgets } from './start/Widgets'

export const HINTERGRUENDE: Record<Exclude<Hintergrund, 'eigen'>, string> = {
  schlicht: 'none',
  nacht: 'linear-gradient(135deg, #090a1a 0%, #17153b 50%, #0c0d1b 100%)',
  wald: 'linear-gradient(135deg, #021a14 0%, #063c2c 50%, #03120e 100%)',
  tiefsee: 'linear-gradient(135deg, #050b14 0%, #0d2137 50%, #030712 100%)',
}

export function hintergrundStil(hintergrund: Hintergrund, eigenesBild: string | null): React.CSSProperties {
  if (hintergrund === 'eigen' && eigenesBild) {
    return { backgroundImage: `url("${eigenesBild}")`, backgroundSize: 'cover', backgroundPosition: 'center' }
  }
  const verlauf = HINTERGRUENDE[hintergrund === 'eigen' ? 'schlicht' : hintergrund]
  return verlauf === 'none' ? {} : { background: verlauf }
}

export function Startseite({ privat }: { privat: boolean }) {
  const { t } = useTranslation()
  const eingeben = useTabsStore((s) => s.eingeben)
  const einstellungen = useTabsStore((s) => s.einstellungen)
  const hintergrund = useEinstellungenStore((s) => s.hintergrund)
  const eigenesBild = useEinstellungenStore((s) => s.eigenesBild)
  // Private Tabs zählen nicht mit; dort gibt es auch keine Statistik.
  const statistik = useEinstellungenStore((s) => s.statistikZeigen) && !privat
  const [text, setText] = useState('')
  const huelle = useRef<HTMLDivElement>(null)
  const rolle = useRef<HTMLDivElement>(null)
  const bild = useRef(0)
  const suche = suchmaschine(useWirksameSuche())
  const suchwahl = useSuchwahl()
  const handy = istAndroid()
  const wahl = (
    <Dropdown
      aria-label={t('browser.start.suchmaschine')}
      value={suchwahl.wert}
      onChange={suchwahl.aendern}
      options={suchwahl.optionen}
    />
  )

  // Wie weit gerollt ist, 0 bis 1 über die erste halbe Höhe.
  const gerollt = () => {
    cancelAnimationFrame(bild.current)
    bild.current = requestAnimationFrame(() => {
      const el = rolle.current
      if (el) huelle.current?.style.setProperty('--msb-rollen', String(Math.min(1, el.scrollTop / Math.max(1, el.clientHeight / 2))))
    })
  }
  const zurStatistik = () =>
    document.getElementById('msb-statistik')?.scrollIntoView({ behavior: bewegungReduziert() ? 'auto' : 'smooth', block: 'start' })

  return (
    <div ref={huelle} className="relative h-full overflow-hidden [--msb-rollen:0]">
      <div className="pointer-events-none absolute inset-0" style={hintergrundStil(hintergrund, eigenesBild)} />
      {hintergrund === 'eigen' && eigenesBild && <div className="pointer-events-none absolute inset-0 bg-surface/50" />}
      <div
        aria-hidden="true"
        data-msb-schleier=""
        className="pointer-events-none absolute inset-0 bg-surface/60 backdrop-blur-xl"
        style={{ opacity: 'var(--msb-rollen)' }}
      />
      <div ref={rolle} onScroll={gerollt} className="relative h-full overflow-y-auto">
        <div className="relative flex min-h-full flex-col items-center px-6 pb-6 pt-[14vh]">
          <div className="flex w-full max-w-2xl flex-col items-center gap-8">
            <div className="flex items-center gap-3">
              <img src="/msp.png" alt="" className="h-10 w-10" />
              <h1 className="text-headline-sm text-on-surface">{t('browser.start.titel')}</h1>
            </div>

            {privat && (
              <div className="flex w-full items-start gap-3 rounded-lg border border-primary/30 bg-primary/10 p-4 text-body-sm">
                <EyeOff className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
                <div>
                  <p className="font-medium text-on-surface">{t('browser.start.privatTitel')}</p>
                  <p className="mt-1 text-on-surface-variant">{t('browser.start.privatText')}</p>
                </div>
              </div>
            )}

            {handy ? (
              <div className="w-full max-w-xs">{wahl}</div>
            ) : (
              <form
                className="flex w-full items-center gap-2"
                onSubmit={(e) => {
                  e.preventDefault()
                  if (text.trim()) eingeben(text)
                }}
              >
                <div className="w-48 shrink-0">{wahl}</div>
                <div className="relative flex-1">
                  <Search className="pointer-events-none absolute left-3 top-1/2 z-10 h-4 w-4 -translate-y-1/2 text-on-surface-variant" aria-hidden="true" />
                  <Input
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    aria-label={t('browser.start.suchen')}
                    placeholder={t('browser.adresse.platzhalter', { suchmaschine: suchName(suche, t) })}
                    className="pl-9"
                    autoComplete="off"
                    spellCheck={false}
                    autoFocus
                  />
                </div>
              </form>
            )}

            {handy && !privat && <WidgetAngebot />}
            <Schnellzugriffe />
            <Widgets privat={privat} />
            {!privat && (
              <Button variant="ghost" size="sm" onClick={() => einstellungen('design')} className="self-end">
                <SlidersHorizontal className="h-4 w-4" aria-hidden="true" />
                {t('browser.start.anpassen')}
              </Button>
            )}
          </div>
          {statistik && (
            <button
              type="button"
              onClick={zurStatistik}
              style={{ opacity: 'calc(1 - var(--msb-rollen) * 2)' }}
              className="mt-auto flex flex-col items-center rounded-lg px-3 pt-10 text-label-md text-on-surface-variant hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              {t('browser.start.statistik.weiter')}
              <ChevronDown className="h-5 w-5 motion-safe:animate-bounce" aria-hidden="true" />
            </button>
          )}
        </div>
        {statistik && (
          <div className="relative flex justify-center px-6 pb-16 pt-8">
            <Statistik />
          </div>
        )}
      </div>
    </div>
  )
}
