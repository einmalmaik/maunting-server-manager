/**
 * Die oberste Leiste: Tabs, „Neuer Tab“ und die Fensterknöpfe. Das Fenster
 * hat keinen Rahmen des Systems; gezogen wird an der freien Fläche
 * (`data-tauri-drag-region`), ein Doppelklick maximiert.
 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type WheelEvent } from 'react'
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Columns2,
  EyeOff,
  Globe,
  Loader2,
  Minus,
  Moon,
  Plus,
  Search,
  Settings,
  Square,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { WorkspaceIcon } from './WorkspaceLeiste'
import { STANDARD_WORKSPACE_ID, useWorkspacesStore } from '../services/workspacesStore'
import { farbwahl } from '@/config/farbpalette'
import { useAnkerLage } from '@/Singra/UI/Ankerlage'
import { createPortal } from 'react-dom'

import { Kurzinfo } from '@/Singra/UI'

import { useEinstellungenStore } from '../services/einstellungenStore'
import { interneSeite, msmSucheBegriff } from '../services/intern'
import { nativ } from '../services/nativ'
import { istAndroid } from '../services/plattform'
import {
  beendeTabDrag,
  entpackeTabTransfer,
  FENSTER_ID,
  istTabFuerSplitVerwendet,
  istTabUebernommen,
  lauscheTabTransfer,
  lauscheTabZiehNachrichten,
  packeTabTransfer,
  registriereTabDrag,
  sendeTabUebernommen,
  sendeTabZiehNachricht,
  TAB_MIME_TYP,
  type TabTransferDaten,
  type TabZiehNachricht,
} from '../services/tabTransfer'
import { useTabsStore, type Tab } from '../services/tabsStore'

interface TabDragZustand {
  id: string
  startX: number
  startY: number
  letzteScreenX: number
  letzteScreenY: number
  letzteClientX: number
  letzteClientY: number
  imBalkenAbgelegt: boolean
  abgebrochen: boolean
  aufraeumenEscape?: () => void
  aufraeumen?: () => void
}

let aktiverTabDrag: TabDragZustand | null = null

/** `namen`: wie die Tabs ohne eigene Seite heißen (neuer Tab, Einstellungen). Die MSM-Suche heißt wie ihr Begriff. */
export function tabTitel(tab: Tab, namen: { neu: string; einstellungen: string }): string {
  const begriff = msmSucheBegriff(tab.url)
  if (begriff) return begriff
  if (interneSeite(tab.url)) return namen.einstellungen
  if (tab.titel) return tab.titel
  if (!tab.url) return namen.neu
  try {
    return new URL(tab.url).host || tab.url
  } catch {
    return tab.url
  }
}

export function TabSymbol({ tab }: { tab: Tab }) {
  const [kaputt, setKaputt] = useState<string | null>(null)
  if (tab.laedt) return <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" aria-hidden="true" />
  if (tab.privat) return <EyeOff className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
  if (interneSeite(tab.url)?.seite === 'suche') return <Search className="h-3.5 w-3.5 text-on-surface-variant" aria-hidden="true" />
  if (interneSeite(tab.url)) return <Settings className="h-3.5 w-3.5 text-on-surface-variant" aria-hidden="true" />
  // Private Tabs zeigen nie ein Favicon: die Oberfläche hätte es sonst in
  // ihrem eigenen Cache, außerhalb der privaten Sitzung.
  if (tab.favicon && kaputt !== tab.favicon) {
    return (
      <img
        src={tab.favicon}
        alt=""
        referrerPolicy="no-referrer"
        className="h-3.5 w-3.5 rounded-sm"
        onError={() => setKaputt(tab.favicon)}
      />
    )
  }
  return <Globe className="h-3.5 w-3.5 text-on-surface-variant" aria-hidden="true" />
}

/** Lautsprecher am Tab, solange die Seite Ton spielt oder stumm ist; ein Klick schaltet um. */
export function TonKnopf({ tab, titel }: { tab: Tab; titel: string }) {
  const { t } = useTranslation()
  if (!tab.ton && !tab.stumm) return null
  const name = tab.stumm ? t('browser.tabs.tonAn', { titel }) : t('browser.tabs.stumm', { titel })
  return (
    <Kurzinfo text={tab.stumm ? t('browser.tabs.tonAnKurz') : t('browser.tabs.stummKurz')} lage="unten">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          useTabsStore.getState().stummSchalten(tab.id)
        }}
        aria-label={name}
        aria-pressed={tab.stumm}
        className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface"
      >
        {tab.stumm ? <VolumeX className="h-3.5 w-3.5" aria-hidden="true" /> : <Volume2 className="h-3.5 w-3.5 text-primary" aria-hidden="true" />}
      </button>
    </Kurzinfo>
  )
}

/**
 * Erstellt ein optisches Phantom (Vorschau-Miniatur) des Browserfensters
 * für das Auslagern per Drag-and-Drop (wie bei Chromium/Chrome).
 */
