/**
 * Was Rust aus einem Tab meldet (`TabEreignis`, `tabs/ereignis.rs`), auf den
 * Tab und die Stores verteilt, die es angeht: Rückfragen, Formulare,
 * Downloads, Verlauf und Entwicklerwerkzeuge. Den Zustand des Tabs ändert nur
 * `tabsStore`; hier steht, was ein Ereignis bedeutet.
 */
import { useDownloadsStore } from './downloadsStore'
import { useFormulare } from './formulare'
import { istIntern, istWebseite } from './intern'
import type { TabEreignis } from './nativ'
import { useRueckfragen } from './rueckfragen'
import type { Tab } from './tab'
import { useVerlaufStore } from './verlaufStore'
import { protokollEreignis } from '../entwickler/werkzeuge'

/** Was `tabsStore` dafür hergibt. */
export interface TabZugriff {
  finden: (id: string) => Tab | undefined
  aendern: (id: string, teil: Partial<Tab>) => void
  /** Webview des vorderen Tabs zeigen oder verbergen (`webviewZeigen`). */
  zeigen: (id: string) => void
  neuerTab: (url: string, optionen: { privat: boolean; nach: string }) => string
}

export function ereignisAnwenden(e: TabEreignis, z: TabZugriff): void {
  const tab = z.finden(e.id)
  if (!tab) return
  switch (e.art) {
    case 'laedt':
      z.aendern(e.id, { laedt: true, url: e.url, abgestuerzt: false, fehler: null, status: '' })
      useFormulare.getState().laedt(e.id)
      // Zurück aus einer Fehlerseite: die Webview wieder nach vorn.
      if (tab.fehler) z.zeigen(e.id)
      break
    case 'fehlerseite':
      z.aendern(e.id, { laedt: false, fehler: e.grund, url: e.url || tab.url })
      z.zeigen(e.id)
      break
    case 'gesperrt':
      z.aendern(e.id, { laedt: false, fehler: 'gesperrt', gesperrt: e.grund, url: e.url })
      z.zeigen(e.id)
      break
    case 'status':
      z.aendern(e.id, { status: e.text })
      break
    case 'treffer':
      z.aendern(e.id, { treffer: { aktuell: e.aktuell, anzahl: e.anzahl } })
      break
    case 'kontextmenue':
    case 'dialog':
    case 'recht':
    case 'anmeldung':
      useRueckfragen.getState().aufnehmen(e)
      break
    case 'formular':
      useFormulare.getState().ereignis(e)
      break
    case 'protokoll':
      protokollEreignis(e.id, e.methode, e.daten)
      break
    case 'geladen':
      z.aendern(e.id, { laedt: false })
      if (!tab.privat && istWebseite(e.url)) {
        useVerlaufStore.getState().besucht(e.url, z.finden(e.id)?.titel || e.url)
      }
      break
    case 'adresse':
      z.aendern(e.id, { url: e.url, zurueck: e.zurueck, vor: e.vor })
      break
    case 'titel':
      z.aendern(e.id, { titel: e.titel })
      if (!tab.privat && istWebseite(tab.url) && e.titel) {
        useVerlaufStore.getState().titelNachtragen(tab.url, e.titel)
      }
      break
    case 'favicon':
      z.aendern(e.id, { favicon: e.url })
      break
    case 'neuer_tab':
      // Eigene Seiten öffnet nur die Oberfläche, nie eine Webseite.
      if (istIntern(e.url)) break
      z.neuerTab(e.url, { privat: tab.privat, nach: e.id })
      break
    case 'schild':
      z.aendern(e.id, { werbung: e.werbung, tracker: e.tracker })
      break
    case 'absturz':
      z.aendern(e.id, { abgestuerzt: true, laedt: false })
      z.zeigen(e.id)
      break
    case 'download':
      useDownloadsStore.getState().ereignis(e)
      break
    case 'ton':
      z.aendern(e.id, { ton: e.spielt, stumm: e.stumm })
      break
    case 'schlaf':
      if (tab.ruhe !== 'verworfen') z.aendern(e.id, { ruhe: e.schlaeft ? 'schlaf' : null })
      break
    case 'verworfen':
      z.aendern(e.id, { ruhe: 'verworfen', nativDa: false, laedt: false, ton: false })
      break
    default:
      break
  }
}
