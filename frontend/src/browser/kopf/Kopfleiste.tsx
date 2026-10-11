/**
 * Die oberste Leiste: Tabs, „Neuer Tab“ und die Fensterknöpfe. Das Fenster
 * hat keinen Rahmen des Systems; gezogen wird an der freien Fläche
 * (`data-tauri-drag-region`), ein Doppelklick maximiert.
 */
import { useState, type DragEvent } from 'react'
import { EyeOff, Globe, Loader2, Minus, Moon, Plus, Search, Settings, Square, Volume2, VolumeX, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Kurzinfo } from '@/Singra/UI'

import { useEinstellungenStore } from '../services/einstellungenStore'
import { interneSeite, msmSucheBegriff } from '../services/intern'
import { nativ } from '../services/nativ'
import { istAndroid } from '../services/plattform'
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
function TonKnopf({ tab, titel }: { tab: Tab; titel: string }) {
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

function TabReiter({ tab, aktiv, index }: { tab: Tab; aktiv: boolean; index: number }) {
  const { t } = useTranslation()
  const { aktivieren, schliessen, verschieben } = useTabsStore.getState()
  const titel = tabTitel(tab, { neu: t('browser.tabs.neu'), einstellungen: t('browser.einstellungen.titel') })

  const ablegen = (e: DragEvent) => {
    if (aktiverTabDrag) {
      aktiverTabDrag.imBalkenAbgelegt = true
    }
    const id = e.dataTransfer.getData('text/msb-tab')
    if (id && id !== tab.id) verschieben(id, index)
  }

  const dragStart = (e: DragEvent) => {
    try {
      e.dataTransfer?.setData('text/msb-tab', tab.id)
      if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'
    } catch {
      // In manchen Umgebungen ohne volles DragEvent ignorieren
    }

    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape' && aktiverTabDrag) {
        aktiverTabDrag.abgebrochen = true
      }
    }
    window.addEventListener('keydown', onKey, { capture: true })

    const sx = typeof e.screenX === 'number' ? e.screenX : 0
    const sy = typeof e.screenY === 'number' ? e.screenY : 0
    const cx = typeof e.clientX === 'number' ? e.clientX : 0
    const cy = typeof e.clientY === 'number' ? e.clientY : 0

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
      aufraeumenEscape: () => window.removeEventListener('keydown', onKey, { capture: true }),
    }
  }

  const beimZiehen = (e: DragEvent) => {
    if (!aktiverTabDrag) return
    const sx = typeof e.screenX === 'number' ? e.screenX : 0
    const sy = typeof e.screenY === 'number' ? e.screenY : 0
    const cx = typeof e.clientX === 'number' ? e.clientX : 0
    const cy = typeof e.clientY === 'number' ? e.clientY : 0

    if (sx !== 0 || sy !== 0) {
      aktiverTabDrag.letzteScreenX = sx
      aktiverTabDrag.letzteScreenY = sy
    }
    if (cx !== 0 || cy !== 0) {
      aktiverTabDrag.letzteClientX = cx
      aktiverTabDrag.letzteClientY = cy
    }
  }

  const dragEnde = (e: DragEvent) => {
    const drag = aktiverTabDrag
    aktiverTabDrag = null
    drag?.aufraeumenEscape?.()

    if (!drag) return
    if (istAndroid()) return
    if (drag.abgebrochen) return
    if (drag.imBalkenAbgelegt) return

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

  return (
    <div
      role="tab"
      aria-selected={aktiv}
      tabIndex={aktiv ? 0 : -1}
      draggable
      onDragStart={dragStart}
      onDrag={beimZiehen}
      onDragEnd={dragEnde}
      onDragOver={(e) => e.preventDefault()}
      onDrop={ablegen}
      onClick={() => aktivieren(tab.id)}
      onAuxClick={(e) => e.button === 1 && schliessen(tab.id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') aktivieren(tab.id)
      }}
      className={`group flex h-8 min-w-[7.5rem] max-w-[14rem] flex-1 cursor-default items-center gap-2 rounded-t-lg px-2.5 text-label-md ${
        aktiv ? 'bg-surface-container text-on-surface' : 'text-on-surface-variant hover:bg-surface-container/60'
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

export function Kopfleiste() {
  const { t } = useTranslation()
  const tabs = useTabsStore((s) => s.tabs)
  const aktivId = useTabsStore((s) => s.aktivId)
  const neuerTab = useTabsStore((s) => s.neuerTab)
  // Ausgeblendet ist nur der Knopf; Strg+Umschalt+N öffnet weiter einen privaten Tab.
  const privatAus = useEinstellungenStore((s) => s.ausgeblendet.includes('privaterTab'))

  return (
    <div
      data-tauri-drag-region
      onDoubleClick={(e) => e.target === e.currentTarget && void nativ.fensterAktion('maximieren')}
      onDragOver={(e) => e.preventDefault()}
      onDrop={() => {
        if (aktiverTabDrag) aktiverTabDrag.imBalkenAbgelegt = true
      }}
      className="flex h-10 shrink-0 select-none items-end gap-1 bg-surface-container-low pl-2"
    >
      <img src="/msp.png" alt="" className="mb-2 h-5 w-5 shrink-0" data-tauri-drag-region />
      <div
        role="tablist"
        aria-label={t('browser.tabs.liste')}
        onDragOver={(e) => e.preventDefault()}
        onDrop={() => {
          if (aktiverTabDrag) aktiverTabDrag.imBalkenAbgelegt = true
        }}
        className="flex min-w-0 items-end gap-0.5 overflow-hidden"
      >
        {tabs.map((tab, i) => (
          <TabReiter key={tab.id} tab={tab} aktiv={tab.id === aktivId} index={i} />
        ))}
      </div>
      <div className="mb-1 flex shrink-0 items-center">
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
      <div className="h-full min-w-6 flex-1" data-tauri-drag-region />
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