export function erstelleTabPhantom(titel: string, favicon: string | null): HTMLElement {
  const container = document.createElement('div')
  container.setAttribute('aria-hidden', 'true')
  container.style.cssText = [
    'position: fixed',
    'left: -9999px',
    'top: -9999px',
    'width: 260px',
    'height: 160px',
    'background: rgba(24, 24, 27, 0.94)',
    'color: #f4f4f5',
    'border-radius: 12px',
    'border: 1px solid rgba(255, 255, 255, 0.18)',
    'box-shadow: 0 20px 35px -5px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(99, 102, 241, 0.35)',
    'overflow: hidden',
    'font-family: system-ui, -apple-system, sans-serif',
    'pointer-events: none',
    'z-index: 999999',
    'display: flex',
    'flex-direction: column',
  ].join(';')

  // Mini-Kopfleiste
  const kopf = document.createElement('div')
  kopf.style.cssText = [
    'height: 32px',
    'background: rgba(39, 39, 42, 0.95)',
    'border-bottom: 1px solid rgba(255, 255, 255, 0.1)',
    'display: flex',
    'align-items: center',
    'padding: 0 10px',
    'gap: 8px',
  ].join(';')

  // Tab-Reiter
  const tabPill = document.createElement('div')
  tabPill.style.cssText = [
    'display: flex',
    'align-items: center',
    'gap: 6px',
    'background: rgba(24, 24, 27, 0.95)',
    'padding: 3px 8px',
    'border-radius: 6px',
    'font-size: 11px',
    'font-weight: 500',
    'max-width: 150px',
    'border: 1px solid rgba(255, 255, 255, 0.12)',
  ].join(';')

  if (favicon) {
    const img = document.createElement('img')
    img.src = favicon
    img.alt = ''
    img.style.cssText = 'width: 12px; height: 12px; border-radius: 2px;'
    tabPill.appendChild(img)
  } else {
    const dot = document.createElement('span')
    dot.style.cssText = 'width: 8px; height: 8px; border-radius: 50%; background: #6366f1;'
    tabPill.appendChild(dot)
  }

  const titelSpan = document.createElement('span')
  titelSpan.textContent = titel || 'Tab'
  titelSpan.style.cssText = [
    'white-space: nowrap',
    'overflow: hidden',
    'text-overflow: ellipsis',
    'max-width: 110px',
    'color: #f4f4f5',
  ].join(';')
  tabPill.appendChild(titelSpan)
  kopf.appendChild(tabPill)

  // Fenstertasten (Windows-Stil)
  const tasten = document.createElement('div')
  tasten.style.cssText = 'margin-left: auto; display: flex; gap: 8px; align-items: center; color: rgba(255, 255, 255, 0.45); font-size: 10px; font-family: monospace;'
  const minBtn = document.createElement('span')
  minBtn.textContent = '—'
  const maxBtn = document.createElement('span')
  maxBtn.textContent = '□'
  const closeBtn = document.createElement('span')
  closeBtn.textContent = '✕'
  tasten.appendChild(minBtn)
  tasten.appendChild(maxBtn)
  tasten.appendChild(closeBtn)
  kopf.appendChild(tasten)

  container.appendChild(kopf)

  // Vorschau-Fläche (Webseiten-Simulation / Code-Linien)
  const inhalt = document.createElement('div')
  inhalt.style.cssText = [
    'flex: 1',
    'display: flex',
    'flex-direction: column',
    'justify-content: center',
    'padding: 14px 16px',
    'background: linear-gradient(180deg, rgba(24, 24, 27, 0.7) 0%, rgba(15, 15, 18, 0.95) 100%)',
    'gap: 7px',
  ].join(';')

  // Adressleiste
  const adressMini = document.createElement('div')
  adressMini.style.cssText = 'width: 75%; height: 9px; border-radius: 4px; background: rgba(255, 255, 255, 0.12); margin-bottom: 4px;'
  inhalt.appendChild(adressMini)

  // Simulation von Code / Inhalts-Zeilen
  const zeilenBreiten = ['85%', '92%', '65%', '78%']
  zeilenBreiten.forEach((breite, idx) => {
    const linie = document.createElement('div')
    linie.style.cssText = `width: ${breite}; height: 5px; border-radius: 2px; background: rgba(255, 255, 255, ${0.08 - idx * 0.015});`
    inhalt.appendChild(linie)
  })

  container.appendChild(inhalt)
  return container
}

function EinfuegeIndikator() {
  return (
    <div
      data-testid="tab-drop-indikator"
      aria-hidden="true"
      className="relative flex h-8 w-2.5 shrink-0 items-center justify-center transition-all duration-150 ease-out animate-scale-in"
    >
      <div className="h-6 w-1 rounded-full bg-primary shadow-[0_0_8px_rgba(99,102,241,0.9)] ring-2 ring-primary/40 animate-pulse" />
    </div>
  )
}

