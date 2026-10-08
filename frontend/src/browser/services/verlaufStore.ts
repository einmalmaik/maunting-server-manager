/**
 * Verlauf und Lesezeichen. Beides liegt nur auf diesem Gerät, nie beim Panel.
 * Private Tabs schreiben nichts hinein (`tabsStore.ts`).
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface Eintrag {
  url: string
  titel: string
  zeit: number
}

/** Mehr hält der Verlauf nicht; das Älteste fällt zuerst. */
export const VERLAUF_MAX = 5000

interface VerlaufZustand {
  verlauf: Eintrag[]
  lesezeichen: Eintrag[]

  besucht: (url: string, titel: string) => void
  titelNachtragen: (url: string, titel: string) => void
  verlaufEntfernen: (url: string, zeit: number) => void
  verlaufLeeren: (seit?: number) => void
  lesezeichenUmschalten: (url: string, titel: string) => void
  lesezeichenEntfernen: (url: string) => void
}

/** Zwei Besuche derselben Adresse binnen einer Minute sind einer (Neuladen, Weiterleitung). */
const ZUSAMMENFASSEN_MS = 60_000

export const useVerlaufStore = create<VerlaufZustand>()(
  persist(
    (set) => ({
      verlauf: [],
      lesezeichen: [],

      besucht: (url, titel) =>
        set((s) => {
          const jetzt = Date.now()
          const [letzter] = s.verlauf
          if (letzter && letzter.url === url && jetzt - letzter.zeit < ZUSAMMENFASSEN_MS) {
            return { verlauf: [{ ...letzter, titel: titel || letzter.titel, zeit: jetzt }, ...s.verlauf.slice(1)] }
          }
          return { verlauf: [{ url, titel, zeit: jetzt }, ...s.verlauf].slice(0, VERLAUF_MAX) }
        }),

      titelNachtragen: (url, titel) =>
        set((s) => {
          const index = s.verlauf.findIndex((e) => e.url === url)
          if (index !== 0) return s
          const verlauf = [...s.verlauf]
          verlauf[0] = { ...verlauf[0], titel }
          return { verlauf }
        }),

      verlaufEntfernen: (url, zeit) =>
        set((s) => ({ verlauf: s.verlauf.filter((e) => !(e.url === url && e.zeit === zeit)) })),

      verlaufLeeren: (seit) =>
        set((s) => ({ verlauf: seit === undefined ? [] : s.verlauf.filter((e) => e.zeit < seit) })),

      lesezeichenUmschalten: (url, titel) =>
        set((s) =>
          s.lesezeichen.some((e) => e.url === url)
            ? { lesezeichen: s.lesezeichen.filter((e) => e.url !== url) }
            : { lesezeichen: [{ url, titel: titel || url, zeit: Date.now() }, ...s.lesezeichen] },
        ),

      lesezeichenEntfernen: (url) => set((s) => ({ lesezeichen: s.lesezeichen.filter((e) => e.url !== url) })),
    }),
    { name: 'msb:verlauf', version: 1 },
  ),
)

/**
 * Vorschläge für die Adresszeile: Lesezeichen zuerst, dann der Verlauf, jede
 * Adresse einmal. Gesucht wird in Adresse und Titel.
 */
export function vorschlaege(text: string, verlauf: Eintrag[], lesezeichen: Eintrag[], max = 6): Eintrag[] {
  const suche = text.trim().toLowerCase()
  if (!suche) return []
  const passt = (e: Eintrag) =>
    e.url.toLowerCase().replace(/^https?:\/\/(www\.)?/, '').includes(suche) || e.titel.toLowerCase().includes(suche)
  const gesehen = new Set<string>()
  const ergebnis: Eintrag[] = []
  for (const e of [...lesezeichen, ...verlauf]) {
    if (ergebnis.length >= max) break
    if (gesehen.has(e.url) || !passt(e)) continue
    gesehen.add(e.url)
    ergebnis.push(e)
  }
  return ergebnis
}
