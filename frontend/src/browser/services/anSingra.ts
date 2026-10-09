/**
 * „An Singra übergeben“: Titel und Adresse einer Seite kommen als Entwurf ins
 * Eingabefeld von Singra (`lib/aiEntwurf.ts`). Den Inhalt der Seite liest der
 * Browser dafür nie, und abgeschickt wird erst, wenn der Nutzer sendet.
 *
 * Nur gekoppelt, nur wenn Singra sichtbar ist, nur für Webseiten und nie aus
 * einem privaten Tab.
 */
import { useNavigate } from 'react-router-dom'

import { entwurfUebergeben } from '@/lib/aiEntwurf'

import { useSichtbareModule } from '../leiste/eintraege'
import { istWebseite } from './intern'
import { istGekoppelt, useSitzung } from './sitzung'
import type { Tab } from './tab'

export function singraEntwurf(tab: Pick<Tab, 'url' | 'titel'>): string {
  const titel = tab.titel?.trim()
  return titel && titel !== tab.url ? `${titel}\n${tab.url}` : tab.url
}

/** Die Aktion für genau diesen Tab, oder `null`, wenn es sie hier nicht gibt. */
export function useAnSingra(tab: Tab | undefined): (() => void) | null {
  const navigate = useNavigate()
  const gekoppelt = istGekoppelt(useSitzung((s) => s.stand))
  const module = useSichtbareModule()
  if (!tab || tab.privat || !istWebseite(tab.url) || !gekoppelt || !module.has('ai')) return null
  const text = singraEntwurf(tab)
  return () => {
    entwurfUebergeben(text)
    navigate('/ai')
  }
}
