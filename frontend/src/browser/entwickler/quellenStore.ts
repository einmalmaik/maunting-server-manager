/**
 * Quellen und Debugger eines Tabs: geladene Skripte, Haltepunkte und, solange
 * die Seite angehalten ist, Aufrufstapel und Bereiche.
 */
import { create } from 'zustand'

import type { Objekt, Ort } from './protokoll'

export interface Skript {
  scriptId: string
  url: string
}

export interface Haltepunkt {
  id: string
  url: string
  zeile: number
  bedingung: string
}

export interface Bereich {
  type: string
  name?: string
  object: Objekt
}

export interface Rahmen {
  callFrameId: string
  functionName: string
  location: Ort
  url: string
  scopeChain: Bereich[]
  this: Objekt
}

export interface Halt {
  grund: string
  rahmen: Rahmen[]
  /** Gewählter Rahmen, darin wertet auch die Konsole aus. */
  gewaehlt: number
}

interface QuellenZustand {
  skripte: Record<string, Record<string, Skript>>
  haltepunkte: Record<string, Haltepunkt[]>
  halt: Record<string, Halt | null>
  ereignis: (tab: string, methode: string, daten: Record<string, unknown>) => void
  haltepunkt: (tab: string, h: Haltepunkt) => void
  haltepunktWeg: (tab: string, id: string) => void
  rahmenWaehlen: (tab: string, i: number) => void
  zuruecksetzen: (tab: string) => void
  tabWeg: (tab: string) => void
}

export const useQuellen = create<QuellenZustand>()((set) => ({
  skripte: {},
  haltepunkte: {},
  halt: {},
  ereignis: (tab, methode, d) => {
    if (methode === 'Debugger.scriptParsed') {
      const url = String(d.url ?? '')
      // Ohne Adresse (eval, Konsole) gibt es nichts zum Ansehen in der Liste.
      if (!url) return
      const s: Skript = { scriptId: String(d.scriptId), url }
      set((z) => ({ skripte: { ...z.skripte, [tab]: { ...z.skripte[tab], [s.scriptId]: s } } }))
    } else if (methode === 'Debugger.paused') {
      // `CallFrame.url` ist im Protokoll veraltet und oft leer; die Adresse kennt das Skript.
      set((z) => {
        const skripte = z.skripte[tab] ?? {}
        const rahmen = (d.callFrames as Rahmen[]).map((r) => (r.url ? r : { ...r, url: skripte[r.location.scriptId]?.url ?? '' }))
        const grund = (d.hitBreakpoints as string[] | undefined)?.length ? 'breakpoint' : String(d.reason)
        return { halt: { ...z.halt, [tab]: { grund, rahmen, gewaehlt: 0 } } }
      })
    } else if (methode === 'Debugger.resumed') {
      set((z) => ({ halt: { ...z.halt, [tab]: null } }))
    } else if (methode === 'Runtime.executionContextsCleared') {
      // Neue Seite: alte Skripte gelten nicht mehr, Haltepunkte nach Adresse schon.
      set((z) => ({ skripte: { ...z.skripte, [tab]: {} }, halt: { ...z.halt, [tab]: null } }))
    }
  },
  haltepunkt: (tab, h) => set((z) => ({ haltepunkte: { ...z.haltepunkte, [tab]: [...(z.haltepunkte[tab] ?? []).filter((x) => x.id !== h.id), h] } })),
  haltepunktWeg: (tab, id) => set((z) => ({ haltepunkte: { ...z.haltepunkte, [tab]: (z.haltepunkte[tab] ?? []).filter((x) => x.id !== id) } })),
  rahmenWaehlen: (tab, i) =>
    set((z) => {
      const halt = z.halt[tab]
      return halt ? { halt: { ...z.halt, [tab]: { ...halt, gewaehlt: i } } } : z
    }),
  zuruecksetzen: (tab) =>
    set((z) => ({ skripte: { ...z.skripte, [tab]: {} }, haltepunkte: { ...z.haltepunkte, [tab]: [] }, halt: { ...z.halt, [tab]: null } })),
  tabWeg: (tab) =>
    set((z) => {
      const skripte = { ...z.skripte }
      const haltepunkte = { ...z.haltepunkte }
      const halt = { ...z.halt }
      delete skripte[tab]
      delete haltepunkte[tab]
      delete halt[tab]
      return { skripte, haltepunkte, halt }
    }),
}))