function PhantomTabVorschau({ tab }: { tab: { titel: string; favicon: string | null; privat?: boolean } }) {
  return (
    <div
      data-testid="phantom-tab-vorschau"
      aria-hidden="true"
      className="flex h-8 min-w-[7.5rem] max-w-[14rem] flex-1 select-none items-center gap-2 rounded-t-lg px-2.5 text-label-md bg-surface-container-high/80 text-primary border border-dashed border-primary/60 shadow-[0_0_12px_rgba(99,102,241,0.25)] transition-all duration-150 ease-out animate-fade-in"
    >
      <span className="flex h-4 w-4 shrink-0 items-center justify-center">
        {tab.favicon ? (
          <img src={tab.favicon} alt="" className="h-3.5 w-3.5 rounded shrink-0 opacity-90" />
        ) : (
          <span className="h-2 w-2 rounded-full bg-primary" />
        )}
      </span>
      <span className="min-w-0 flex-1 truncate text-xs font-medium text-on-surface">
        {tab.titel || 'Tab'}
      </span>
    </div>
  )
}

interface TabReiterProps {
  tab: Tab
  aktiv: boolean
  index: number
  onHoverZieh?: (e: DragEvent<HTMLDivElement>) => void
  onDropTab?: (e: DragEvent<HTMLDivElement>) => void
}

