/**
 * Die Konsole eines Tabs: Meldungen der Seite, Fehler, Eingaben und deren
 * Ergebnisse. Nur im Speicher, höchstens `MAX` je Tab.
 */
import { create } from 'zustand'

import type { Objekt, Stapel } from './protokoll'

export const MAX = 1000
const VERLAUF_MAX = 100

export type Stufe = 'log' | 'info' | 'warn' | 'error' | 'debug'

export interface Eintrag {
  nr: number
  art: 'meldung' | 'eingabe' | 'ergebnis' | 'navigiert' | 'geleert'
  stufe: Stufe
  /** Werte aus der Seite, so wie `console.log` sie bekam. */
  werte: Objekt[]
  /** Text ohne Objekt (Browsermeldung, Eingabe, Adresse). */
  text?: string
  quelle?: { url: string; zeile: number }
  stapel?: Stapel
  /** Gleiche Meldung direkt hintereinander zählt hoch statt neu. */
  anzahl: number
  /** Tiefe in `console.group`. */
  ebene: number
  /** Eine Gruppe, die eingeklappt beginnt (`groupCollapsed`). */
  gruppe?: 'offen' | 'zu'
}

interface KonsoleZustand {
  eintraege: Record<string, Eintrag[]>
  ebene: Record<string, number>
  beibehalten: boolean
  verlauf: string[]
  ereignis: (tab: string, methode: string, daten: Record<string, unknown>) => void
  navigiert: (tab: string, url: string) => void
  eingabe: (tab: string, text: string) => void
  ergebnis: (tab: string, wert: Objekt, fehler: boolean) => void
  leeren: (tab: string) => void
  setBeibehalten: (an: boolean) => void
  tabWeg: (tab: string) => void
}

const STUFE: Record<string, Stufe> = {
  error: 'error',
  assert: 'error',
  warning: 'warn',
  info: 'info',
  debug: 'debug',
  verbose: 'debug',
}

let zaehler = 0

/** Was zwei Meldungen gleich macht (für das Hochzählen). */
function abdruck(e: Omit<Eintrag, 'nr' | 'anzahl'>): string {
  const werte = e.werte.map((w) => (w.objectId ? `#${w.objectId}` : `${w.type}:${String(w.value ?? w.unserializableValue ?? w.description)}`))
  return JSON.stringify([e.art, e.stufe, e.text, werte, e.quelle, e.ebene, e.gruppe])
}

export const useKonsole = create<KonsoleZustand>()((set, get) => {
  const anhaengen = (tab: string, e: Omit<Eintrag, 'nr' | 'anzahl'>) =>
    set((s) => {
      const liste = s.eintraege[tab] ?? []
      const letzter = liste[liste.length - 1]
      if (letzter && !e.gruppe && e.art === 'meldung' && abdruck(letzter) === abdruck(e)) {
        return { eintraege: { ...s.eintraege, [tab]: [...liste.slice(0, -1), { ...letzter, anzahl: letzter.anzahl + 1 }] } }
      }
      const neu = [...liste, { ...e, nr: ++zaehler, anzahl: 1 }]
      return { eintraege: { ...s.eintraege, [tab]: neu.length > MAX ? neu.slice(neu.length - MAX) : neu } }
    })
  const ebene = (tab: string) => get().ebene[tab] ?? 0
  const setEbene = (tab: string, wert: number) => set((s) => ({ ebene: { ...s.ebene, [tab]: Math.max(0, wert) } }))

  return {
    eintraege: {},
    ebene: {},
    beibehalten: false,
    verlauf: [],
    ereignis: (tab, methode, d) => {
      if (methode === 'Runtime.consoleAPICalled') {
        const typ = String(d.type)
        const werte = (d.args as Objekt[] | undefined) ?? []
        const stapel = d.stackTrace as Stapel | undefined
        const oben = stapel?.callFrames[0]
        const quelle = oben?.url ? { url: oben.url, zeile: oben.lineNumber } : undefined
        if (typ === 'clear') {
          set((s) => ({ eintraege: { ...s.eintraege, [tab]: [] }, ebene: { ...s.ebene, [tab]: 0 } }))
          anhaengen(tab, { art: 'geleert', stufe: 'info', werte: [], ebene: 0 })
          return
        }
        if (typ === 'endGroup') return setEbene(tab, ebene(tab) - 1)
        const gruppe = typ === 'startGroup' ? 'offen' : typ === 'startGroupCollapsed' ? 'zu' : undefined
        anhaengen(tab, {
          art: 'meldung',
          stufe: STUFE[typ] ?? 'log',
          werte,
          quelle,
          // Ein Fehler, `console.trace` und `assert` zeigen ihren Stapel.
          stapel: typ === 'trace' || typ === 'error' || typ === 'assert' ? stapel : undefined,
          ebene: ebene(tab),
          gruppe,
        })
        if (gruppe) setEbene(tab, ebene(tab) + 1)
      } else if (methode === 'Runtime.exceptionThrown') {
        const x = d.exceptionDetails as { text: string; exception?: Objekt; url?: string; lineNumber: number; stackTrace?: Stapel }
        anhaengen(tab, {
          art: 'meldung',
          stufe: 'error',
          werte: x.exception ? [x.exception] : [],
          text: x.exception ? undefined : x.text,
          quelle: x.url ? { url: x.url, zeile: x.lineNumber } : undefined,
          stapel: x.stackTrace,
          ebene: 0,
        })
      } else if (methode === 'Log.entryAdded') {
        const l = d.entry as { level: string; text: string; url?: string; lineNumber?: number; stackTrace?: Stapel }
        anhaengen(tab, {
          art: 'meldung',
          stufe: STUFE[l.level] ?? 'info',
          werte: [],
          text: l.text,
          quelle: l.url ? { url: l.url, zeile: l.lineNumber ?? 0 } : undefined,
          stapel: l.stackTrace,
          ebene: 0,
        })
      } else if (methode === 'Runtime.executionContextsCleared') {
        // Die Seite hat gewechselt.
        if (!get().beibehalten) set((s) => ({ eintraege: { ...s.eintraege, [tab]: [] } }))
        setEbene(tab, 0)
      }
    },
    navigiert: (tab, url) => {
      if (get().beibehalten) anhaengen(tab, { art: 'navigiert', stufe: 'info', werte: [], text: url, ebene: 0 })
    },
    eingabe: (tab, text) => {
      set((s) => ({ verlauf: [...s.verlauf.filter((v) => v !== text), text].slice(-VERLAUF_MAX) }))
      anhaengen(tab, { art: 'eingabe', stufe: 'log', werte: [], text, ebene: 0 })
    },
    ergebnis: (tab, wert, fehler) => anhaengen(tab, { art: 'ergebnis', stufe: fehler ? 'error' : 'log', werte: [wert], ebene: 0 }),
    leeren: (tab) => set((s) => ({ eintraege: { ...s.eintraege, [tab]: [] }, ebene: { ...s.ebene, [tab]: 0 } })),
    setBeibehalten: (an) => set({ beibehalten: an }),
    tabWeg: (tab) =>
      set((s) => {
        const eintraege = { ...s.eintraege }
        const ebenen = { ...s.ebene }
        delete eintraege[tab]
        delete ebenen[tab]
        return { eintraege, ebene: ebenen }
      }),
  }
})
