/**
 * Die Tabs des Browsers. Die Oberfläche ist die Wahrheit über Reihenfolge,
 * Titel und Kennungen; Rust zeigt nur an (`nativ.ts`).
 *
 * Ein Tab ohne Adresse ist die Startseite: dafür gibt es keine Webview, die
 * Oberfläche zeichnet sie selbst. Ebenso die Seiten des Browsers selbst
 * (`msb://einstellungen`, `intern.ts`). Sobald der Tab eine Seite öffnet,
 * entsteht die Webview, und ab dann meldet Rust, was darin geschieht
 * (`tabEreignis.ts`).
 *
 * Normale Tabs überdauern einen Neustart (`tabsAblage.ts`). Private Tabs
 * nie, und ihre Seiten landen nicht im Verlauf.
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import { useEinstellungenStore } from './einstellungenStore'
import { useFormulare } from './formulare'
import { interneAdresse, interneSeite, istIntern } from './intern'
import { nativ, type TabEreignis } from './nativ'
import { useRueckfragen } from './rueckfragen'
import { baueZielUrl, type Suchmaschine } from './searchEngines'
import { leererTab, neueTabId, webviewZeigen, type Tab } from './tab'
import { ereignisAnwenden } from './tabEreignis'
import { tabsSpeichern } from './tabsAblage'
import { tabWeg as entwicklerTabWeg } from '../entwickler/werkzeuge'

interface Geschlossen {
  url: string
  privat: boolean
}

export type { Tab }

export interface TabsZustand {
  tabs: Tab[]
  aktivId: string
  geschlossen: Geschlossen[]

  neuerTab: (url?: string, optionen?: { privat?: boolean; hintergrund?: boolean; nach?: string }) => string
  schliessen: (id: string) => void
  aktivieren: (id: string) => void
  /** Eingabe aus Adresszeile oder Startseite: Adresse oder Suchbegriff. */
  eingeben: (eingabe: string, id?: string) => void
  oeffnen: (url: string, id?: string) => void
  startseite: (id?: string) => void
  /** Öffnet die Einstellungen (`teil`: Kategorie) im vorhandenen Tab oder einem neuen. */
  einstellungen: (teil?: string) => void
  /** Ein neuer Tab, der das Foto des Such-Widgets an die Bildsuche schickt (`widget.rs`). */
  bildsuche: (bild: NonNullable<Suchmaschine['bild']>) => string
  aktion: (aktion: 'zurueck' | 'vor' | 'neu_laden' | 'anhalten', id?: string) => void
  /** Schaltet den Ton des Tabs stumm oder wieder an. */
  stummSchalten: (id: string) => void
  wiederherstellen: () => void
  wechseln: (richtung: 1 | -1) => void
  verschieben: (id: string, nachIndex: number) => void
  ereignis: (e: TabEreignis) => void
  /** Nach dem Start: Rust kennt noch keine Webview, die Oberfläche schon Tabs. */
  hochfahren: () => Promise<void>
}

const START_ID = neueTabId()

/**
 * Wahr nur beim ersten Hochfahren nach dem Start der App; ein Neuladen der
 * Oberfläche (nach der Kopplung) behält `sessionStorage` und ist keiner.
 */
function ersterStart(): boolean {
  const schluessel = 'msb:gestartet'
  if (sessionStorage.getItem(schluessel)) return false
  sessionStorage.setItem(schluessel, '1')
  return true
}