function TabReiter({ tab, aktiv, index: _index, onHoverZieh, onDropTab }: TabReiterProps) {
  const { t } = useTranslation()
  const { aktivieren, schliessen } = useTabsStore.getState()
  const titel = tabTitel(tab, { neu: t('browser.tabs.neu'), einstellungen: t('browser.einstellungen.titel') })

  const dragStart = (e: DragEvent<HTMLDivElement>) => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape' && aktiverTabDrag) {
        aktiverTabDrag.abgebrochen = true
      }
    }
    window.addEventListener('keydown', onKey, { capture: true })

    // Verhindert das Stop-Zeichen (🚫) beim Überqueren des Fensters und meldet Position für Snap
    const onWindowDragOver = (ev: globalThis.DragEvent) => {
      ev.preventDefault()
      if (ev.dataTransfer) {
        ev.dataTransfer.dropEffect = 'move'
      }
      const cx = typeof ev.clientX === 'number' ? ev.clientX : 0
      const cy = typeof ev.clientY === 'number' ? ev.clientY : 0
      const sx = typeof ev.screenX === 'number' ? ev.screenX : 0
      const sy = typeof ev.screenY === 'number' ? ev.screenY : 0

      if (aktiverTabDrag) {
        aktiverTabDrag.letzteClientX = cx
        aktiverTabDrag.letzteClientY = cy
        aktiverTabDrag.letzteScreenX = sx
        aktiverTabDrag.letzteScreenY = sy
      }

      sendeTabZiehNachricht({
        art: 'ziehen',
        ursprungFensterId: FENSTER_ID,
        tabId: tab.id,
        url: tab.url,
        titel,
        favicon: tab.favicon,
        privat: tab.privat,
        screenX: sx,
        screenY: sy,
        clientX: cx,
        clientY: cy,
      })
    }
    window.addEventListener('dragover', onWindowDragOver, { capture: true })

    let phantom: HTMLElement | null = null
    const transferPayload: TabTransferDaten = {
      ursprungFensterId: FENSTER_ID,
      tabId: tab.id,
      url: tab.url,
      titel,
      favicon: tab.favicon,
      privat: tab.privat,
    }

    registriereTabDrag(transferPayload)

    try {
      if (e.dataTransfer) {
        const payloadJson = packeTabTransfer(transferPayload)
        e.dataTransfer.setData(TAB_MIME_TYP, payloadJson)
        e.dataTransfer.setData('text/plain', payloadJson)
        e.dataTransfer.setData('text/msb-tab', tab.id)
        if (tab.url) e.dataTransfer.setData('text/uri-list', tab.url)
        e.dataTransfer.effectAllowed = 'move'
        if (typeof e.dataTransfer.setDragImage === 'function' && typeof document !== 'undefined') {
          phantom = erstelleTabPhantom(titel, tab.favicon)
          document.body.appendChild(phantom)
          e.dataTransfer.setDragImage(phantom, 60, 16)
        }
      }
    } catch {
      // In manchen Umgebungen ohne volles DragEvent ignorieren
    }

    const sx = typeof e.screenX === 'number' ? e.screenX : 0
    const sy = typeof e.screenY === 'number' ? e.screenY : 0
    const cx = typeof e.clientX === 'number' ? e.clientX : 0
    const cy = typeof e.clientY === 'number' ? e.clientY : 0

    sendeTabZiehNachricht({
      art: 'ziehen',
      ursprungFensterId: FENSTER_ID,
      tabId: tab.id,
      url: tab.url,
      titel,
      favicon: tab.favicon,
      privat: tab.privat,
      screenX: sx,
      screenY: sy,
      clientX: cx,
      clientY: cy,
    })

    const aufraeumen = () => {
      window.removeEventListener('keydown', onKey, { capture: true })
      window.removeEventListener('dragover', onWindowDragOver, { capture: true })
      beendeTabDrag()
      if (phantom) {
        phantom.remove()
        phantom = null
      }
    }

    aktiverTabDrag = {
      id: tab.id,
      startX: sx,
      startY: sy,
      letzteScreenX: sx,
      letzteScreenY: sy,
      letzteClientX: cx,
      letzteClientY: cy,
      imBalkenAbgelegt: false,
      abgebrochen: false,
      aufraeumenEscape: aufraeumen,
      aufraeumen,
    }
  }

  const beimZiehen = (e: DragEvent<HTMLDivElement>) => {
    if (!aktiverTabDrag) return
    const sx = typeof e.screenX === 'number' ? e.screenX : 0
    const sy = typeof e.screenY === 'number' ? e.screenY : 0
    const cx = typeof e.clientX === 'number' ? e.clientX : 0
    const cy = typeof e.clientY === 'number' ? e.clientY : 0

    if (sx !== 0 || sy !== 0 || cx !== 0 || cy !== 0) {
      aktiverTabDrag.letzteScreenX = sx
      aktiverTabDrag.letzteScreenY = sy
      aktiverTabDrag.letzteClientX = cx
      aktiverTabDrag.letzteClientY = cy

      sendeTabZiehNachricht({
        art: 'ziehen',
        ursprungFensterId: FENSTER_ID,
        tabId: tab.id,
        url: tab.url,
        titel,
        favicon: tab.favicon,
        privat: tab.privat,
        screenX: sx,
        screenY: sy,
        clientX: cx,
        clientY: cy,
      })
    }
  }

  const dragEnde = (e: DragEvent<HTMLDivElement>) => {
    const drag = aktiverTabDrag
    aktiverTabDrag = null
    beendeTabDrag()
    drag?.aufraeumen?.()
    drag?.aufraeumenEscape?.()

    if (!drag) return
    if (istAndroid()) return
    if (drag.abgebrochen) return
    if (drag.imBalkenAbgelegt) return

    // Prüfen, ob der Tab in einem anderen Fenster übernommen wurde
    if (istTabUebernommen(drag.id)) {
      schliessen(drag.id)
      return
    }

    const tabs = useTabsStore.getState().tabs
    if (tabs.length <= 1) return

    const posScreenX = typeof e.screenX === 'number' ? e.screenX : 0
    const posScreenY = typeof e.screenY === 'number' ? e.screenY : 0
    const posClientX = typeof e.clientX === 'number' ? e.clientX : 0
    const posClientY = typeof e.clientY === 'number' ? e.clientY : 0

    const hatScreen = posScreenX !== 0 || posScreenY !== 0
    const screenX = hatScreen ? posScreenX : drag.letzteScreenX
    const screenY = hatScreen ? posScreenY : drag.letzteScreenY

    const distanz = Math.hypot(screenX - drag.startX, screenY - drag.startY)

    const hatClient = posClientX !== 0 || posClientY !== 0
    const clientX = hatClient ? posClientX : drag.letzteClientX
    const clientY = hatClient ? posClientY : drag.letzteClientY

    sendeTabZiehNachricht({
      art: 'ablegen',
      ursprungFensterId: FENSTER_ID,
      tabId: tab.id,
      url: tab.url,
      titel,
      favicon: tab.favicon,
      privat: tab.privat,
      screenX,
      screenY,
      clientX,
      clientY,
    })

    const split = useTabsStore.getState().split
    const istImSplit = split && (split.sekundaerId === drag.id || useTabsStore.getState().aktivId === drag.id)
    if (istImSplit || istTabFuerSplitVerwendet(drag.id)) {
      return
    }

    if (tabs.length <= 1) return

    // Nur wenn der Zeiger nachweislich in der Kopfleiste geblieben ist und sich kaum bewegt hat
    const imTabBereich =
      hatClient &&
      clientX >= 0 &&
      clientX <= window.innerWidth &&
      clientY >= 0 &&
      clientY <= 48 &&
      distanz < 50

    if (imTabBereich) return

    const ausserhalb =
      distanz >= 50 ||
      clientY < 0 ||
      clientY > 48 ||
      clientX < 0 ||
      clientX > window.innerWidth

    if (!ausserhalb) return

    void nativ
      .tabAuslagern(tab.url, screenX, screenY, tab.privat)
      .then(() => {
        schliessen(tab.id)
      })
      .catch((err) => {
        console.error('[MSB] Tab konnte nicht ausgelagert werden:', err)
      })
  }

  const split = useTabsStore((s) => s.split)
  const aktivId = useTabsStore((s) => s.aktivId)
  const istImSplit = split ? (tab.id === split.sekundaerId || tab.id === aktivId) : false

  return (
    <div
      role="tab"
      aria-selected={aktiv}
      tabIndex={aktiv ? 0 : -1}
      draggable
      onDragStart={dragStart}
      onDrag={beimZiehen}
      onDragEnd={dragEnde}
      onDragOver={(e) => {
        e.preventDefault()
        if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
        onHoverZieh?.(e)
      }}
      onDrop={(e) => {
        onDropTab?.(e)
      }}
      onClick={() => aktivieren(tab.id)}
      onAuxClick={(e) => e.button === 1 && schliessen(tab.id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') aktivieren(tab.id)
      }}
      className={`group flex h-8 min-w-[7.5rem] max-w-[14rem] flex-1 cursor-default items-center gap-2 rounded-t-lg px-2.5 text-label-md transition-all duration-150 ease-out ${
        aktiv
          ? 'bg-surface-container text-on-surface'
          : istImSplit
          ? 'bg-surface-container/80 text-on-surface'
          : 'text-on-surface-variant hover:bg-surface-container/60'
      }`}
    >
      <span className={`flex h-4 w-4 shrink-0 items-center justify-center ${tab.ruhe ? 'opacity-50' : ''}`}>
        <TabSymbol tab={tab} />
      </span>
      <span className={`min-w-0 flex-1 truncate ${tab.ruhe ? 'opacity-60' : ''}`}>
        {tab.privat && <span className="sr-only">{t('browser.tabs.privat')}: </span>}
        {titel}
        {tab.ruhe && <span className="sr-only">, {t(`browser.tabs.ruhe.${tab.ruhe}`)}</span>}
      </span>
      {istImSplit && (
        <span
          className={`flex h-4 items-center gap-0.5 rounded px-1 text-label-sm font-semibold ${
            aktiv ? 'bg-primary/20 text-primary' : 'bg-surface-container-highest/60 text-on-surface-variant'
          }`}
          aria-label={
            tab.id === aktivId
              ? t('browser.split.primaerBadge', { defaultValue: 'Geteilte Ansicht: Links' })
              : t('browser.split.sekundaerBadge', { defaultValue: 'Geteilte Ansicht: Rechts' })
          }
        >
          <Columns2 className="h-3 w-3" aria-hidden="true" />
          <span className="text-label-sm">{tab.id === aktivId ? 'L' : 'R'}</span>
        </span>
      )}
      {tab.ruhe && <Moon className="h-3 w-3 shrink-0 text-on-surface-variant" aria-hidden="true" />}
      <TonKnopf tab={tab} titel={titel} />
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          schliessen(tab.id)
        }}
        className={`flex h-5 w-5 shrink-0 items-center justify-center rounded hover:bg-surface-container-highest ${
          aktiv ? '' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100'
        }`}
        aria-label={t('browser.tabs.schliessen', { titel })}
      >
        <X className="h-3 w-3" aria-hidden="true" />
      </button>
    </div>
  )
}

