/**
 * Vertikale Tableiste mit Baumstruktur (Tree-Style Tabs im Microsoft Edge-Design).
 *
 * Zeigt geöffnete Tabs und Unterseiten (z. B. GitHub-Dateien, Unterseiten,
 * geöffnete Links) hierarchisch eingerückt an.
 *
 * - Ausgeklappt: Nahtlose Frame-Integration (bg-surface-container), prominenter
 *   „Neuer Tab“-Eintrag mit Tastenkürzel Strg+T, Pfeil-Schaltfläche zum Einklappen,
 *   Favicons, Schließen-Knöpfe, Umsortieren und Fenster-Auslagerung.
 * - Eingeklappt: Schmale Icon-Leiste mit Favicons. Tooltips werden sowohl via DOM
 *   als auch nativ über das Betriebssystem gerendert, damit sie nicht von der
 *   Seitenfläche (WebView2) verdeckt werden.
 */
import { Fragment, useEffect, useMemo, useState, type DragEvent } from 'react'
import {
  ArrowLeftToLine,
  ChevronDown,
  ChevronRight,
  EyeOff,
  Moon,
  PanelLeftOpen,
  Plus,
  X,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Kurzinfo } from '@/Singra/UI'

import { useLeistenname, useLeistennameAufraeumen } from '../leiste/leistenname'
import { useEinstellungenStore } from '../services/einstellungenStore'
import { nativ } from '../services/nativ'
import { istAndroid } from '../services/plattform'
import {
  beendeTabDrag,
  entpackeTabTransfer,
  FENSTER_ID,
  istTabUebernommen,
  lauscheTabTransfer,
  lauscheTabZiehNachrichten,
  packeTabTransfer,
  registriereTabDrag,
  sendeTabUebernommen,
  sendeTabZiehNachricht,
  TAB_MIME_TYP,
  type TabTransferDaten,
} from '../services/tabTransfer'
import { useTabsStore, type Tab } from '../services/tabsStore'
import { erstelleTabPhantom, TabSymbol, tabTitel, TonKnopf } from './Kopfleiste'

export interface TabBaumKnoten {
  tab: Tab
  ebene: number
  hatKinder: boolean
  sichtbar: boolean
  kinderCount: number
}

/**
 * Erstellt eine geordnete Baum-Liste aus den flachen Tabs anhand ihrer `elternId`.
 */
export function baueTabBaum(tabs: Tab[]): TabBaumKnoten[] {
  const kinderMap = new Map<string, Tab[]>()
  const rootTabs: Tab[] = []
  const tabIds = new Set(tabs.map((t) => t.id))

  for (const t of tabs) {
    if (t.elternId && tabIds.has(t.elternId) && t.elternId !== t.id) {
      const arr = kinderMap.get(t.elternId) ?? []
      arr.push(t)
      kinderMap.set(t.elternId, arr)
    } else {
      rootTabs.push(t)
    }
  }

  const ergebnis: TabBaumKnoten[] = []

  function durchlaufe(tab: Tab, ebene: number, vorfahreEingeklappt: boolean) {
    const kinder = kinderMap.get(tab.id) ?? []
    const hatKinder = kinder.length > 0
    const sichtbar = !vorfahreEingeklappt

    ergebnis.push({
      tab,
      ebene,
      hatKinder,
      sichtbar,
      kinderCount: kinder.length,
    })

    const naechsteEingeklappt = vorfahreEingeklappt || Boolean(tab.baumEingeklappt)
    for (const kind of kinder) {
      durchlaufe(kind, ebene + 1, naechsteEingeklappt)
    }
  }

  for (const r of rootTabs) {
    durchlaufe(r, 0, false)
  }

  return ergebnis
}

