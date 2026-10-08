/**
 * Tastenkürzel des Browsers. Sie kommen auf zwei Wegen an: aus einer Seite
 * meldet Rust sie (`kuerzel` in `webview2.rs`, die Seite bekommt sie nie zu
 * sehen), in der Oberfläche fängt sie `useKuerzel` ab. Beide landen in
 * `kuerzelAusfuehren`, damit es eine Liste gibt und nicht zwei.
 */
import { useEffect } from 'react'

import { nativ } from './nativ'
import { useSuche } from './suche'
import { useTabsStore } from './tabsStore'
import { useVerlaufStore } from './verlaufStore'

export type Kuerzel =
  | 'neuer_tab'
  | 'privater_tab'
  | 'tab_schliessen'
  | 'tab_wiederherstellen'
  | 'naechster_tab'
  | 'voriger_tab'
  | 'adresszeile'
  | 'lesezeichen'
  | 'verlauf'
  | 'downloads'
  | 'singra'
  | 'neu_laden'
  | 'suchen'
  | 'weitersuchen'
  | 'zuruecksuchen'
  | 'drucken'
  | 'entwickler'
  | 'einstellungen'
  | 'still'

/** Was die Wurzel für Kürzel tun lässt, die über die Tabs hinausgehen. */
export interface KuerzelZiele {
  adresszeile: () => void
  panel: (welches: 'verlauf' | 'downloads' | 'singra' | 'entwickler') => void
}

export function kuerzelAusfuehren(taste: string, ziele: KuerzelZiele): void {
  const tabs = useTabsStore.getState()
  switch (taste as Kuerzel) {
    case 'neuer_tab':
      tabs.neuerTab()
      ziele.adresszeile()
      break
    case 'privater_tab':
      tabs.neuerTab(undefined, { privat: true })
      ziele.adresszeile()
      break
    case 'tab_schliessen':
      tabs.schliessen(tabs.aktivId)
      break
    case 'tab_wiederherstellen':
      tabs.wiederherstellen()
      break
    case 'naechster_tab':
      tabs.wechseln(1)
      break
    case 'voriger_tab':
      tabs.wechseln(-1)
      break
    case 'adresszeile':
      ziele.adresszeile()
      break
    case 'lesezeichen': {
      const tab = tabs.tabs.find((t) => t.id === tabs.aktivId)
      if (tab?.url.startsWith('http')) useVerlaufStore.getState().lesezeichenUmschalten(tab.url, tab.titel)
      break
    }
    case 'verlauf':
    case 'downloads':
    case 'singra':
      ziele.panel(taste as 'verlauf' | 'downloads' | 'singra')
      break
    case 'neu_laden':
      tabs.aktion('neu_laden')
      break
    case 'suchen':
      useSuche.getState().oeffnen()
      break
    case 'weitersuchen':
    case 'zuruecksuchen':
      if (useSuche.getState().offen) void nativ.tabSuchen(tabs.aktivId, taste === 'weitersuchen' ? 'weiter' : 'zurueck').catch(() => null)
      else useSuche.getState().oeffnen()
      break
    case 'drucken':
      void nativ.tabDrucken(tabs.aktivId).catch(() => null)
      break
    case 'entwickler':
      ziele.panel('entwickler')
      break
    case 'einstellungen':
      tabs.einstellungen()
      break
    case 'still':
      break
  }
}

/** Dieselbe Zuordnung wie `kuerzel` in `webview2.rs`. */
export function kuerzelAusTaste(e: KeyboardEvent): Kuerzel | null {
  const strg = e.ctrlKey || e.metaKey
  const taste = e.key.toLowerCase()
  if (strg && e.shiftKey) {
    if (taste === 't') return 'tab_wiederherstellen'
    if (taste === 'n') return 'privater_tab'
    if (taste === 'tab') return 'voriger_tab'
    if (taste === 'i' || taste === 'j' || taste === 'c') return 'entwickler'
    if (taste === 'g') return 'zuruecksuchen'
    return null
  }
  if (strg && !e.altKey) {
    if (taste === 't' || taste === 'n') return 'neuer_tab'
    if (taste === 'w') return 'tab_schliessen'
    if (taste === 'l') return 'adresszeile'
    if (taste === 'd') return 'lesezeichen'
    if (taste === 'h') return 'verlauf'
    if (taste === 'j') return 'downloads'
    if (taste === 'r') return 'neu_laden'
    if (taste === 'tab') return 'naechster_tab'
    if (taste === 'f') return 'suchen'
    if (taste === 'g') return 'weitersuchen'
    if (taste === 'p') return 'drucken'
    if (taste === ',') return 'einstellungen'
    return null
  }
  if (e.altKey && !strg && taste === 's') return 'singra'
  if (!strg && !e.altKey && e.key === 'F6') return 'adresszeile'
  if (!strg && !e.altKey && e.key === 'F5') return 'neu_laden'
  if (!strg && !e.altKey && e.key === 'F12') return 'entwickler'
  if (!strg && !e.altKey && e.key === 'F3') return e.shiftKey ? 'zuruecksuchen' : 'weitersuchen'
  if (!strg && !e.altKey && e.key === 'F7') return 'still'
  return null
}

/** Kürzel, solange der Fokus in der Oberfläche liegt. */
export function useKuerzel(ziele: KuerzelZiele): void {
  useEffect(() => {
    const beiTaste = (e: KeyboardEvent) => {
      const kuerzel = kuerzelAusTaste(e)
      if (!kuerzel) return
      e.preventDefault()
      kuerzelAusfuehren(kuerzel, ziele)
    }
    document.addEventListener('keydown', beiTaste, true)
    return () => document.removeEventListener('keydown', beiTaste, true)
  }, [ziele])
}