function WorkspaceKopfDropdown() {
  const { t } = useTranslation()
  const workspaces = useWorkspacesStore((s) => s.workspaces)
  const aktiverWorkspaceId = useWorkspacesStore((s) => s.aktiverWorkspaceId)
  const wechselnWorkspace = useWorkspacesStore((s) => s.wechselnWorkspace)
  const [offen, setOffen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menueRef = useRef<HTMLDivElement>(null)
  const lage = useAnkerLage(offen, triggerRef, menueRef, {
    seite: 'unten',
    ausrichtung: 'start',
    abstand: 4,
  })

  const aktiver = workspaces.find((w) => w.id === aktiverWorkspaceId) ?? workspaces[0]
  const aktiverFarbe = farbwahl(aktiver?.farbe)

  useEffect(() => {
    if (!offen) return
    const daneben = (e: MouseEvent) => {
      const target = e.target as Node
      if (!triggerRef.current?.contains(target) && !menueRef.current?.contains(target)) {
        setOffen(false)
      }
    }
    const taste = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOffen(false)
        triggerRef.current?.focus()
      }
    }
    document.addEventListener('mousedown', daneben)
    document.addEventListener('keydown', taste)
    return () => {
      document.removeEventListener('mousedown', daneben)
      document.removeEventListener('keydown', taste)
    }
  }, [offen])

  return (
    <div className="relative mb-1 shrink-0">
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={offen}
        onClick={(e) => {
          e.stopPropagation()
          setOffen(!offen)
        }}
        className="flex h-7 items-center gap-1.5 rounded-lg border border-outline-variant bg-surface-container px-2 text-label-sm font-medium text-on-surface hover:bg-surface-container-high transition-colors"
        aria-label={t('browser.workspaces.titel')}
      >
        <span className={`h-2 w-2 rounded-full ${aktiverFarbe.flaecheStark} border ${aktiverFarbe.rand}`} />
        <WorkspaceIcon icon={aktiver?.icon ?? 'globe'} className="h-3.5 w-3.5 opacity-80" />
        <span className="max-w-24 truncate">{aktiver?.name}</span>
        <ChevronDown className="h-3 w-3 text-on-surface-variant opacity-70" aria-hidden="true" />
      </button>

      {offen &&
        createPortal(
          <div
            ref={menueRef}
            role="menu"
            data-msb-verdeckt="true"
            style={lage}
            className="fixed z-50 flex flex-col min-w-48 max-w-[calc(100vw-1rem)] max-h-[calc(100dvh-1rem)] overflow-y-auto rounded-lg border border-outline-variant bg-surface-container-high p-1.5 shadow-panel backdrop-blur-sm text-label-sm select-none"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-2.5 py-1 text-label-sm font-semibold uppercase tracking-wider text-on-surface-variant/70">
              {t('browser.workspaces.titel')}
            </div>
            {workspaces.map((ws, index) => {
              const aktiv = ws.id === aktiverWorkspaceId
              const wsFarbe = farbwahl(ws.farbe)
              return (
                <button
                  key={ws.id}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    wechselnWorkspace(ws.id)
                    setOffen(false)
                  }}
                  className={`flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-1.5 transition-colors text-left ${
                    aktiv
                      ? 'bg-primary/15 text-primary font-semibold'
                      : 'text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface'
                  }`}
                >
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <span className={`h-2 w-2 rounded-full shrink-0 ${wsFarbe.flaecheStark} border ${wsFarbe.rand}`} />
                    <WorkspaceIcon icon={ws.icon} className="h-3.5 w-3.5 shrink-0 opacity-80" />
                    <span className="truncate">{ws.name}</span>
                  </div>
                  {index < 9 && (
                    <span className="text-label-sm text-on-surface-variant/50 ml-2">
                      Alt+{index + 1}
                    </span>
                  )}
                </button>
              )
            })}
          </div>,
          document.body
        )}
    </div>
  )
}

