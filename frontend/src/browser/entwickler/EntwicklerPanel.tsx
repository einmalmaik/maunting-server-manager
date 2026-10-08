/**
 * Die Entwicklerwerkzeuge des vorderen Tabs: Elemente, Konsole, Quellen,
 * Netzwerk, Anwendung, Leistung und Gerät. Solange das Panel offen ist, laufen
 * die Bereiche des Protokolls für genau diesen Tab (`einschalten`); beim
 * Schließen oder Wechseln fällt alles wieder weg (`ausschalten`).
 */
import { useEffect } from 'react'
import { Code2, Database, FileCode, Gauge, MousePointerClick, Network, Smartphone, SquareTerminal } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { TabBar, Zustandsflaeche, type TabDef } from '@/Singra/UI'

import { useAktiverTab } from '../services/tabsStore'
import { Anwendung } from './Anwendung'
import { Elemente } from './Elemente'
import { bildschirm, Geraet } from './Geraet'
import { Konsole } from './Konsole'
import { Knopf } from './Leiste'
import { Leistung } from './Leistung'
import { Netzwerk } from './Netzwerk'
import { rufen } from './protokoll'
import { Quellen } from './Quellen'
import { aufdecken, ausschalten, einschalten, useAnsicht, useSeitenflaeche, waehlenUmschalten, type Ansicht } from './werkzeuge'

const ANSICHTEN: TabDef<Ansicht>[] = [
  { id: 'elemente', labelKey: 'browser.entwickler.elemente.titel', icon: Code2 },
  { id: 'konsole', labelKey: 'browser.entwickler.konsole.titel', icon: SquareTerminal },
  { id: 'quellen', labelKey: 'browser.entwickler.quellen.titel', icon: FileCode },
  { id: 'netz', labelKey: 'browser.entwickler.netz.titel', icon: Network },
  { id: 'anwendung', labelKey: 'browser.entwickler.anwendung.titel', icon: Database },
  { id: 'leistung', labelKey: 'browser.entwickler.leistung.titel', icon: Gauge },
  { id: 'geraet', labelKey: 'browser.entwickler.geraet.titel', icon: Smartphone },
]

export function EntwicklerPanel() {
  const { t } = useTranslation()
  const tab = useAktiverTab()
  const ansicht = useAnsicht((s) => s.ansicht)
  const waehlen = useAnsicht((s) => s.waehlen)
  const untersuchen = useAnsicht((s) => s.untersuchen)
  const id = tab?.nativDa ? tab.id : null

  useEffect(() => {
    if (!id) return
    void einschalten(id)
    return () => void ausschalten(id)
  }, [id])

  // Ändert sich die Fläche der Seite, passt das nachgestellte Gerät neu hinein.
  const flaeche = useSeitenflaeche()
  useEffect(() => {
    const e = id ? useAnsicht.getState().emulation[id] : undefined
    if (id && e && e.geraet !== 'aus') void bildschirm(id, e)
  }, [id, flaeche.breite, flaeche.hoehe])

  // Rechtsklick „Untersuchen“: der Knoten unter dem Zeiger.
  useEffect(() => {
    if (!id || !untersuchen || untersuchen.tab !== id) return
    useAnsicht.getState().setUntersuchen(null)
    void rufen<{ backendNodeId?: number }>(id, 'DOM.getNodeForLocation', { x: Math.round(untersuchen.x), y: Math.round(untersuchen.y), includeUserAgentShadowDOM: false })
      .then((r) => (r.backendNodeId ? aufdecken(id, { backendNodeId: r.backendNodeId }) : undefined))
      .catch(() => null)
  }, [id, untersuchen])

  if (!id || !tab) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <Zustandsflaeche art="leer" titel={t('browser.entwickler.ohneSeiteTitel')} text={t('browser.entwickler.ohneSeiteText')} />
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-start gap-1 border-b border-outline-variant px-2 py-1.5">
        <Knopf text={t('browser.entwickler.waehlen')} an={waehlen} onClick={() => void waehlenUmschalten(id, !waehlen)}>
          <MousePointerClick className="h-4 w-4" aria-hidden="true" />
        </Knopf>
        <TabBar tabs={ANSICHTEN} active={ansicht} onChange={useAnsicht.getState().setAnsicht} embedded kompakt ariaLabel={t('browser.entwickler.titel')} />
      </div>
      {ansicht === 'elemente' && <Elemente tab={id} />}
      {ansicht === 'konsole' && <Konsole tab={id} />}
      {ansicht === 'quellen' && <Quellen tab={id} />}
      {ansicht === 'netz' && <Netzwerk tab={id} />}
      {ansicht === 'anwendung' && <Anwendung tab={id} url={tab.url} />}
      {ansicht === 'leistung' && <Leistung tab={id} />}
      {ansicht === 'geraet' && <Geraet tab={id} />}
    </div>
  )
}
