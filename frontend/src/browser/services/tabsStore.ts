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

import { useEinstellungenStore, wirksameSucheJetzt } from './einstellungenStore'
import { useFormulare } from './formulare'
import { interneAdresse, interneSeite, istIntern, istWebseite } from './intern'
import { nativ, SCHUTZ_NICHT_BEREIT, type TabEreignis } from './nativ'
import { useRueckfragen } from './rueckfragen'
import { baueZielUrl, type Suchmaschine } from './searchEngines'
import { leererTab, neueTabId, webviewZeigen, type Tab } from './tab'
import { ereignisAnwenden } from './tabEreignis'
import { istUnterfenster, setzeUnterfenster, tabsSpeichern } from './tabsAblage'
import { tabWeg as entwicklerTabWeg } from '../entwickler/werkzeuge'
import { useUebersetzung } from '../uebersetzung/ablauf'

interface Geschlossen {
  url: string
  privat: boolean
}

export type { Tab }

export interface NeuerTabOptionen {
  privat?: boolean
  hintergrund?: boolean
  nach?: string
  elternId?: string | null
  id?: string
  nativDa?: boolean
  anIndex?: number
  titel?: string
  favicon?: string | null
}

export type SplitRichtung = 'horizontal' | 'vertikal'

export interface SplitInfo {
  sekundaerId: string
  richtung: SplitRichtung
  anteil: number // 0.2 bis 0.8, Standard 0.5
  aktiveHaelfte: 'primaer' | 'sekundaer'
}

export interface TabsZustand {
  tabs: Tab[]
  aktivId: string
  verlaufIds: string[]
  geschlossen: Geschlossen[]
  split: SplitInfo | null

  neuerTab: (url?: string, optionen?: NeuerTabOptionen) => string
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
  /** Geht in der Seitenchronik zurück oder kehrt zum vorherigen Tab zurück. Wahr, wenn gehandelt wurde. */
  zurueckGehen: () => boolean
  /** Schaltet den Ton des Tabs stumm oder wieder an. */
  stummSchalten: (id: string) => void
  wiederherstellen: () => void
  wechseln: (richtung: 1 | -1) => void
  verschieben: (id: string, nachIndex: number, neueElternId?: string | null) => void
  /** Schaltet das Ein-/Ausklappen eines Eltern-Tabs in der Baumstruktur um. */
  baumUmschalten: (id: string) => void
  /** Setzt oder löst die Eltern-Kind-Beziehung eines Tabs in der Baumstruktur. */
  setzeElternTab: (id: string, elternId: string | null) => void
  /** Aktiviert die geteilte Ansicht mit einem zweiten Tab (Desktop & Mobile). */
  splitAktivieren: (sekundaerId: string, richtung?: SplitRichtung, anteil?: number) => void
  /** Hebt die geteilte Ansicht auf. */
  splitBeenden: () => void
  /** Ändert das Breiten- oder Höhenverhältnis (20 % bis 80 %). */
  splitAnteilAendern: (anteil: number) => void
  /** Wechselt zwischen horizontaler (nebeneinander) und vertikaler (übereinander) Teilung. */
  splitRichtungUmschalten: () => void
  /** Tauscht primären und sekundären Tab. */
  splitTauschen: () => void
  /** Setzt den Tastatur- und Omnibox-Fokus auf eine Hälfte der geteilten Ansicht. */
  splitFokusSetzen: (haelfte: 'primaer' | 'sekundaer') => void
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
      /** `rueck`: der Tab geht seinen gemerkten Rückweg und merkt sich danach keinen. */
      const laden = (id: string, url: string, rueck = false) => {
        const tab = finden(id)
        if (!tab) return
        if (istIntern(url)) {
          // Die Oberfläche zeichnet die Seite; eine Webview hätte hier nichts zu zeigen.
          if (tab.nativDa) void nativ.tabSchliessen(id)
          // Von einer Webseite in die MSM-Suche führt Zurück auf die Seite.
          const vorher = !rueck && interneSeite(url)?.seite === 'suche' && istWebseite(tab.url) ? tab.url : null
          aendern(id, { ...leererTab(id, tab.privat), url, vorher })
          zeigen(id)
          return
        }
        // Aus der MSM-Suche heraus merkt sich der Tab den Rückweg; eine
        // weitere Seite in derselben Webview behält ihn.
        const vorher = rueck ? null : interneSeite(tab.url)?.seite === 'suche' ? tab.url : tab.nativDa ? tab.vorher : null
        aendern(id, { url, laedt: true, abgestuerzt: false, fehler: null, nativDa: true, ruhe: null, vorher })
        void nativ
          .tabLaden(id, url, tab.privat)
          .then(() => zeigen(id))
          // Steht der Jugendschutz noch nicht, lädt Rust nichts und sagt es.
          .catch((e) => aendern(id, { laedt: false, ...(e === SCHUTZ_NICHT_BEREIT ? { fehler: e } : {}) }))
      }