export function Kopfleiste() {
  const { t } = useTranslation()
  const tabs = useTabsStore((s) => s.tabs)
  const aktiverWorkspaceId = useWorkspacesStore((s) => s.aktiverWorkspaceId)
  const aktivId = useTabsStore((s) => s.aktivId)
  const split = useTabsStore((s) => s.split)
  const wirksamAktivId = split && split.aktiveHaelfte === 'sekundaer' ? split.sekundaerId : aktivId
  const neuerTab = useTabsStore((s) => s.neuerTab)
  const startseite = useTabsStore((s) => s.startseite)
  const privatAus = useEinstellungenStore((s) => s.ausgeblendet.includes('privaterTab'))
  const tabAusrichtung = useEinstellungenStore((s) => s.tabAusrichtung)
  const [dropZielIndex, setDropZielIndex] = useState<number | null>(null)
  const [fremderDragTab, setFremderDragTab] = useState<TabZiehNachricht | null>(null)

  const sichtbareTabs = useMemo(
    () => tabs.filter((t) => (t.workspaceId ?? STANDARD_WORKSPACE_ID) === aktiverWorkspaceId),
    [tabs, aktiverWorkspaceId]
  )

  const tabListRef = useRef<HTMLDivElement>(null)
  const [kannScrollenLinks, setKannScrollenLinks] = useState(false)
  const [kannScrollenRechts, setKannScrollenRechts] = useState(false)

  const pruefeScroll = useCallback(() => {
    const el = tabListRef.current
    if (!el) return
    setKannScrollenLinks(el.scrollLeft > 2)
    setKannScrollenRechts(el.scrollLeft + el.clientWidth < el.scrollWidth - 2)
  }, [])

  useEffect(() => {
    pruefeScroll()
    const el = tabListRef.current
    if (!el) return
    el.addEventListener('scroll', pruefeScroll, { passive: true })
    window.addEventListener('resize', pruefeScroll)
    return () => {
      el.removeEventListener('scroll', pruefeScroll)
      window.removeEventListener('resize', pruefeScroll)
    }
  }, [sichtbareTabs, pruefeScroll])

  useEffect(() => {
    if (tabAusrichtung !== 'horizontal') return
    const aktivEl = tabListRef.current?.querySelector('[role="tab"][aria-selected="true"]') as HTMLElement | null
    if (typeof aktivEl?.scrollIntoView === 'function') {
      aktivEl.scrollIntoView({ behavior: 'smooth', inline: 'nearest', block: 'nearest' })
    }
  }, [wirksamAktivId, tabAusrichtung])

  const behandleMausrad = (e: WheelEvent<HTMLDivElement>) => {
    if (e.deltaY !== 0) {
      e.currentTarget.scrollLeft += e.deltaY
    }
  }

  useEffect(() => {
    const abmeldenTransfer = lauscheTabTransfer(({ tabId }) => {
      if (aktiverTabDrag && aktiverTabDrag.id === tabId) {
        aktiverTabDrag.imBalkenAbgelegt = true
        aktiverTabDrag.aufraeumen?.()
      }
      useTabsStore.getState().schliessen(tabId)
    })

    const abmeldenLive = lauscheTabZiehNachrichten((msg) => {
      if (msg.ursprungFensterId === FENSTER_ID) return

      if (msg.art === 'abbrechen') {
        setDropZielIndex(null)
        setFremderDragTab(null)
        return
      }

      const winX = typeof window.screenX === 'number' ? window.screenX : 0
      const winY = typeof window.screenY === 'number' ? window.screenY : 0
      const relX = msg.screenX - winX
      const relY = msg.screenY - winY
      const imBalken = relY >= 0 && relY <= 48 && relX >= 0 && relX <= (window.innerWidth || 1920)

      if (msg.art === 'ziehen') {
        if (!imBalken) {
          setDropZielIndex(null)
          setFremderDragTab(null)
          return
        }

        setFremderDragTab(msg)

        const tabEls = document.querySelectorAll<HTMLElement>('[role="tab"]')
        let ziel = sichtbareTabs.length
        for (let i = 0; i < tabEls.length; i++) {
          const rect = tabEls[i].getBoundingClientRect()
          const mitte = rect.left + rect.width / 2
          if (relX < mitte) {
            ziel = i
            break
          }
        }
        setDropZielIndex(ziel)
        return
      }

      if (msg.art === 'ablegen') {
        if (imBalken) {
          const tabEls = document.querySelectorAll<HTMLElement>('[role="tab"]')
          let sichtbaresZiel = sichtbareTabs.length
          for (let i = 0; i < tabEls.length; i++) {
            const rect = tabEls[i].getBoundingClientRect()
            const mitte = rect.left + rect.width / 2
            if (relX < mitte) {
              sichtbaresZiel = i
              break
            }
          }
          let globalerZielIndex = tabs.length
          if (sichtbaresZiel < sichtbareTabs.length) {
            const zielTab = sichtbareTabs[sichtbaresZiel]
            const idx = tabs.findIndex((t) => t.id === zielTab.id)
            if (idx >= 0) globalerZielIndex = idx
          } else if (sichtbareTabs.length > 0) {
            const letzterTab = sichtbareTabs[sichtbareTabs.length - 1]
            const idx = tabs.findIndex((t) => t.id === letzterTab.id)
            if (idx >= 0) globalerZielIndex = idx + 1
          }
          neuerTab(msg.url, {
            anIndex: globalerZielIndex,
            titel: msg.titel,
            favicon: msg.favicon,
            privat: msg.privat,
            workspaceId: aktiverWorkspaceId,
          })
          sendeTabUebernommen(msg.ursprungFensterId, msg.tabId)
        }
        setDropZielIndex(null)
        setFremderDragTab(null)
      }
    })

    return () => {
      abmeldenTransfer()
      abmeldenLive()
    }
  }, [tabs, sichtbareTabs, aktiverWorkspaceId, neuerTab])

  const behandleDrop = (e: DragEvent, zielIndex: number) => {
    e.preventDefault()
    e.stopPropagation()
    setDropZielIndex(null)
    setFremderDragTab(null)

    const transfer = entpackeTabTransfer(e.dataTransfer)
    if (!transfer) return

    let globalerZielIndex = tabs.length
    if (zielIndex < sichtbareTabs.length) {
      const zielTab = sichtbareTabs[zielIndex]
      const idx = tabs.findIndex((t) => t.id === zielTab.id)
      if (idx >= 0) globalerZielIndex = idx
    } else if (sichtbareTabs.length > 0) {
      const letzterTab = sichtbareTabs[sichtbareTabs.length - 1]
      const idx = tabs.findIndex((t) => t.id === letzterTab.id)
      if (idx >= 0) globalerZielIndex = idx + 1
    }

    // 1. Aus einem anderen Fenster zusammenführen (Re-docking)
    if (transfer.ursprungFensterId !== FENSTER_ID) {
      neuerTab(transfer.url, {
        anIndex: globalerZielIndex,
        titel: transfer.titel,
        favicon: transfer.favicon,
        privat: transfer.privat,
        workspaceId: aktiverWorkspaceId,
      })
      sendeTabUebernommen(transfer.ursprungFensterId, transfer.tabId)
      beendeTabDrag()
      return
    }

    // 2. Innerhalb dieses Fensters verschieben
    if (aktiverTabDrag) {
      aktiverTabDrag.imBalkenAbgelegt = true
      aktiverTabDrag.aufraeumen?.()
    }
    const von = tabs.findIndex((t) => t.id === transfer.tabId)
    if (von >= 0) {
      const korrigiert = von < globalerZielIndex ? globalerZielIndex - 1 : globalerZielIndex
      if (korrigiert !== von) {
        useTabsStore.getState().verschieben(transfer.tabId, korrigiert)
      }
    }
  }

  return (
    <div
      data-tauri-drag-region
      onDoubleClick={(e) => e.target === e.currentTarget && void nativ.fensterAktion('maximieren')}
      onDragOver={(e) => {
        e.preventDefault()
        if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) {
          setDropZielIndex(null)
          setFremderDragTab(null)
        }
      }}
      onDrop={(e) => {
        behandleDrop(e, dropZielIndex ?? sichtbareTabs.length)
      }}
      className={`relative flex h-10 shrink-0 select-none gap-1 bg-surface-container-low pl-2 ${
        tabAusrichtung === 'vertikal' ? 'items-center' : 'items-end'
      }`}
    >
      {tabAusrichtung === 'vertikal' ? (
        <>
          {/* Frei ziehbare Fensterfläche */}
          <div data-tauri-drag-region className="h-full min-w-0 flex-1" />

          {/* Mittig platziertes Logo: Klick führt zur Startseite */}
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none select-none">
            <Kurzinfo text={t('browser.start.titel')} lage="unten">
              <button
                type="button"
                onClick={() => startseite()}
                className="pointer-events-auto flex h-7 w-7 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface transition-colors"
                aria-label={t('browser.start.titel')}
              >
                <img src="/msp.png" alt="" className="h-5 w-5 pointer-events-none" />
              </button>
            </Kurzinfo>
          </div>
        </>
      ) : (
        <>
          <button
            type="button"
            onClick={() => startseite()}
            className="mb-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg hover:bg-surface-container-high transition-colors"
            aria-label={t('browser.start.titel')}
          >
            <img src="/msp.png" alt="" className="h-5 w-5 pointer-events-none" />
          </button>
          <WorkspaceKopfDropdown />
          {kannScrollenLinks && (
            <button
              type="button"
              onClick={() => tabListRef.current?.scrollBy({ left: -220, behavior: 'smooth' })}
              className="mb-1 flex h-7 w-5 shrink-0 items-center justify-center rounded text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
              aria-label={t('browser.tabs.scrollLinks')}
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
          <div
            ref={tabListRef}
            role="tablist"
            aria-label={t('browser.tabs.liste')}
            onWheel={behandleMausrad}
            onDragOver={(e) => {
              e.preventDefault()
              if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
            }}
            onDragLeave={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node)) {
                setDropZielIndex(null)
                setFremderDragTab(null)
              }
            }}
            onDrop={(e) => {
              e.stopPropagation()
              behandleDrop(e, dropZielIndex ?? sichtbareTabs.length)
            }}
            className="flex min-w-0 items-end gap-0.5 overflow-x-auto scroll-smooth [overscroll-behavior:contain] msm-ohne-rollbalken"
          >
            {sichtbareTabs.map((tab, i) => (
              <Fragment key={tab.id}>
                {dropZielIndex === i && (
                  <>
                    <EinfuegeIndikator />
                    {fremderDragTab && <PhantomTabVorschau tab={fremderDragTab} />}
                  </>
                )}
                <TabReiter
                  tab={tab}
                  aktiv={tab.id === wirksamAktivId}
                  index={i}
                  onHoverZieh={(e) => {
                    const rect = e.currentTarget.getBoundingClientRect()
                    const mitte = rect.left + rect.width / 2
                    const ziel = e.clientX < mitte ? i : i + 1
                    setDropZielIndex(ziel)
                  }}
                  onDropTab={(e) => {
                    e.stopPropagation()
                    const rect = e.currentTarget.getBoundingClientRect()
                    const mitte = rect.left + rect.width / 2
                    const ziel = e.clientX < mitte ? i : i + 1
                    behandleDrop(e, ziel)
                  }}
                />
              </Fragment>
            ))}
            {dropZielIndex === sichtbareTabs.length && (
              <>
                <EinfuegeIndikator />
                {fremderDragTab && <PhantomTabVorschau tab={fremderDragTab} />}
              </>
            )}
          </div>
          {kannScrollenRechts && (
            <button
              type="button"
              onClick={() => tabListRef.current?.scrollBy({ left: 220, behavior: 'smooth' })}
              className="mb-1 flex h-7 w-5 shrink-0 items-center justify-center rounded text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
              aria-label={t('browser.tabs.scrollRechts')}
            >
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
          <div
            className="mb-1 flex shrink-0 items-center"
            onDragOver={(e) => {
              e.preventDefault()
              if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
              setDropZielIndex(sichtbareTabs.length)
            }}
            onDrop={(e) => {
              e.stopPropagation()
              behandleDrop(e, sichtbareTabs.length)
            }}
          >
            <Kurzinfo text={t('browser.tabs.neuKuerzel')} seite="anfang">
              <button
                type="button"
                onClick={() => neuerTab()}
                className="flex h-7 w-7 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
                aria-label={t('browser.tabs.neu')}
              >
                <Plus className="h-4 w-4" aria-hidden="true" />
              </button>
            </Kurzinfo>
            {!privatAus && (
              <Kurzinfo text={t('browser.tabs.privatKuerzel')} seite="anfang">
                <button
                  type="button"
                  onClick={() => neuerTab(undefined, { privat: true })}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container-high hover:text-primary"
                  aria-label={t('browser.tabs.neuPrivat')}
                >
                  <EyeOff className="h-4 w-4" aria-hidden="true" />
                </button>
              </Kurzinfo>
            )}
          </div>
        </>
      )}
      <div
        className="h-full min-w-6 flex-1"
        data-tauri-drag-region
        onDragOver={(e) => {
          e.preventDefault()
          if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
          setDropZielIndex(tabs.length)
        }}
        onDrop={(e) => {
          e.stopPropagation()
          behandleDrop(e, tabs.length)
        }}
      />
      <div className="flex h-full shrink-0 items-stretch">
        {(
          [
            ['minimieren', Minus, t('browser.fenster.minimieren')],
            ['maximieren', Square, t('browser.fenster.maximieren')],
          ] as const
        ).map(([aktion, Symbol, name]) => (
          <Kurzinfo key={aktion} text={name} seite="ende" aussen="h-full">
            <button
              type="button"
              onClick={() => void nativ.fensterAktion(aktion)}
              className="flex w-11 items-center justify-center text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
              aria-label={name}
            >
              <Symbol className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </Kurzinfo>
        ))}
        <Kurzinfo text={t('browser.fenster.schliessen')} seite="ende" aussen="h-full">
          <button
            type="button"
            onClick={() => void nativ.fensterAktion('schliessen')}
            className="flex w-11 items-center justify-center text-on-surface-variant hover:bg-status-destructive hover:text-white"
            aria-label={t('browser.fenster.schliessen')}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </Kurzinfo>
      </div>
    </div>
  )
}