export const useTabsStore = create<TabsZustand>()(
  persist(
    (set, get) => {
      const aendern = (id: string, teil: Partial<Tab>) =>
        set((s) => ({ tabs: s.tabs.map((t) => (t.id === id ? { ...t, ...teil } : t)) }))

      const finden = (id: string) => get().tabs.find((t) => t.id === id)

      /**
       * Ist der Tab vorn, zeigt Rust seine Webview oder keine. Gelesen wird
       * der Stand nach der Änderung: unter Android meldet Rust eine Sperre,
       * bevor `tab_laden` zurückkommt, und die leere Webview lag sonst über
       * der Sperrseite (bis 09.10.2026).
       */
      const zeigen = (id: string) => {
        if (get().aktivId !== id) return
        const tab = finden(id)
        void nativ.tabAktivieren(tab && webviewZeigen(tab) ? id : null)
      }

      /** Lädt eine Adresse im Tab; legt dabei die Webview an, falls nötig. */
      const laden = (id: string, url: string) => {
        const tab = finden(id)
        if (!tab) return
        if (istIntern(url)) {
          // Die Oberfläche zeichnet die Seite; eine Webview hätte hier nichts zu zeigen.
          if (tab.nativDa) void nativ.tabSchliessen(id)
          aendern(id, { ...leererTab(id, tab.privat), url })
          zeigen(id)
          return
        }
        aendern(id, { url, laedt: true, abgestuerzt: false, fehler: null, nativDa: true, ruhe: null })
        void nativ
          .tabLaden(id, url, tab.privat)
          .then(() => zeigen(id))
          .catch(() => aendern(id, { laedt: false }))
      }

      return {
        tabs: [leererTab(START_ID)],
        aktivId: START_ID,
        geschlossen: [],

        bildsuche: (bild) => {
          const id = get().neuerTab()
          aendern(id, { url: bild.url, laedt: true, nativDa: true })
          void nativ
            .bildsuche(id, false, bild)
            .then(() => zeigen(id))
            .catch(() => aendern(id, { laedt: false, url: '' }))
          return id
        },

        neuerTab: (url, optionen = {}) => {
          const id = neueTabId()
          const tab = leererTab(id, optionen.privat ?? false)
          set((s) => {
            const nachIndex = optionen.nach ? s.tabs.findIndex((t) => t.id === optionen.nach) : -1
            const tabs = [...s.tabs]
            tabs.splice(nachIndex >= 0 ? nachIndex + 1 : tabs.length, 0, tab)
            return { tabs, aktivId: optionen.hintergrund ? s.aktivId : id }
          })
          if (!optionen.hintergrund) zeigen(id)
          if (url) {
            if (optionen.hintergrund) {
              // Erst laden, wenn er nach vorne kommt: spart Speicher und Netz.
              aendern(id, { url, titel: url })
            } else {
              laden(id, url)
            }
          }
          return id
        },

        schliessen: (id) => {
          const { tabs, aktivId } = get()
          const index = tabs.findIndex((t) => t.id === id)
          if (index < 0) return
          const tab = tabs[index]
          void nativ.tabSchliessen(id)
          useRueckfragen.getState().tabWeg(id)
          entwicklerTabWeg(id)
          useFormulare.getState().tabWeg(id)
          const rest = tabs.filter((t) => t.id !== id)
          if (tab.privat && !rest.some((t) => t.privat)) {
            // Die private Sitzung ist zu (Android leert ihr Profil): keine ihrer Seiten lässt sich zurückholen.
            set((s) => ({ geschlossen: s.geschlossen.filter((g) => !g.privat) }))
          } else if (tab.url) {
            set((s) => ({ geschlossen: [{ url: tab.url, privat: tab.privat }, ...s.geschlossen].slice(0, 20) }))
          }
          if (rest.length === 0) {
            const neu = leererTab(neueTabId())
            set({ tabs: [neu], aktivId: neu.id })
            zeigen(neu.id)
            return
          }
          if (aktivId === id) {
            const naechster = rest[Math.min(index, rest.length - 1)]
            set({ tabs: rest })
            get().aktivieren(naechster.id)
          } else {
            set({ tabs: rest })
          }
        },

        aktivieren: (id) => {
          const tab = finden(id)
          if (!tab) return
          set({ aktivId: id })
          const seite = !!tab.url && !istIntern(tab.url)
          if (seite && !tab.nativDa) {
            // Wiederhergestellter oder im Hintergrund geöffneter Tab.
            laden(id, tab.url)
            return
          }
          zeigen(id)
        },

        eingeben: (eingabe, id = get().aktivId) => {
          const intern = interneSeite(eingabe.trim())
          if (intern) {
            laden(id, interneAdresse(intern.seite, intern.teil))
            return
          }
          const { suchmaschine, searxngUrl } = useEinstellungenStore.getState()
          const ziel = baueZielUrl(eingabe, suchmaschine, searxngUrl ?? undefined)
          if (!ziel) return
          laden(id, ziel)
        },

        oeffnen: (url, id = get().aktivId) => laden(id, url),

        startseite: (id = get().aktivId) => {
          const tab = finden(id)
          if (!tab) return
          if (tab.nativDa) void nativ.tabSchliessen(id)
          aendern(id, { ...leererTab(id, tab.privat) })
          zeigen(id)
        },

        einstellungen: (teil) => {
          const adresse = interneAdresse('einstellungen', teil)
          const { tabs, aktivId } = get()
          const offen = tabs.find((t) => !t.privat && interneSeite(t.url)?.seite === 'einstellungen')
          if (offen) {
            if (teil) laden(offen.id, adresse)
            get().aktivieren(offen.id)
            return
          }
          const aktiv = finden(aktivId)
          if (aktiv && !aktiv.url && !aktiv.privat) laden(aktivId, adresse)
          else get().neuerTab(adresse)
        },

        aktion: (aktion, id = get().aktivId) => {
          const tab = finden(id)
          if (!tab?.nativDa) return
          if (aktion === 'neu_laden' && (tab.abgestuerzt || tab.fehler)) {
            laden(id, tab.url)
            return
          }
          void nativ.tabAktion(id, aktion)
        },

        wiederherstellen: () => {
          const [letzter, ...rest] = get().geschlossen
          if (!letzter) return
          set({ geschlossen: rest })
          get().neuerTab(letzter.url, { privat: letzter.privat })
        },

        stummSchalten: (id) => {
          const tab = finden(id)
          if (tab?.nativDa) void nativ.tabStumm(id, !tab.stumm).catch(() => null)
        },

        wechseln: (richtung) => {
          const { tabs, aktivId } = get()
          const index = tabs.findIndex((t) => t.id === aktivId)
          const ziel = tabs[(index + richtung + tabs.length) % tabs.length]
          if (ziel) get().aktivieren(ziel.id)
        },

        verschieben: (id, nachIndex) => {
          set((s) => {
            const tabs = [...s.tabs]
            const von = tabs.findIndex((t) => t.id === id)
            if (von < 0) return s
            const [tab] = tabs.splice(von, 1)
            tabs.splice(Math.max(0, Math.min(nachIndex, tabs.length)), 0, tab)
            return { tabs }
          })
        },

        ereignis: (e) => ereignisAnwenden(e, { finden, aendern, zeigen, neuerTab: get().neuerTab }),

        hochfahren: async () => {
          // Webviews aus einem früheren Lauf der Oberfläche (Neuladen nach
          // der Kopplung) gehören zu keinem Tab mehr.
          await nativ.tabsZuruecksetzen().catch(() => null)
          if (ersterStart() && useEinstellungenStore.getState().beimStart === 'startseite') {
            // Die Tabs vom letzten Mal bleiben über „Tab wiederherstellen“ erreichbar.
            const neu = leererTab(neueTabId())
            const vorher = get().tabs.filter((t) => t.url).map((t) => ({ url: t.url, privat: false }))
            set({ tabs: [neu], aktivId: neu.id, geschlossen: vorher.reverse().slice(0, 20) })
          } else {
            set((s) => ({ tabs: s.tabs.map((t) => ({ ...t, nativDa: false, laedt: false })) }))
          }
          get().aktivieren(get().aktivId)
        },
      }
    },
    tabsSpeichern,
  ),
)

export function useAktiverTab(): Tab | undefined {
  return useTabsStore((s) => s.tabs.find((t) => t.id === s.aktivId))
}

/** Eine Seite aus einer Liste oder Kachel: im vorderen Tab, mit Strg oder mittlerer Taste in einem neuen. */
export function seiteOeffnen(url: string, neuerTab: boolean) {
  const tabs = useTabsStore.getState()
  if (neuerTab) tabs.neuerTab(url, { hintergrund: true })
  else tabs.oeffnen(url)
}
