/**
 * Die Auswahl der Suchmaschine, auf der Startseite und in den Einstellungen.
 * Oben steht die Werkseinstellung mit dem Namen der Suche, die sie gerade
 * meint; die MSM-Suche steht nur gekoppelt in der Liste.
 */
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { SuchSymbol } from '../marken'
import { useEinstellungenStore } from './einstellungenStore'
import { SUCHMASCHINEN, suchmaschine, wirksameSuche, type Suchmaschine, type SuchmaschinenId } from './searchEngines'
import { istGekoppelt, useSitzung } from './sitzung'

export const STANDARD = 'standard'

/** Fremde Suchen heißen wie ihre Marke, die eigene in der App-Sprache. */
export function suchName(maschine: Suchmaschine, t: TFunction): string {
  return maschine.id === 'msm' ? t('browser.einstellungen.msmSuche') : maschine.name
}

export function useSuchwahl() {
  const { t } = useTranslation()
  const gewaehlt = useEinstellungenStore((s) => s.suchmaschine)
  const setzen = useEinstellungenStore((s) => s.setzen)
  const gekoppelt = useSitzung((s) => istGekoppelt(s.stand))
  const standard = suchmaschine(wirksameSuche(null, gekoppelt))
  const angeboten = SUCHMASCHINEN.filter((s) => s.id !== 'msm' || gekoppelt)
  const optionen = [
    { value: STANDARD, label: t('browser.einstellungen.suchStandard', { name: suchName(standard, t) }), icon: <SuchSymbol maschine={standard} /> },
    ...angeboten.map((s) => ({ value: s.id as string, label: suchName(s, t), icon: <SuchSymbol maschine={s} /> })),
  ]
  return {
    wert: angeboten.some((s) => s.id === gewaehlt) ? (gewaehlt as string) : STANDARD,
    optionen,
    aendern: (wert: string) => setzen({ suchmaschine: wert === STANDARD ? null : (wert as SuchmaschinenId) }),
  }
}
