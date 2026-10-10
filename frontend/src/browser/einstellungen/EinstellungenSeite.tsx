/**
 * Die Einstellungen als eigene Seite im Tab (`msb://einstellungen/<kategorie>`).
 * Links die Kategorien, rechts ihr Inhalt; ist die Fläche schmal, stehen die
 * Kategorien als wischbare Reiterleiste oben. Die Kategorie steht in der
 * Adresse des Tabs, nicht in einem Zustand daneben.
 */
import { lazy, Suspense } from 'react'
import { useTranslation } from 'react-i18next'

import { TabBar } from '@/components/ui/TabBar'

import { useTabsStore } from '../services/tabsStore'
import { Allgemein } from './Allgemein'
import { Design } from './Design'
import { Dienste } from './Dienste'
import { Downloads } from './Downloads'
import { Erweiterungen } from './Erweiterungen'
import { Jugendschutz } from './Jugendschutz'
import { istAndroid } from '../services/plattform'
import { DATENSCHUTZ_TEIL, sichtbareKategorien, type Kategorie } from './kategorien'
import { Leistung } from './Leistung'
import { Passwoerter } from './Passwoerter'
import { Schutz } from './Schutz'
import { Suche } from './Suche'
import { Ueber } from './Ueber'
import { VerlaufDaten } from './VerlaufDaten'

const Privacy = lazy(() => import('@/pages/Privacy').then((m) => ({ default: m.Privacy })))

const INHALT: Record<Kategorie, () => JSX.Element | null> = {
  allgemein: Allgemein,
  suche: Suche,
  schutz: Schutz,
  jugendschutz: Jugendschutz,
  verlauf: VerlaufDaten,
  passwoerter: Passwoerter,
  downloads: Downloads,
  erweiterungen: Erweiterungen,
  design: Design,
  leistung: Leistung,
  dienste: Dienste,
  ueber: Ueber,
}

export function EinstellungenSeite({ teil }: { teil: string | null }) {
  const { t } = useTranslation()
  const oeffnen = useTabsStore((s) => s.einstellungen)

  if (teil === DATENSCHUTZ_TEIL) {
    return (
      <div className="h-full overflow-y-auto">
        <Suspense fallback={null}>
          <Privacy zurueck="/" zurueckAktion={() => oeffnen('ueber')} />
        </Suspense>
      </div>
    )
  }

  const kategorien = sichtbareKategorien(istAndroid())
  const kategorie = kategorien.find((k) => k.id === teil)?.id ?? 'allgemein'
  const Inhalt = INHALT[kategorie]
  const titel = t('browser.einstellungen.titel')

  return (
    // Eigener Bereich: die Breakpoints richten sich nach der Seitenfläche, nicht nach dem Fenster.
    <div className="msb-bereich h-full bg-surface">
      <div className="flex h-full flex-col md:flex-row">
        <nav aria-label={titel} className="hidden w-60 shrink-0 flex-col gap-0.5 overflow-y-auto px-3 py-6 md:flex">
          <h1 className="px-3 pb-4 font-headline text-headline-sm text-on-surface">{titel}</h1>
          {kategorien.map(({ id, symbol: Symbol }) => (
            <button
              key={id}
              type="button"
              aria-current={id === kategorie ? 'page' : undefined}
              onClick={() => oeffnen(id)}
              className={`flex h-10 items-center gap-3 rounded-xl px-3 text-left text-label-md transition-colors ${
                id === kategorie ? 'bg-primary/15 text-primary' : 'text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface'
              }`}
            >
              <Symbol className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span className="truncate">{t(`browser.einstellungen.kategorie.${id}`)}</span>
            </button>
          ))}
        </nav>
        <div className="shrink-0 px-3 pt-3 md:hidden">
          <TabBar
            einzeilig
            ariaLabel={titel}
            active={kategorie}
            onChange={oeffnen}
            tabs={kategorien.map((k) => ({ id: k.id, labelKey: `browser.einstellungen.kategorie.${k.id}`, icon: k.symbol }))}
          />
        </div>
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-2xl flex-col gap-4 px-4 py-4 md:px-8 md:py-6">
            <Inhalt />
          </div>
        </div>
      </div>
    </div>
  )
}