      return {
        tabs: [leererTab(START_ID)],
        aktivId: START_ID,
        verlaufIds: [],
        geschlossen: [],
        split: null,

        bildsuche: (bild) => {
          const id = get().neuerTab()
          aendern(id, { url: bild.url, laedt: true, nativDa: true })
          void nativ
            .bildsuche(id, false, bild)
            .then(() => zeigen(id))
            .catch((e) => aendern(id, e === SCHUTZ_NICHT_BEREIT ? { laedt: false, fehler: e } : { laedt: false, url: '' }))
          return id
        },

        neuerTab: (url, optionen = {}) => {
          const id = optionen.id ?? neueTabId()
          const effektiveUrl = url || (optionen.nativDa ? 'about:blank' : '')
          const elternId = optionen.elternId !== undefined ? optionen.elternId : (optionen.nach ?? null)
          const tab: Tab = {
            ...leererTab(id, optionen.privat ?? false),
            url: effektiveUrl,
            titel: optionen.titel || (effektiveUrl ? (interneSeite(effektiveUrl) ? '' : effektiveUrl) : ''),
            favicon: optionen.favicon ?? null,
            laedt: Boolean(optionen.nativDa && effektiveUrl),
            nativDa: optionen.nativDa ?? false,
            elternId,
          }
          set((s) => {
            let einfuegeIndex = s.tabs.length
            if (typeof optionen.anIndex === 'number') {
              einfuegeIndex = Math.max(0, Math.min(optionen.anIndex, s.tabs.length))
            } else if (optionen.nach) {
              const nachIndex = s.tabs.findIndex((t) => t.id === optionen.nach)
              if (nachIndex >= 0) {
                let letzterIndex = nachIndex
                while (letzterIndex + 1 < s.tabs.length && s.tabs[letzterIndex + 1].elternId === optionen.nach) {
                  letzterIndex++
                }
                einfuegeIndex = letzterIndex + 1
              }
            }
            const tabs = [...s.tabs]
            tabs.splice(einfuegeIndex, 0, tab)
            const verlaufIds = optionen.hintergrund
              ? s.verlaufIds
              : [...s.verlaufIds.filter((v) => v !== s.aktivId), s.aktivId]
            return { tabs, aktivId: optionen.hintergrund ? s.aktivId : id, verlaufIds }
          })
          if (optionen.nativDa) {
            // Die Webview existiert bereits in Rust und navigiert.
            if (!optionen.hintergrund) {
              get().aktivieren(id)
            }
          } else {
            if (!optionen.hintergrund) zeigen(id)
            if (url) {
              if (optionen.hintergrund) {
                // Erst laden, wenn er nach vorne kommt: spart Speicher und Netz.
                aendern(id, { url, titel: optionen.titel || url })
              } else {
                laden(id, url)
              }
            }
          }
          return id
        },

        schliessen: (id) => {
          const { tabs, aktivId, verlaufIds, split } = get()
          if (split && (split.sekundaerId === id || aktivId === id)) {
            void nativ.tabsSplitAufheben().catch(() => null)
            set({ split: null })
          }
          const index = tabs.findIndex((t) => t.id === id)
          if (index < 0) return
          const tab = tabs[index]
          void nativ.tabSchliessen(id)
          useRueckfragen.getState().tabWeg(id)
          entwicklerTabWeg(id)
          useFormulare.getState().tabWeg(id)
          useUebersetzung.getState().seiteWeg(id)
          // Kind-Tabs an den übergeordneten Eltern-Tab vererben
          const rest = tabs.filter((t) => t.id !== id).map((t) => (t.elternId === id ? { ...t, elternId: tab.elternId } : t))
          const restVerlauf = verlaufIds.filter((v) => v !== id)
          if (tab.privat && !rest.some((t) => t.privat)) {
            // Die private Sitzung ist zu (Android leert ihr Profil): keine ihrer Seiten lässt sich zurückholen.
            set((s) => ({ geschlossen: s.geschlossen.filter((g) => !g.privat), verlaufIds: restVerlauf }))
          } else if (tab.url) {
            set((s) => ({ geschlossen: [{ url: tab.url, privat: tab.privat }, ...s.geschlossen].slice(0, 20), verlaufIds: restVerlauf }))
          } else {
            set({ verlaufIds: restVerlauf })
          }
          if (rest.length === 0) {
            if (istUnterfenster()) {
              void nativ.fensterAktion('schliessen')
              return
            }
            const neu = leererTab(neueTabId())
            set({ tabs: [neu], aktivId: neu.id, verlaufIds: [] })
            zeigen(neu.id)
            return
          }
          if (aktivId === id) {
            const vorherigerId = [...restVerlauf].reverse().find((vid) => rest.some((t) => t.id === vid))
            const naechster = vorherigerId ? rest.find((t) => t.id === vorherigerId)! : rest[Math.min(index, rest.length - 1)]
            set({ tabs: rest, aktivId: naechster.id, verlaufIds: restVerlauf.filter((v) => v !== naechster.id) })
            get().aktivieren(naechster.id)
          } else {
            set({ tabs: rest })
          }
        },

        aktivieren: (id) => {
          const tab = finden(id)
          if (!tab) return
          const { aktivId, verlaufIds, split } = get()
          if (split) {
            if (id === split.sekundaerId) {
              set((s) => ({ split: s.split ? { ...s.split, aktiveHaelfte: 'sekundaer' } : null }))
              return
            } else if (id === aktivId) {
              set((s) => ({ split: s.split ? { ...s.split, aktiveHaelfte: 'primaer' } : null }))
              return
            } else {
              // Ein dritter Tab wurde angeklickt: Ersetze die aktuell fokussierte Hälfte des Splits
              if (split.aktiveHaelfte === 'sekundaer') {
                set((s) => ({
                  split: s.split ? { ...s.split, sekundaerId: id } : null,
                  verlaufIds: [...verlaufIds.filter((v) => v !== id), id],
                }))
                const seite = !!tab.url && !istIntern(tab.url)
                if (seite && !tab.nativDa) {
                  void nativ.tabLaden(tab.id, tab.url, tab.privat).catch(() => null)
                  aendern(tab.id, { nativDa: true })
                }
                return
              } else {
                set({
                  aktivId: id,
                  verlaufIds: [...verlaufIds.filter((v) => v !== aktivId), aktivId],
                })
                const seite = !!tab.url && !istIntern(tab.url)
                if (seite && !tab.nativDa) {
                  laden(id, tab.url)
                  return
                }
                zeigen(id)
                return
              }
            }
          }
          if (aktivId !== id) {
            set({ aktivId: id, verlaufIds: [...verlaufIds.filter((v) => v !== aktivId), aktivId] })
          } else {
            set({ aktivId: id })
          }
          const seite = !!tab.url && !istIntern(tab.url)
          if (seite && !tab.nativDa) {
            // Wiederhergestellter oder im Hintergrund geöffneter Tab.
            laden(id, tab.url)
            return
          }
          zeigen(id)
        },

        eingeben: (eingabe, id) => {
          const s = get()
          const zielId = id ?? (s.split && s.split.aktiveHaelfte === 'sekundaer' ? s.split.sekundaerId : s.aktivId)
          const intern = interneSeite(eingabe.trim())
          if (intern) {
            // Die Suche behält ihren Begriff, samt Schreibweise.
            laden(zielId, intern.seite === 'suche' ? eingabe.trim() : interneAdresse(intern.seite, intern.teil))
            return
          }
          const { searxngUrl } = useEinstellungenStore.getState()
          const ziel = baueZielUrl(eingabe, wirksameSucheJetzt(), searxngUrl ?? undefined)
          if (!ziel) return
          laden(zielId, ziel)
        },

        oeffnen: (url, id) => {
          const s = get()
          const zielId = id ?? (s.split && s.split.aktiveHaelfte === 'sekundaer' ? s.split.sekundaerId : s.aktivId)
          laden(zielId, url)
        },

        startseite: (id) => {
          const s = get()
          const zielId = id ?? (s.split && s.split.aktiveHaelfte === 'sekundaer' ? s.split.sekundaerId : s.aktivId)
          const tab = finden(zielId)
          if (!tab) return
          if (tab.nativDa) void nativ.tabSchliessen(zielId)
          aendern(zielId, { ...leererTab(zielId, tab.privat) })
          zeigen(zielId)
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

        aktion: (akt, id) => {
          const s = get()
          const zielId = id ?? (s.split && s.split.aktiveHaelfte === 'sekundaer' ? s.split.sekundaerId : s.aktivId)
          const tab = finden(zielId)
          if (akt === 'zurueck' && tab && !tab.zurueck && tab.vorher) {
            laden(zielId, tab.vorher, true)
            return
          }
          if (!tab?.nativDa) return
          if (akt === 'neu_laden' && (tab.abgestuerzt || tab.fehler)) {
            laden(zielId, tab.url)
            return
          }
          void nativ.tabAktion(zielId, akt)
        },

        zurueckGehen: () => {
          const { tabs, aktivId, verlaufIds } = get()
          const tab = finden(aktivId)
          if (tab && (tab.zurueck || tab.vorher)) {
            get().aktion('zurueck')
            return true
          }
          const vorherige = [...verlaufIds].reverse().find((vid) => vid !== aktivId && tabs.some((t) => t.id === vid))
          if (vorherige) {
            const neueVerlauf = verlaufIds.filter((v) => v !== vorherige && v !== aktivId)
            if (tab && interneSeite(tab.url)?.seite === 'einstellungen') {
              get().schliessen(aktivId)
              return true
            }
            set({ aktivId: vorherige, verlaufIds: neueVerlauf })
            const zielTab = finden(vorherige)
            if (zielTab) {
              const seite = !!zielTab.url && !istIntern(zielTab.url)
              if (seite && !zielTab.nativDa) {
                laden(vorherige, zielTab.url)
              } else {
                zeigen(vorherige)
              }
            }
            return true
          }
          if (tab && interneSeite(tab.url)?.seite === 'einstellungen') {
            if (tabs.length > 1) {
              get().schliessen(aktivId)
              return true
            }
            get().startseite(aktivId)
            return true
          }
          if (tab && tab.url) {
            get().startseite(aktivId)
            return true
          }
          return false
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

        verschieben: (id, nachIndex, neueElternId) => {
          set((s) => {
            const tabs = [...s.tabs]
            const von = tabs.findIndex((t) => t.id === id)
            if (von < 0) return s
            const [tab] = tabs.splice(von, 1)
            if (neueElternId !== undefined) {
              tab.elternId = neueElternId
            }
            tabs.splice(Math.max(0, Math.min(nachIndex, tabs.length)), 0, tab)
            return { tabs }
          })
        },

        baumUmschalten: (id) =>
          set((s) => ({
            tabs: s.tabs.map((t) => (t.id === id ? { ...t, baumEingeklappt: !t.baumEingeklappt } : t)),
          })),

        setzeElternTab: (id, elternId) => {
          if (id === elternId) return
          set((s) => ({
            tabs: s.tabs.map((t) => (t.id === id ? { ...t, elternId } : t)),
          }))
        },

        splitAktivieren: (sekundaerId, richtung, anteil = 0.5) => {
          const { aktivId, tabs } = get()
          if (sekundaerId === aktivId) return
          const sek = tabs.find((t) => t.id === sekundaerId)
          if (!sek) return

          // Standard-Richtung adaptiv ermitteln:
          // Hochformat (Höhe > Breite) -> 'vertikal'
          // Querformat / Tablet / Desktop -> 'horizontal'
          const breite = typeof window !== 'undefined' ? window.innerWidth : 1200
          const hoehe = typeof window !== 'undefined' ? window.innerHeight : 800
          const gewaehlteRichtung: SplitRichtung = richtung ?? (hoehe > breite ? 'vertikal' : 'horizontal')

          const clampedAnteil = Math.max(0.2, Math.min(0.8, anteil))
          set({
            split: {
              sekundaerId,
              richtung: gewaehlteRichtung,
              anteil: clampedAnteil,
              aktiveHaelfte: 'primaer',
            },
          })

          if (sek.url && !istIntern(sek.url) && !sek.nativDa) {
            void nativ.tabLaden(sek.id, sek.url, sek.privat).catch(() => null)
            aendern(sek.id, { nativDa: true })
          }
          const prim = tabs.find((t) => t.id === aktivId)
          if (prim && prim.url && !istIntern(prim.url) && !prim.nativDa) {
            void nativ.tabLaden(prim.id, prim.url, prim.privat).catch(() => null)
            aendern(prim.id, { nativDa: true })
          }
        },

        splitBeenden: () => {
          void nativ.tabsSplitAufheben().catch(() => null)
          set({ split: null })
        },

        splitAnteilAendern: (anteil) => {
          const s = get().split
          if (!s) return
          const clamped = Math.max(0.2, Math.min(0.8, Math.round(anteil * 100) / 100))
          set({ split: { ...s, anteil: clamped } })
        },

        splitRichtungUmschalten: () => {
          const s = get().split
          if (!s) return
          set({ split: { ...s, richtung: s.richtung === 'horizontal' ? 'vertikal' : 'horizontal' } })
        },

        splitTauschen: () => {
          const { split, aktivId, tabs } = get()
          if (!split) return
          const neueAktivId = split.sekundaerId
          const neueSekundaerId = aktivId
          set({
            aktivId: neueAktivId,
            split: {
              ...split,
              sekundaerId: neueSekundaerId,
              aktiveHaelfte: split.aktiveHaelfte === 'primaer' ? 'sekundaer' : 'primaer',
            },
          })
          const neuerAktiv = tabs.find((t) => t.id === neueAktivId)
          if (neuerAktiv && neuerAktiv.url && !istIntern(neuerAktiv.url) && !neuerAktiv.nativDa) {
            void nativ.tabLaden(neuerAktiv.id, neuerAktiv.url, neuerAktiv.privat).catch(() => null)
            aendern(neuerAktiv.id, { nativDa: true })
          }
        },

        splitFokusSetzen: (haelfte) => {
          const s = get().split
          if (!s || s.aktiveHaelfte === haelfte) return
          set({ split: { ...s, aktiveHaelfte: haelfte } })
        },

        ereignis: (e) =>
          ereignisAnwenden(e, {
            finden,
            aendern,
            zeigen,
            neuerTab: get().neuerTab,
            schliessen: get().schliessen,
          }),

        hochfahren: async () => {
          const startInfo = await nativ.fensterStartInfo().catch(() => null)
          if (startInfo?.ist_neues_fenster) {
            setzeUnterfenster(true)
            const neuId = neueTabId()
            const hatUrl = Boolean(startInfo.start_url && (istWebseite(startInfo.start_url) || istIntern(startInfo.start_url)))
            const zielUrl = hatUrl ? startInfo.start_url! : ''
            const tab: Tab = {
              ...leererTab(neuId, startInfo.privat),
              url: zielUrl,
              titel: zielUrl || '',
            }
            set({
              tabs: [tab],
              aktivId: neuId,
              geschlossen: [],
              verlaufIds: [],
            })
            if (hatUrl) {
              laden(neuId, zielUrl)
            } else {
              zeigen(neuId)
            }
            return
          }

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
  return useTabsStore((s) => {
    const split = s.split
    if (split && split.aktiveHaelfte === 'sekundaer') {
      return s.tabs.find((t) => t.id === split.sekundaerId) ?? s.tabs.find((t) => t.id === s.aktivId)
    }
    return s.tabs.find((t) => t.id === s.aktivId)
  })
}

export function useSplitZustand() {
  return useTabsStore((s) => {
    const split = s.split
    return {
      split,
      primaerTab: s.tabs.find((t) => t.id === s.aktivId),
      sekundaerTab: split ? s.tabs.find((t) => t.id === split.sekundaerId) : undefined,
    }
  })
}

/** Eine Seite aus einer Liste oder Kachel: im vorderen Tab, mit Strg oder mittlerer Taste in einem neuen. */
export function seiteOeffnen(url: string, neuerTab: boolean) {
  const tabs = useTabsStore.getState()
  if (neuerTab) tabs.neuerTab(url, { hintergrund: true })
  else tabs.oeffnen(url)
}
