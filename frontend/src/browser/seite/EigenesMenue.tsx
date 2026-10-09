/**
 * Der Rechtsklick in der Oberfläche selbst. Webseiten liegen in einer
 * eigenen Webview und bekommen `SeitenMenue`; die Oberfläche ist auch eine
 * Webview, und dort ließ Tauri bis 10.10.2026 das Menü von Edge stehen
 * („Aktualisieren“, „Weitere Tools“). Je nachdem, wo man klickte, kam das
 * eine oder das andere.
 *
 * `useOhneEdgeMenue` nimmt das Edge-Menü überall weg, außer in Eingaben:
 * dort bleiben Ausschneiden, Einfügen und die Rechtschreibung. Auf den
 * Seiten des Browsers (Startseite, Einstellungen, Suche) zeigt
 * `EigenesMenue` dasselbe Menü wie auf Webseiten, mit dem, was dort geht.
 */
import { useEffect, useState, type MouseEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, ArrowRight, Copy, Search } from 'lucide-react'

import { Kontextmenue } from '@/Singra/UI'
import type { ActionMenuItem } from '@/Singra/UI/ActionMenu'

import { useEinstellungenStore, useWirksameSuche } from '../services/einstellungenStore'
import { baueZielUrl, suchmaschine } from '../services/searchEngines'
import { suchName } from '../services/suchwahl'
import type { Tab } from '../services/tab'
import { useTabsStore } from '../services/tabsStore'

const EINGABE = 'input, textarea, select, [contenteditable=""], [contenteditable="true"]'

export function useOhneEdgeMenue() {
  useEffect(() => {
    const weg = (e: Event) => {
      if (e.defaultPrevented || (e.target instanceof Element && e.target.closest(EINGABE))) return
      e.preventDefault()
    }
    document.addEventListener('contextmenu', weg)
    return () => document.removeEventListener('contextmenu', weg)
  }, [])
}

const symbol = (s: React.ReactNode) => <span className="[&>svg]:h-4 [&>svg]:w-4">{s}</span>

/** Gibt den Griff für `onContextMenu` der Seitenfläche und das Menü dazu. */
export function useEigenesMenue(tab: Tab | undefined) {
  const { t } = useTranslation()
  const aktion = useTabsStore((s) => s.aktion)
  const neuerTab = useTabsStore((s) => s.neuerTab)
  const maschine = useWirksameSuche()
  const searxng = useEinstellungenStore((s) => s.searxngUrl)
  const [menue, setMenue] = useState<{ ort: { x: number; y: number }; auswahl: string } | null>(null)

  const oeffnen = (ev: MouseEvent<HTMLElement>) => {
    if (ev.defaultPrevented || (ev.target instanceof Element && ev.target.closest(EINGABE))) return
    ev.preventDefault()
    setMenue({ ort: { x: ev.clientX, y: ev.clientY }, auswahl: window.getSelection()?.toString().trim() ?? '' })
  }

  const items: ActionMenuItem[] = []
  if (menue && tab) {
    const eintrag = (key: string, label: string, icon: React.ReactNode, tun: () => void, separatorBefore = false) =>
      items.push({ key, label, icon: symbol(icon), separatorBefore, onSelect: tun })
    if (menue.auswahl) {
      const auswahl = menue.auswahl
      eintrag('copy', t('browser.menue.copy'), <Copy />, () => void navigator.clipboard.writeText(auswahl).catch(() => null))
      const ziel = baueZielUrl(auswahl, maschine, searxng ?? undefined)
      if (ziel) {
        const kurz = auswahl.length > 24 ? `${auswahl.slice(0, 24)}…` : auswahl
        const label = t('browser.menue.suchen', { maschine: suchName(suchmaschine(maschine), t), text: kurz })
        eintrag('suchen', label, <Search />, () => neuerTab(ziel, { nach: tab.id, privat: tab.privat }))
      }
    }
    if (tab.zurueck || tab.vorher) eintrag('back', t('browser.menue.back'), <ArrowLeft />, () => aktion('zurueck'), items.length > 0)
    if (tab.vor) eintrag('forward', t('browser.menue.forward'), <ArrowRight />, () => aktion('vor'), items.length > 0 && !(tab.zurueck || tab.vorher))
  }

  const ansicht =
    menue && items.length > 0 ? (
      <Kontextmenue ort={menue.ort} items={items} label={t('browser.menue.name')} onSchliessen={() => setMenue(null)} />
    ) : null
  return { oeffnen, ansicht }
}
