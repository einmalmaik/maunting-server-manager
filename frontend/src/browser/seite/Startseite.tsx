/**
 * Die Startseite eines neuen Tabs: Suche, Schnellzugriffe, Hintergrund.
 *
 * Die Schnellzugriffe sind die ersten Lesezeichen; ohne Lesezeichen stehen
 * dort ein paar bekannte Seiten. Symbole kommen aus `marken.tsx` oder sind ein
 * Buchstabe: die Startseite lädt nichts von fremden Servern.
 */
import { useState } from 'react'
import { EyeOff, Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Dropdown, Input } from '@/Singra/UI'

import { MarkenSymbol, type Marke } from '../marken'
import { useEinstellungenStore, type Hintergrund } from '../services/einstellungenStore'
import { seitenHost } from '../services/geraetKonfig'
import { SUCHMASCHINEN, suchmaschine, type SuchmaschinenId } from '../services/searchEngines'
import { useTabsStore } from '../services/tabsStore'
import { useVerlaufStore } from '../services/verlaufStore'

export const HINTERGRUENDE: Record<Exclude<Hintergrund, 'eigen'>, string> = {
  schlicht: 'none',
  nacht: 'linear-gradient(135deg, #090a1a 0%, #17153b 50%, #0c0d1b 100%)',
  wald: 'linear-gradient(135deg, #021a14 0%, #063c2c 50%, #03120e 100%)',
  tiefsee: 'linear-gradient(135deg, #050b14 0%, #0d2137 50%, #030712 100%)',
}

const BEKANNTE: { host: string; marke: Marke }[] = [
  { host: 'wikipedia.org', marke: 'wikipedia' },
  { host: 'youtube.com', marke: 'youtube' },
  { host: 'github.com', marke: 'github' },
  { host: 'reddit.com', marke: 'reddit' },
  { host: 'duckduckgo.com', marke: 'duckduckgo' },
  { host: 'google.com', marke: 'google' },
  { host: 'bing.com', marke: 'bing' },
  { host: 'ecosia.org', marke: 'ecosia' },
  { host: 'search.brave.com', marke: 'brave' },
]

const VORGABEN = [
  { url: 'https://de.wikipedia.org/', titel: 'Wikipedia' },
  { url: 'https://www.youtube.com/', titel: 'YouTube' },
  { url: 'https://github.com/', titel: 'GitHub' },
  { url: 'https://www.reddit.com/', titel: 'Reddit' },
]

function markeFuer(url: string): Marke | null {
  const host = seitenHost(url)
  if (!host) return null
  return BEKANNTE.find((b) => host === b.host || host.endsWith(`.${b.host}`))?.marke ?? null
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
  const oeffnen = useTabsStore((s) => s.oeffnen)
  const lesezeichen = useVerlaufStore((s) => s.lesezeichen)
  const { suchmaschine: sucheId, hintergrund, eigenesBild, setzen } = useEinstellungenStore()
  const [text, setText] = useState('')
  const suche = suchmaschine(sucheId)
  const schnell = lesezeichen.length > 0 ? lesezeichen.slice(0, 8) : VORGABEN

  return (
    <div className="relative flex h-full flex-col items-center overflow-y-auto px-6 pt-[14vh]" style={hintergrundStil(hintergrund, eigenesBild)}>
      {hintergrund === 'eigen' && eigenesBild && <div className="pointer-events-none absolute inset-0 bg-surface/50" />}
      <div className="relative flex w-full max-w-2xl flex-col items-center gap-8">
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

        <form
          className="flex w-full items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (text.trim()) eingeben(text)
          }}
        >
          <div className="w-40 shrink-0">
            <Dropdown
              aria-label={t('browser.start.suchmaschine')}
              value={sucheId}
              onChange={(v) => setzen({ suchmaschine: v as SuchmaschinenId })}
              options={SUCHMASCHINEN.map((s) => ({
                value: s.id,
                label: s.name,
                icon: <MarkenSymbol marke={s.marke} />,
              }))}
            />
          </div>
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 z-10 h-4 w-4 -translate-y-1/2 text-on-surface-variant" aria-hidden="true" />
            <Input
              value={text}
              onChange={(e) => setText(e.target.value)}
              aria-label={t('browser.start.suchen')}
              placeholder={t('browser.adresse.platzhalter', { suchmaschine: suche.name })}
              className="pl-9"
              autoComplete="off"
              spellCheck={false}
              autoFocus
            />
          </div>
        </form>

        <nav aria-label={t('browser.start.schnellzugriff')} className="grid w-full grid-cols-4 gap-3 sm:grid-cols-8">
          {schnell.map((s) => {
            const marke = markeFuer(s.url)
            const host = seitenHost(s.url) ?? s.url
            return (
              <button
                key={s.url}
                type="button"
                onClick={() => oeffnen(s.url)}
                className="group flex flex-col items-center gap-2 rounded-lg p-2 hover:bg-surface-container/80"
              >
                <span className="flex h-12 w-12 items-center justify-center rounded-full bg-surface-container-high text-on-surface group-hover:bg-surface-container-highest">
                  {marke ? (
                    <MarkenSymbol marke={marke} className="h-6 w-6" />
                  ) : (
                    <span className="text-title-md uppercase" aria-hidden="true">{host.charAt(0)}</span>
                  )}
                </span>
                <span className="w-full truncate text-center text-label-sm text-on-surface-variant">{s.titel || host}</span>
              </button>
            )
          })}
        </nav>
      </div>
    </div>
  )
}
