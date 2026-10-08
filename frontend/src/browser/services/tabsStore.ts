/**
 * Die Tabs des Browsers. Die Oberfläche ist die Wahrheit über Reihenfolge,
 * Titel und Kennungen; Rust zeigt nur an (`nativ.ts`).
 *
 * Ein Tab ohne Adresse ist die Startseite: dafür gibt es keine Webview, die
 * Oberfläche zeichnet sie selbst. Ebenso die Seiten des Browsers selbst
 * (`msb://einstellungen`, `intern.ts`). Sobald der Tab eine Seite öffnet,
 * entsteht die Webview, und ab dann meldet Rust, was darin geschieht.
 *
 * Normale Tabs überdauern einen Neustart (nur Adresse, Titel, Favicon).
 * Private Tabs nie, und ihre Seiten landen nicht im Verlauf.
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import { ablage, ADRESSE_MAX, kurzerTitel, passend } from './ablage'
import { useDownloadsStore } from './downloadsStore'
import { useEinstellungenStore } from './einstellungenStore'
import { useFormulare } from './formulare'
import { interneAdresse, interneSeite, istIntern } from './intern'
import { nativ, type TabEreignis } from './nativ'
import { useRueckfragen } from './rueckfragen'
import { baueZielUrl, type Suchmaschine } from './searchEngines'
import { useVerlaufStore } from './verlaufStore'
import { protokollEreignis, tabWeg as entwicklerTabWeg } from '../entwickler/werkzeuge'

export interface Tab {
  id: string
  /** Leer heißt: Startseite. */
  url: string
  titel: string
  favicon: string | null
  laedt: boolean
  zurueck: boolean
  vor: boolean
  privat: boolean
  /** In diesem Tab seit dem letzten Seitenwechsel geblockt. */
  werbung: number
  tracker: number
  abgestuerzt: boolean
  /** Die Seite ließ sich nicht laden (`grund` aus `ohne_edge.rs`) oder ist `gesperrt`. */
  fehler: string | null
  /** Warum der Jugend- und Suchtschutz die Seite sperrt: Kategorie oder `eigene`. */
  gesperrt: string | null
  /** Ziel des Links unter dem Zeiger. */
  status: string
  /** Suche in der Seite; `aktuell` ab 0. */
  treffer: { aktuell: number; anzahl: number } | null
  /** Rust hat für diesen Tab eine Webview. */
  nativDa: boolean
  /** Die Seite spielt Ton. */
  ton: boolean
  stumm: boolean
  /** Eingefroren (`schlaf`) oder ohne Webview, um Speicher zu sparen (`verworfen`). */
  ruhe: 'schlaf' | 'verworfen' | null
}

interface Geschlossen {
  url: string
  privat: boolean
}

