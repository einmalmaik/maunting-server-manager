/**
 * Der Kameraknopf der Navigationsleiste: sichtbarer Bereich, Bereich
 * auswählen und (nur Windows) die ganze Seite. Am Handy steht derselbe
 * Screenshot im Menü (`Modulmenue.tsx`) und beginnt mit der Auswahl.
 *
 * Die ganze Seite nimmt Rust auf, indem es die Ansicht kurz auf die Höhe der
 * Seite zieht und danach zurücksetzt. Bilden die Entwicklerwerkzeuge gerade
 * ein Gerät nach, fiele dessen Ansicht dabei weg; dann gibt es sie nicht.
 */
import { useRef, useState } from 'react'
import { Camera, Crop, FileImage, Monitor, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Ankerfenster } from '@/Singra/UI'

import { useAnsicht } from '../entwickler/werkzeuge'
import { Knopf } from '../kopf/knopf'
import { istWebseite } from '../services/intern'
import { istAndroid } from '../services/plattform'
import type { Tab } from '../services/tab'
import { useAktiverTab } from '../services/tabsStore'
import { useAufnahme, type Art } from './ablauf'

/** Nur echte Seiten, die gerade zu sehen sind. */
export function aufnahmeMoeglich(tab: Tab | undefined): tab is Tab {
  return !!tab && istWebseite(tab.url) && !tab.fehler && !tab.abgestuerzt
}

const ARTEN: { art: Art; symbol: LucideIcon }[] = [
  { art: 'sichtbar', symbol: Monitor },
  { art: 'auswahl', symbol: Crop },
  { art: 'ganz', symbol: FileImage },
]

export function AufnahmeKnopf() {
  const { t } = useTranslation()
  const tab = useAktiverTab()
  const anker = useRef<HTMLButtonElement>(null)
  const [offen, setOffen] = useState(false)
  const aufnehmen = useAufnahme((s) => s.aufnehmen)
  const name = t('browser.aufnahme.knopf')
  const geraet = useAnsicht((s) => (tab ? s.emulation[tab.id]?.geraet : undefined))
  const ganz = !istAndroid() && (!geraet || geraet === 'aus')
  const arten = ARTEN.filter((a) => a.art !== 'ganz' || ganz)

  return (
    <>
      <Knopf ref={anker} name={name} onClick={() => setOffen((o) => !o)} disabled={!aufnahmeMoeglich(tab)} aria-expanded={offen} aria-haspopup="dialog" seite="ende">
        <Camera className="h-4 w-4" aria-hidden="true" />
      </Knopf>
      <Ankerfenster offen={offen} onSchliessen={() => setOffen(false)} anker={anker} label={name} ausrichtung="ende" className="w-56 p-1">
        <ul className="flex flex-col">
          {arten.map(({ art, symbol: Symbol }) => (
            <li key={art}>
              <button
                type="button"
                onClick={() => {
                  setOffen(false)
                  if (aufnahmeMoeglich(tab)) void aufnehmen(tab, art)
                }}
                className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-body-sm text-on-surface hover:bg-surface-container-highest [@media(pointer:coarse)]:min-h-11"
              >
                <Symbol className="h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
                {t(`browser.aufnahme.${art}`)}
              </button>
            </li>
          ))}
        </ul>
      </Ankerfenster>
    </>
  )
}