interface VertikalerDragZustand {
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

let aktiverVertikalerDrag: VertikalerDragZustand | null = null

interface VertikalerTabKnotenProps {
  knoten: TabBaumKnoten
  aktiv: boolean
  titel: string
  vertikaleTabsEingeklappt: boolean
  i: number
  dropZielIndex: number | null
  dropAlsKindVon: string | null
  dragStart: (e: DragEvent<HTMLDivElement>, tab: Tab, titel: string) => void
  dragEnde: (e: DragEvent<HTMLDivElement>, tab: Tab, titel: string) => void
  behandleDrop: (e: DragEvent, zielIndex: number, elternId?: string | null) => void
  setDropZielIndex: (val: number | null) => void
  setDropAlsKindVon: (val: string | null) => void
  aktivieren: (id: string) => void
  schliessen: (id: string) => void
  baumUmschalten: (id: string) => void
}

/**
 * Einzelner Knoten im vertikalen Tab-Baum.
 *
 * Verwendet `useLeistenname(titel, 'rechts')`, damit Tooltips auch auf Windows
 * nativ über die WebView2-Fläche gerendert werden.
 */
function VertikalerTabKnoten({
  knoten,
  aktiv,
  titel,
  vertikaleTabsEingeklappt,
  i,
  dropZielIndex,
  dropAlsKindVon,
  dragStart,
  dragEnde,
  behandleDrop,
  setDropZielIndex,
  setDropAlsKindVon,
  aktivieren,
  schliessen,
  baumUmschalten,
}: VertikalerTabKnotenProps) {
  const { t } = useTranslation()
  const tab = knoten.tab
  const leistenname = useLeistenname(titel, 'rechts')

  if (vertikaleTabsEingeklappt) {
    // EINGEKLAPPTER MODUS: Nur Favicon, Tooltip nativ und via Kurzinfo
    return (
      <Kurzinfo text={titel} lage="rechts">
        <button
          type="button"
          role="treeitem"
          aria-selected={aktiv}
          onClick={() => aktivieren(tab.id)}
          onAuxClick={(e) => e.button === 1 && schliessen(tab.id)}
          {...leistenname}
          className={`group relative flex h-9 w-full items-center justify-center rounded-lg transition-colors ${
            aktiv
              ? 'bg-surface-container-highest text-primary shadow-sm'
              : 'text-on-surface-variant hover:bg-surface-container-high/80 hover:text-on-surface'
          }`}
          aria-label={titel}
        >
          <span className="flex h-4 w-4 shrink-0 items-center justify-center">
            <TabSymbol tab={tab} />
          </span>
          {knoten.hatKinder && (
            <span
              className="absolute bottom-1 right-1 h-1.5 w-1.5 rounded-full bg-primary/80"
              aria-hidden="true"
            />
          )}
        </button>
      </Kurzinfo>
    )
  }

  // AUSGEKLAPPTER MODUS: Baum-Einrückung, Titel, Favicon, Schließen-Button
  return (
    <Fragment>
      {dropZielIndex === i && (
        <div
          data-testid="vertikal-drop-indikator"
          className="my-0.5 h-0.5 w-full rounded-full bg-primary shadow-[0_0_8px_rgba(99,102,241,0.8)]"
        />
      )}
      <div
        role="treeitem"
        aria-selected={aktiv}
        tabIndex={aktiv ? 0 : -1}
        draggable
        onDragStart={(e) => dragStart(e, tab, titel)}
        onDragEnd={(e) => dragEnde(e, tab, titel)}
        onDragOver={(e) => {
          e.preventDefault()
          if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
          const rect = e.currentTarget.getBoundingClientRect()
          const relY = e.clientY - rect.top
          if (relY < rect.height * 0.3) {
            setDropZielIndex(i)
            setDropAlsKindVon(null)
          } else if (relY > rect.height * 0.7) {
            setDropZielIndex(i + 1)
            setDropAlsKindVon(null)
          } else {
            setDropZielIndex(i + 1)
            setDropAlsKindVon(tab.id)
          }
        }}
        onDrop={(e) => {
          e.stopPropagation()
          behandleDrop(e, dropZielIndex ?? i + 1, dropAlsKindVon)
        }}
        onClick={() => aktivieren(tab.id)}
        onAuxClick={(e) => e.button === 1 && schliessen(tab.id)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') aktivieren(tab.id)
        }}
        {...leistenname}
        style={{ paddingLeft: `${knoten.ebene * 14 + 8}px` }}
        className={`group flex h-8 items-center gap-1.5 rounded-lg pr-1.5 text-label-sm cursor-default transition-all duration-150 ${
          aktiv
            ? 'bg-surface-container-highest text-on-surface font-medium shadow-sm'
            : 'text-on-surface-variant hover:bg-surface-container-high/60 hover:text-on-surface'
        }`}
      >
        {/* Chevron zum Ein-/Ausklappen von Kind-Zweigen */}
        {knoten.hatKinder ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              baumUmschalten(tab.id)
            }}
            className="flex h-4 w-4 shrink-0 items-center justify-center rounded text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
            aria-label={
              tab.baumEingeklappt
                ? t('browser.tabs.baumAusklappen')
                : t('browser.tabs.baumEinklappen')
            }
          >
            {tab.baumEingeklappt ? (
              <ChevronRight className="h-3 w-3" aria-hidden="true" />
            ) : (
              <ChevronDown className="h-3 w-3" aria-hidden="true" />
            )}
          </button>
        ) : knoten.ebene > 0 ? (
          <span className="w-4 shrink-0 text-outline-variant/60 text-center select-none" aria-hidden="true">
            └
          </span>
        ) : (
          <span className="w-1 shrink-0" />
        )}

        {/* Favicon */}
        <span className={`flex h-4 w-4 shrink-0 items-center justify-center ${tab.ruhe ? 'opacity-50' : ''}`}>
          <TabSymbol tab={tab} />
        </span>

        {/* Titel */}
        <span className={`min-w-0 flex-1 truncate ${tab.ruhe ? 'opacity-60' : ''}`}>
          {tab.privat && <span className="sr-only">{t('browser.tabs.privat')}: </span>}
          {titel}
          {tab.ruhe && <span className="sr-only">, {t(`browser.tabs.ruhe.${tab.ruhe}`)}</span>}
        </span>

        {/* Anzahl eingeklappter Kind-Tabs */}
        {knoten.hatKinder && tab.baumEingeklappt && (
          <span className="rounded bg-surface-container-high px-1 text-label-sm font-medium text-on-surface-variant">
            +{knoten.kinderCount}
          </span>
        )}

        {/* Ton / Ruhe */}
        {tab.ruhe && <Moon className="h-3 w-3 shrink-0 text-on-surface-variant" aria-hidden="true" />}
        <TonKnopf tab={tab} titel={titel} />

        {/* Schließen-Knopf */}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            schliessen(tab.id)
          }}
          className={`flex h-5 w-5 shrink-0 items-center justify-center rounded hover:bg-surface-container-high hover:text-status-destructive ${
            aktiv ? 'opacity-80 group-hover:opacity-100' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100'
          }`}
          aria-label={t('browser.tabs.schliessen', { titel })}
        >
          <X className="h-3 w-3" aria-hidden="true" />
        </button>
      </div>
    </Fragment>
  )
}