interface TabsZustand {
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

export function neueTabId(): string {
  return `tab-${crypto.randomUUID()}`
}

function leererTab(id: string, privat = false): Tab {
  return {
    id,
    url: '',
    titel: '',
    favicon: null,
    laedt: false,
    zurueck: false,
    vor: false,
    privat,
    werbung: 0,
    tracker: 0,
    abgestuerzt: false,
    fehler: null,
    gesperrt: null,
    status: '',
    treffer: null,
    nativDa: false,
    ton: false,
    stumm: false,
    ruhe: null,
  }
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

      /** Lädt eine Adresse im Tab; legt dabei die Webview an, falls nötig. */
      /**
       * Nach `tab_laden`: die Webview nach vorn, wenn der Tab noch vorn ist und
       * keine Seite der Oberfläche zeigt. Unter Android meldet Rust die Sperre,
       * bevor `tab_laden` zurückkommt; die leere Webview lag sonst über der
       * Sperrseite (bis 09.10.2026).
       */
      const nachVorn = (id: string) => (get().aktivId === id && !finden(id)?.fehler ? nativ.tabAktivieren(id) : null)

      const laden = (id: string, url: string) => {
        const tab = finden(id)
        if (!tab) return
        if (istIntern(url)) {
          // Die Oberfläche zeichnet die Seite; eine Webview hätte hier nichts zu zeigen.
          if (tab.nativDa) void nativ.tabSchliessen(id)
          aendern(id, { ...leererTab(id, tab.privat), url })
          if (get().aktivId === id) void nativ.tabAktivieren(null)
          return
        }
        aendern(id, { url, laedt: true, abgestuerzt: false, fehler: null, nativDa: true, ruhe: null })
        void nativ
          .tabLaden(id, url, tab.privat)
          .then(() => nachVorn(id))
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
            .then(() => nachVorn(id))
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
          if (!optionen.hintergrund) void nativ.tabAktivieren(null)
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
            void nativ.tabAktivieren(null)
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
          // Startseite, eigene Seiten, Absturz und Fehlerseite zeichnet die
          // Oberfläche; die Webview käme sonst darüber.
          void nativ.tabAktivieren(seite && !tab.abgestuerzt && !tab.fehler ? id : null)
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
          if (get().aktivId === id) void nativ.tabAktivieren(null)
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

        ereignis: (e) => {
          const tab = finden(e.id)
          if (!tab) return
          switch (e.art) {
            case 'laedt':
              aendern(e.id, { laedt: true, url: e.url, abgestuerzt: false, fehler: null, status: '' })
              useFormulare.getState().laedt(e.id)
              // Zurück aus einer Fehlerseite: die Webview wieder nach vorn.
              if (tab.fehler && get().aktivId === e.id) void nativ.tabAktivieren(e.id)
              break
            case 'fehlerseite':
              aendern(e.id, { laedt: false, fehler: e.grund, url: e.url || tab.url })
              if (get().aktivId === e.id) void nativ.tabAktivieren(null)
              break
            case 'gesperrt':
              aendern(e.id, { laedt: false, fehler: 'gesperrt', gesperrt: e.grund, url: e.url })
              if (get().aktivId === e.id) void nativ.tabAktivieren(null)
              break
            case 'status':
              aendern(e.id, { status: e.text })
              break
            case 'treffer':
              aendern(e.id, { treffer: { aktuell: e.aktuell, anzahl: e.anzahl } })
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
              aendern(e.id, { laedt: false })
              if (!tab.privat && e.url.startsWith('http')) {
                useVerlaufStore.getState().besucht(e.url, finden(e.id)?.titel || e.url)
              }
              break
            case 'adresse':
              aendern(e.id, { url: e.url, zurueck: e.zurueck, vor: e.vor })
              break
            case 'titel':
              aendern(e.id, { titel: e.titel })
              if (!tab.privat && tab.url.startsWith('http') && e.titel) {
                useVerlaufStore.getState().titelNachtragen(tab.url, e.titel)
              }
              break
            case 'favicon':
              aendern(e.id, { favicon: e.url })
              break
            case 'neuer_tab':
              // Eigene Seiten öffnet nur die Oberfläche, nie eine Webseite.
              if (istIntern(e.url)) break
              get().neuerTab(e.url, { privat: tab.privat, nach: e.id })
              break
            case 'schild':
              aendern(e.id, { werbung: e.werbung, tracker: e.tracker })
              break
            case 'absturz':
              aendern(e.id, { abgestuerzt: true, laedt: false })
              if (get().aktivId === e.id) void nativ.tabAktivieren(null)
              break
            case 'download':
              useDownloadsStore.getState().ereignis(e)
              break
            case 'ton':
              aendern(e.id, { ton: e.spielt, stumm: e.stumm })
              break
            case 'schlaf':
              if (tab.ruhe !== 'verworfen') aendern(e.id, { ruhe: e.schlaeft ? 'schlaf' : null })
              break
            case 'verworfen':
              aendern(e.id, { ruhe: 'verworfen', nativDa: false, laedt: false, ton: false })
              break
            default:
              break
          }
        },

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
    {
      name: 'msb:tabs',
      version: 1,
      storage: ablage,
      partialize: (s) => {
        const tabs = s.tabs
          .filter((t) => !t.privat)
          .map((t) => ({
            ...leererTab(t.id),
            // Zu lang (eine Seite kann das): beim nächsten Start nur die Herkunft.
            url: t.url.length > ADRESSE_MAX ? herkunftVon(t.url) : t.url,
            titel: kurzerTitel(t.titel),
            favicon: t.favicon && t.favicon.length <= FAVICON_MAX ? t.favicon : null,
          }))
        const aktivId = tabs.some((t) => t.id === s.aktivId) ? s.aktivId : tabs[0]?.id
        return { tabs, aktivId }
      },
      merge: (gespeichert, aktuell) => {
        const g = (gespeichert ?? {}) as { tabs?: unknown; aktivId?: unknown }
        // Nur Tabs mit gültiger Kennung und von ihnen nur, was `partialize`
        // schreibt, mit dem richtigen Typ; der Rest kommt aus dem leeren Tab.
        const tabs = (Array.isArray(g.tabs) ? g.tabs : [])
          .filter((t): t is { id: string } => typeof t?.id === 'string' && /^tab-[A-Za-z0-9-]{1,40}$/.test(t.id))
          .map((t) => ({ ...leererTab(t.id), ...passend({ url: '', titel: '', favicon: null as string | null }, t) }))
        const aktivId = tabs.find((t) => t.id === g.aktivId)?.id
        return aktivId ? { ...aktuell, tabs, aktivId } : aktuell
      },
    },
  ),
)

/** Ein Favicon (unter Android eine Data-Adresse) wird nur bis zu dieser Länge gespeichert. */
const FAVICON_MAX = 64 * 1024

function herkunftVon(url: string): string {
  try {
    return `${new URL(url).origin}/`
  } catch {
    return ''
  }
}

export function useAktiverTab(): Tab | undefined {
  return useTabsStore((s) => s.tabs.find((t) => t.id === s.aktivId))
}

/** Eine Seite aus einer Liste oder Kachel: im vorderen Tab, mit Strg oder mittlerer Taste in einem neuen. */
export function seiteOeffnen(url: string, neuerTab: boolean) {
  const tabs = useTabsStore.getState()
  if (neuerTab) tabs.neuerTab(url, { hintergrund: true })
  else tabs.oeffnen(url)
}
