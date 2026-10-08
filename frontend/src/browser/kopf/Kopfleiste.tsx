/**
 * Die oberste Leiste: Tabs, „Neuer Tab“ und die Fensterknöpfe. Das Fenster
 * hat keinen Rahmen des Systems; gezogen wird an der freien Fläche
 * (`data-tauri-drag-region`), ein Doppelklick maximiert.
 */
import { useState, type DragEvent } from 'react'
import { EyeOff, Globe, Loader2, Minus, Plus, Square, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Kurzinfo } from '@/Singra/UI'

import { nativ } from '../services/nativ'
import { useTabsStore, type Tab } from '../services/tabsStore'

export function tabTitel(tab: Tab, neu: string): string {
  if (tab.titel) return tab.titel
  if (!tab.url) return neu
  try {
    return new URL(tab.url).host || tab.url
  } catch {
    return tab.url
  }
}

function TabSymbol({ tab }: { tab: Tab }) {
  const [kaputt, setKaputt] = useState<string | null>(null)
  if (tab.laedt) return <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" aria-hidden="true" />
  if (tab.privat) return <EyeOff className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
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

function TabReiter({ tab, aktiv, index }: { tab: Tab; aktiv: boolean; index: number }) {
  const { t } = useTranslation()
  const { aktivieren, schliessen, verschieben } = useTabsStore.getState()
  const titel = tabTitel(tab, t('browser.tabs.neu'))

  const ablegen = (e: DragEvent) => {
    const id = e.dataTransfer.getData('text/msb-tab')
    if (id && id !== tab.id) verschieben(id, index)
  }

  return (
    <div
      role="tab"
      aria-selected={aktiv}
      tabIndex={aktiv ? 0 : -1}
      draggable
      onDragStart={(e) => e.dataTransfer.setData('text/msb-tab', tab.id)}
      onDragOver={(e) => e.preventDefault()}
      onDrop={ablegen}
      onClick={() => aktivieren(tab.id)}
      onAuxClick={(e) => e.button === 1 && schliessen(tab.id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') aktivieren(tab.id)
      }}
      className={`group flex h-8 min-w-[7.5rem] max-w-[14rem] flex-1 cursor-default items-center gap-2 rounded-t-md border-x border-t px-2.5 text-label-md ${
        aktiv
          ? 'border-outline-variant bg-surface-container text-on-surface'
          : 'border-transparent text-on-surface-variant hover:bg-surface-container-low'
      }`}
    >
      <span className="flex h-4 w-4 shrink-0 items-center justify-center">
        <TabSymbol tab={tab} />
      </span>
      <span className="min-w-0 flex-1 truncate">
        {tab.privat && <span className="sr-only">{t('browser.tabs.privat')}: </span>}
        {titel}
      </span>
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

  return (
    <div
      data-tauri-drag-region
      onDoubleClick={(e) => e.target === e.currentTarget && void nativ.fensterAktion('maximieren')}
      className="flex h-10 shrink-0 select-none items-end gap-1 border-b border-outline-variant bg-surface-container-lowest pl-2"
    >
      <img src="/msp.png" alt="" className="mb-2 h-5 w-5 shrink-0" data-tauri-drag-region />
      <div role="tablist" aria-label={t('browser.tabs.liste')} className="flex min-w-0 items-end gap-0.5 overflow-hidden">
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
