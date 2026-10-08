/**
 * Was eine Seite fragt: Rechtsklick, `alert`/`confirm`/`prompt`, Rechte und
 * HTTP-Anmeldung. Rust hält die Anfrage, bis hier geantwortet wird
 * (`rueckfragen.rs`); gezeigt wird nur, was der vordere Tab fragt, der Rest
 * wartet, bis man zu ihm wechselt.
 */
import { create } from 'zustand'

import { nativ, type Antwort, type TabEreignis } from './nativ'

export type Rueckfrage = Extract<TabEreignis, { art: 'kontextmenue' | 'dialog' | 'recht' | 'anmeldung' }>

interface RueckfragenZustand {
  offen: Rueckfrage[]
  aufnehmen: (frage: Rueckfrage) => void
  antworten: (nr: number, antwort: Antwort) => void
  /** Ein Tab ist zu; Rust lehnt seine offenen Fragen selbst ab. */
  tabWeg: (id: string) => void
}

export const useRueckfragen = create<RueckfragenZustand>()((set, get) => ({
  offen: [],
  aufnehmen: (frage) =>
    set((s) => ({
      // Ein neuer Rechtsklick ersetzt ein noch offenes Menü desselben Tabs.
      offen: [...s.offen.filter((f) => !(frage.art === 'kontextmenue' && f.art === 'kontextmenue' && f.id === frage.id)), frage],
    })),
  antworten: (nr, antwort) => {
    if (!get().offen.some((f) => f.nr === nr)) return
    set((s) => ({ offen: s.offen.filter((f) => f.nr !== nr) }))
    void nativ.tabAntworten(nr, antwort).catch(() => null)
  },
  tabWeg: (id) => set((s) => ({ offen: s.offen.filter((f) => f.id !== id) })),
}))

/** Die erste offene Frage des Tabs, falls es eine gibt. */
export function useFrageDesTabs(id: string | undefined): Rueckfrage | undefined {
  return useRueckfragen((s) => s.offen.find((f) => f.id === id))
}

/** Der Host einer Herkunft, so wie Menschen ihn lesen. */
export function herkunftsName(url: string): string {
  try {
    return new URL(url).host || url
  } catch {
    return url
  }
}
