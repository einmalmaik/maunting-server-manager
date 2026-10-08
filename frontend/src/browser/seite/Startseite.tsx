/**
 * Die Startseite eines neuen Tabs: Suche, Schnellzugriffe, Widgets,
 * Hintergrund. Kacheln und Widgets stehen unter `start/`.
 */
import { useState } from 'react'
import { EyeOff, Search, SlidersHorizontal } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, Dropdown, Input } from '@/Singra/UI'

import { MarkenSymbol } from '../marken'
import { useEinstellungenStore, type Hintergrund } from '../services/einstellungenStore'
import { SUCHMASCHINEN, suchmaschine, type SuchmaschinenId } from '../services/searchEngines'
import { useTabsStore } from '../services/tabsStore'
import { Schnellzugriffe } from './start/Schnellzugriffe'
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
  const sucheId = useEinstellungenStore((s) => s.suchmaschine)
  const hintergrund = useEinstellungenStore((s) => s.hintergrund)
  const eigenesBild = useEinstellungenStore((s) => s.eigenesBild)
  const setzen = useEinstellungenStore((s) => s.setzen)
  const [text, setText] = useState('')
  const suche = suchmaschine(sucheId)

  return (
    <div className="relative flex h-full flex-col items-center overflow-y-auto px-6 pb-8 pt-[14vh]" style={hintergrundStil(hintergrund, eigenesBild)}>
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

        <Schnellzugriffe />
        <Widgets privat={privat} />
        {!privat && (
          <Button variant="ghost" size="sm" onClick={() => einstellungen('design')} className="self-end">
            <SlidersHorizontal className="h-4 w-4" aria-hidden="true" />
            {t('browser.start.anpassen')}
          </Button>
        )}
      </div>
    </div>
  )
}
