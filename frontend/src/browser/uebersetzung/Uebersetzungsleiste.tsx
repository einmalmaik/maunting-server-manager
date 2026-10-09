/**
 * Die Leiste über der Seite, solange eine Übersetzung offen ist. Sie liegt
 * über der Seite, nicht darauf (AGENTS.md Punkt 121). Ihr Inhalt samt
 * Modellliste lädt erst, wenn sie gebraucht wird.
 */
import { lazy, Suspense } from 'react'

import { useAktiverTab } from '../services/tabsStore'
import { useUebersetzung } from './ablauf'

const Leisteninhalt = lazy(() => import('./Leisteninhalt').then((m) => ({ default: m.Leisteninhalt })))

export function Uebersetzungsleiste() {
  const tab = useAktiverTab()
  const eintrag = useUebersetzung((s) => (tab ? s.eintraege[tab.id] : undefined))
  if (!tab || !eintrag) return null
  return (
    <Suspense fallback={null}>
      <Leisteninhalt tab={tab.id} e={eintrag} />
    </Suspense>
  )
}