export function VertikaleTableiste() {
  const { t } = useTranslation()
  useLeistennameAufraeumen()

  const tabs = useTabsStore((s) => s.tabs)
  const aktivId = useTabsStore((s) => s.aktivId)
  const neuerTab = useTabsStore((s) => s.neuerTab)
  const schliessen = useTabsStore((s) => s.schliessen)
  const aktivieren = useTabsStore((s) => s.aktivieren)
  const baumUmschalten = useTabsStore((s) => s.baumUmschalten)
  const verschieben = useTabsStore((s) => s.verschieben)

  const vertikaleTabsEingeklappt = useEinstellungenStore((s) => s.vertikaleTabsEingeklappt)
  const setzen = useEinstellungenStore((s) => s.setzen)
  const privatAus = useEinstellungenStore((s) => s.ausgeblendet.includes('privaterTab'))

  const [dropZielIndex, setDropZielIndex] = useState<number | null>(null)
  const [dropAlsKindVon, setDropAlsKindVon] = useState<string | null>(null)

  const baumKnoten = useMemo(() => baueTabBaum(tabs), [tabs])

  const einklappenNamen = useLeistenname(t('browser.tabs.leisteEinklappen'), 'rechts')
  const ausklappenNamen = useLeistenname(t('browser.tabs.leisteAusklappen'), 'rechts')
  const neuTabNamen = useLeistenname(t('browser.tabs.neuKuerzel'), 'rechts')

  useEffect(() => {
    const abmeldenTransfer = lauscheTabTransfer(({ tabId }) => {
      if (aktiverVertikalerDrag && aktiverVertikalerDrag.id === tabId) {
        aktiverVertikalerDrag.imBalkenAbgelegt = true
        aktiverVertikalerDrag.aufraeumen?.()
      }
      useTabsStore.getState().schliessen(tabId)
    })

    const abmeldenLive = lauscheTabZiehNachrichten((msg) => {
      if (msg.ursprungFensterId === FENSTER_ID) return

      if (msg.art === 'abbrechen' || msg.art === 'ablegen') {
        setDropZielIndex(null)
        setDropAlsKindVon(null)
      }
    })

    return () => {
      abmeldenTransfer()
      abmeldenLive()
    }
  }, [])

  const behandleDrop = (e: DragEvent, zielIndex: number, elternId: string | null = null) => {
    e.preventDefault()
    e.stopPropagation()
    setDropZielIndex(null)
    setDropAlsKindVon(null)

    const transfer = entpackeTabTransfer(e.dataTransfer)
    if (!transfer) return

    // 1. Aus einem anderen Fenster zusammenführen (Re-docking)
    if (transfer.ursprungFensterId !== FENSTER_ID) {
      neuerTab(transfer.url, {
        anIndex: zielIndex,
        elternId,
        titel: transfer.titel,
        favicon: transfer.favicon,
        privat: transfer.privat,
      })
      sendeTabUebernommen(transfer.ursprungFensterId, transfer.tabId)
      beendeTabDrag()
      return
    }

    // 2. Innerhalb dieses Fensters verschieben
    if (aktiverVertikalerDrag) {
      aktiverVertikalerDrag.imBalkenAbgelegt = true
      aktiverVertikalerDrag.aufraeumen?.()
    }
    const von = tabs.findIndex((t) => t.id === transfer.tabId)
    if (von >= 0) {
      const korrigiert = von < zielIndex ? zielIndex - 1 : zielIndex
      verschieben(transfer.tabId, korrigiert, elternId)
    }
  }

  const dragStart = (e: DragEvent<HTMLDivElement>, tab: Tab, titel: string) => {
    if (e.button !== undefined && e.button !== 0) return
    const rect = e.currentTarget.getBoundingClientRect()
    const offsetRelX = e.clientX - rect.left
    const offsetRelY = e.clientY - rect.top

    const phantom = erstelleTabPhantom(titel, tab.favicon)
    document.body.appendChild(phantom)
    if (typeof e.dataTransfer?.setDragImage === 'function') {
      e.dataTransfer.setDragImage(phantom, offsetRelX, offsetRelY)
    }

    const transferPayload: TabTransferDaten = {
      ursprungFensterId: FENSTER_ID,
      tabId: tab.id,
      url: tab.url,
      titel,
      favicon: tab.favicon,
      privat: tab.privat,
    }

    if (e.dataTransfer) {
      const payloadJson = packeTabTransfer(transferPayload)
      e.dataTransfer.setData(TAB_MIME_TYP, payloadJson)
      e.dataTransfer.setData('text/plain', payloadJson)
    }

    const drag: VertikalerDragZustand = {
      id: tab.id,
      startX: e.screenX,
      startY: e.screenY,
      letzteScreenX: e.screenX,
      letzteScreenY: e.screenY,
      letzteClientX: e.clientX,
      letzteClientY: e.clientY,
      imBalkenAbgelegt: false,
      abgebrochen: false,
      aufraeumen: () => phantom.remove(),
    }
    aktiverVertikalerDrag = drag
    registriereTabDrag(transferPayload)

    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') {
        drag.abgebrochen = true
        phantom.remove()
        window.removeEventListener('keydown', onKey)
        sendeTabZiehNachricht({
          art: 'abbrechen',
          ursprungFensterId: FENSTER_ID,
          tabId: tab.id,
          url: tab.url,
          titel,
          favicon: tab.favicon,
          privat: tab.privat,
          screenX: 0,
          screenY: 0,
        })
      }
    }
    drag.aufraeumenEscape = () => window.removeEventListener('keydown', onKey)
    window.addEventListener('keydown', onKey)
  }

  const dragEnde = (e: DragEvent<HTMLDivElement>, tab: Tab, _titel: string) => {
    const drag = aktiverVertikalerDrag
    aktiverVertikalerDrag = null
    beendeTabDrag()
    drag?.aufraeumen?.()
    drag?.aufraeumenEscape?.()

    if (!drag || istAndroid() || drag.abgebrochen || drag.imBalkenAbgelegt) return

    if (istTabUebernommen(drag.id)) {
      schliessen(drag.id)
      return
    }

    if (tabs.length <= 1) return

    const posScreenX = typeof e.screenX === 'number' ? e.screenX : 0
    const posScreenY = typeof e.screenY === 'number' ? e.screenY : 0
    const hatScreen = posScreenX !== 0 || posScreenY !== 0
    const screenX = hatScreen ? posScreenX : drag.letzteScreenX
    const screenY = hatScreen ? posScreenY : drag.letzteScreenY
    const distanz = Math.hypot(screenX - drag.startX, screenY - drag.startY)

    if (distanz >= 50) {
      void nativ
        .tabAuslagern(tab.url, screenX, screenY, tab.privat)
        .then(() => schliessen(tab.id))
        .catch((err) => console.error('[MSB] Tab konnte nicht ausgelagert werden:', err))
    }
  }

  return (
    <aside
      aria-label={t('browser.tabs.liste')}
      className={`relative flex flex-col shrink-0 border-r border-outline-variant/20 bg-surface-container transition-all duration-200 ease-in-out select-none ${
        vertikaleTabsEingeklappt ? 'w-12' : 'w-60'
      }`}
    >
      {/* Kopfbereich der vertikalen Leiste im Edge-Stil */}
      <div className="flex h-10 shrink-0 items-center justify-between px-2">
        {vertikaleTabsEingeklappt ? (
          <Kurzinfo text={t('browser.tabs.leisteAusklappen')} lage="rechts">
            <button
              type="button"
              onClick={() => setzen({ vertikaleTabsEingeklappt: false })}
              className="flex h-8 w-8 mx-auto items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface transition-colors"
              aria-label={t('browser.tabs.leisteAusklappen')}
              {...ausklappenNamen}
            >
              <PanelLeftOpen className="h-4 w-4" aria-hidden="true" />
            </button>
          </Kurzinfo>
        ) : (
          <div className="flex w-full items-center justify-between">
            <Kurzinfo text={t('browser.tabs.leisteEinklappen')} lage="unten">
              <button
                type="button"
                onClick={() => setzen({ vertikaleTabsEingeklappt: true })}
                className="flex h-7 w-7 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface transition-colors"
                aria-label={t('browser.tabs.leisteEinklappen')}
                {...einklappenNamen}
              >
                <ArrowLeftToLine className="h-4 w-4" aria-hidden="true" />
              </button>
            </Kurzinfo>
            {!privatAus && (
              <Kurzinfo text={t('browser.tabs.privatKuerzel')} lage="unten">
                <button
                  type="button"
                  onClick={() => neuerTab(undefined, { privat: true })}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container-high hover:text-primary transition-colors"
                  aria-label={t('browser.tabs.neuPrivat')}
                >
                  <EyeOff className="h-4 w-4" aria-hidden="true" />
                </button>
              </Kurzinfo>
            )}
          </div>
        )}
      </div>

      {/* Prominenter "Neuer Tab" Button im ausgeklappten Zustand (Edge-Stil) */}
      {!vertikaleTabsEingeklappt && (
        <div className="px-2 pb-1">
          <button
            type="button"
            onClick={() => neuerTab()}
            className="group flex h-8 w-full items-center gap-2 rounded-lg px-2 text-label-sm font-medium text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface transition-colors"
            aria-label={t('browser.tabs.neu')}
          >
            <Plus className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
            <span className="flex-1 text-left truncate">{t('browser.tabs.neu')}</span>
            <kbd className="text-label-sm text-on-surface-variant/70 tracking-wider">Strg+T</kbd>
          </button>
        </div>
      )}

      {/* Liste der Baumstruktur-Tabs */}
      <div
        role="tree"
        aria-label={t('browser.tabs.liste')}
        onDragOver={(e) => {
          e.preventDefault()
          if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
        }}
        onDrop={(e) => behandleDrop(e, tabs.length)}
        className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-1.5 py-1 scrollbar-none [overscroll-behavior:contain]"
      >
        {baumKnoten.map((knoten, i) => {
          if (!knoten.sichtbar) return null
          const tab = knoten.tab
          const aktiv = tab.id === aktivId
          const titel = tabTitel(tab, {
            neu: t('browser.tabs.neu'),
            einstellungen: t('browser.start.anpassen'),
          })

          return (
            <VertikalerTabKnoten
              key={tab.id}
              knoten={knoten}
              aktiv={aktiv}
              titel={titel}
              vertikaleTabsEingeklappt={vertikaleTabsEingeklappt}
              i={i}
              dropZielIndex={dropZielIndex}
              dropAlsKindVon={dropAlsKindVon}
              dragStart={dragStart}
              dragEnde={dragEnde}
              behandleDrop={behandleDrop}
              setDropZielIndex={setDropZielIndex}
              setDropAlsKindVon={setDropAlsKindVon}
              aktivieren={aktivieren}
              schliessen={schliessen}
              baumUmschalten={baumUmschalten}
            />
          )
        })}

        {/* Plus-Knopf unterhalb im eingeklappten Zustand */}
        {vertikaleTabsEingeklappt && (
          <div className="mt-1 flex flex-col items-center border-t border-outline-variant/20 pt-1.5">
            <Kurzinfo text={t('browser.tabs.neuKuerzel')} lage="rechts">
              <button
                type="button"
                onClick={() => neuerTab()}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface transition-colors"
                aria-label={t('browser.tabs.neu')}
                {...neuTabNamen}
              >
                <Plus className="h-4 w-4" aria-hidden="true" />
              </button>
            </Kurzinfo>
          </div>
        )}
      </div>
    </aside>
  )
}
