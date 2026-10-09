/**
 * Der Rechtsklick in einer Seite, im Stil des Browsers statt von Edge.
 *
 * Was die WebView2 selbst kann (Kopieren, Einfügen, Bild speichern, Zurück),
 * wählt das Menü über ihren Befehl aus (`befehl`); so bleibt etwa das
 * Einfügen in ein Feld der Seite echt. Was den Browser betrifft (Link in neuem
 * Tab, Suchen nach der Auswahl, Untersuchen), macht die Oberfläche selbst.
 */
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import {
  ArrowLeft,
  ArrowRight,
  ClipboardPaste,
  Code,
  Copy,
  Download,
  ExternalLink,
  Image as ImageIcon,
  Link2,
  Printer,
  RotateCw,
  Scissors,
  Search,
  TextSelect,
  VenetianMask,
} from 'lucide-react'

import { Kontextmenue } from '@/Singra/UI'
import type { ActionMenuItem } from '@/Singra/UI/ActionMenu'

import { useAnsicht } from '../entwickler/werkzeuge'
import { useEinstellungenStore, useWirksameSuche } from '../services/einstellungenStore'
import { nativ } from '../services/nativ'
import { istAndroid } from '../services/plattform'
import { useFrageDesTabs, useRueckfragen } from '../services/rueckfragen'
import { baueZielUrl, suchmaschine } from '../services/searchEngines'
import { suchName } from '../services/suchwahl'
import { useAktiverTab, useTabsStore } from '../services/tabsStore'

/** Befehle der WebView2, die das Menü anbietet, in dieser Reihenfolge. */
const NATIV: Record<string, ReactNode> = {
  back: <ArrowLeft />,
  forward: <ArrowRight />,
  reload: <RotateCw />,
  cut: <Scissors />,
  copy: <Copy />,
  paste: <ClipboardPaste />,
  pasteAndMatchStyle: <ClipboardPaste />,
  selectAll: <TextSelect />,
  saveImageAs: <Download />,
  copyImage: <ImageIcon />,
  saveLinkAs: <Download />,
  saveAs: <Download />,
}

const symbol = (s: ReactNode) => <span className="[&>svg]:h-4 [&>svg]:w-4">{s}</span>

export function SeitenMenue({ flaeche }: { flaeche: React.RefObject<HTMLElement | null> }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const tab = useAktiverTab()
  const frage = useFrageDesTabs(tab?.id)
  const antworten = useRueckfragen((s) => s.antworten)
  const neuerTab = useTabsStore((s) => s.neuerTab)
  const maschine = useWirksameSuche()
  const searxng = useEinstellungenStore((s) => s.searxngUrl)

  if (!tab || frage?.art !== 'kontextmenue') return null
  const menue = frage
  const rahmen = flaeche.current?.getBoundingClientRect()
  const ort = {
    x: (rahmen?.left ?? 0) + menue.x / window.devicePixelRatio,
    y: (rahmen?.top ?? 0) + menue.y / window.devicePixelRatio,
  }

  const nativEintrag = (name: string, separatorBefore = false): ActionMenuItem[] => {
    const eintrag = menue.eintraege.find((e) => e.name === name)
    if (!eintrag) return []
    return [
      {
        key: name,
        label: t(`browser.menue.${name}`),
        icon: symbol(NATIV[name]),
        separatorBefore,
        onSelect: () => antworten(menue.nr, { art: 'menue', befehl: eintrag.befehl }),
      },
    ]
  }
  const eigen = (key: string, label: string, icon: ReactNode, tun: () => void, separatorBefore = false): ActionMenuItem => ({
    key,
    label,
    icon: symbol(icon),
    separatorBefore,
    onSelect: () => {
      antworten(menue.nr, { art: 'menue', befehl: null })
      tun()
    },
  })
  const kopieren = (text: string) => void navigator.clipboard.writeText(text).catch(() => null)

  const items: ActionMenuItem[] = []
  if (menue.link) {
    const link = menue.link
    items.push(
      eigen('link-tab', t('browser.menue.linkNeuerTab'), <ExternalLink />, () =>
        neuerTab(link, { nach: tab.id, privat: tab.privat, hintergrund: true }),
      ),
      eigen('link-privat', t('browser.menue.linkPrivat'), <VenetianMask />, () => neuerTab(link, { privat: true })),
      eigen('link-kopieren', t('browser.menue.linkKopieren'), <Link2 />, () => kopieren(link)),
      ...nativEintrag('saveLinkAs'),
    )
  }
  if (menue.bild) {
    const bild = menue.bild
    items.push(
      eigen('bild-tab', t('browser.menue.bildNeuerTab'), <ExternalLink />, () =>
        neuerTab(bild, { nach: tab.id, privat: tab.privat, hintergrund: true }), items.length > 0),
      ...nativEintrag('saveImageAs'),
      ...nativEintrag('copyImage'),
      eigen('bild-adresse', t('browser.menue.bildAdresse'), <Link2 />, () => kopieren(bild)),
    )
  }
  const bearbeiten = ['cut', 'copy', 'paste', 'pasteAndMatchStyle', 'selectAll'].flatMap((n, i) => nativEintrag(n, i === 0 && items.length > 0))
  items.push(...bearbeiten)
  if (menue.auswahl?.trim()) {
    const auswahl = menue.auswahl.trim()
    const ziel = baueZielUrl(auswahl, maschine, searxng ?? undefined)
    if (ziel) {
      const kurz = auswahl.length > 24 ? `${auswahl.slice(0, 24)}…` : auswahl
      items.push(
        eigen('suchen', t('browser.menue.suchen', { maschine: suchName(suchmaschine(maschine), t), text: kurz }), <Search />, () =>
          neuerTab(ziel, { nach: tab.id, privat: tab.privat }),
        ),
      )
    }
  }
  if (!menue.link && !menue.bild && !menue.auswahl && !menue.bearbeitbar) {
    items.push(...nativEintrag('back', items.length > 0), ...nativEintrag('forward'), ...nativEintrag('reload'), ...nativEintrag('saveAs'))
    items.push(eigen('drucken', t('browser.menue.drucken'), <Printer />, () => void nativ.tabDrucken(tab.id)))
  }
  // Entwicklerwerkzeuge gibt es auf Android nicht.
  if (!istAndroid()) items.push(
    eigen(
      'untersuchen',
      t('browser.menue.untersuchen'),
      <Code />,
      () => {
        // Die Stelle kommt in Pixeln des Geräts, die Seite rechnet in CSS-Pixeln.
        const { setUntersuchen, setAnsicht } = useAnsicht.getState()
        setUntersuchen({ tab: tab.id, x: menue.x / window.devicePixelRatio, y: menue.y / window.devicePixelRatio })
        setAnsicht('elemente')
        navigate('/entwickler')
      },
      true,
    ),
  )

  return (
    <Kontextmenue
      ort={ort}
      items={items}
      label={t('browser.menue.name')}
      onSchliessen={() => antworten(menue.nr, { art: 'menue', befehl: null })}
    />
  )
}
