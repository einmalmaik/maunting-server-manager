/**
 * Eine Seite übersetzen: Sprache der Seite erfragen, Modelle laden (nur nach
 * Zustimmung), dann Stück für Stück Text holen, übersetzen und zurückgeben,
 * bis die Seite nichts mehr hat (`seite.js`, `tabs/uebersetzung.rs`).
 *
 * Nach jedem `await` prüft der Lauf, ob er noch gilt (AGENTS.md Punkt 45):
 * ein Seitenwechsel, „Original“ oder Schließen beendet ihn, und ein neuer
 * Lauf löst den alten ab.
 */
import { useTranslation } from 'react-i18next'
import { create } from 'zustand'

import { normalizePanelLanguage } from '@/config/panelLocales'

import { istWebseite } from '../services/intern'
import { nativ, type TabEreignis } from '../services/nativ'
import type { Tab } from '../services/tab'

// Modellliste und Engine kommen erst beim ersten Übersetzen ins Spiel, nicht
// ins Startbündel (AGENTS.md Punkt 48).
const modelle = () => import('./modelle')
const engine = () => import('./engine')

export type Stand =
  | { art: 'erkennen' }
  /** Wartet auf die Wahl der Sprachen bzw. die Zustimmung zum Laden. */
  | { art: 'wahl'; fehlt: number | null }
  | { art: 'laden'; geladen: number; gesamt: number }
  | { art: 'uebersetzen'; texte: number }
  | { art: 'fertig' }
  | { art: 'fehler'; grund: string }

export interface Eintrag {
  url: string
  von: string
  nach: string
  stand: Stand
  lauf: number
}

type TexteEreignis = Extract<TabEreignis, { art: 'texte' }>

/** So lange wartet der Lauf auf ein Stück der Seite. */
const FRIST_MS = 15_000
export const GRUENDE = ['netz', 'pruefsumme', 'paar', 'seite', 'engine'] as const

let naechsteNr = 0
let naechsterLauf = 0
const warten = new Map<string, { nr: number; fertig: (e: TexteEreignis) => void; fehler: (f: Error) => void }>()
const abbrueche = new Map<string, AbortController>()

function aufhoeren(tab: string) {
  abbrueche.get(tab)?.abort()
  abbrueche.delete(tab)
  warten.get(tab)?.fehler(new Error('abgebrochen'))
  warten.delete(tab)
}

/** Schickt einen Schritt an die Seite und wartet auf ihr nächstes Stück. */
async function anfragen(tab: string, url: string, schritt: 'start' | 'weiter', texte?: string[]): Promise<TexteEreignis> {
  const nr = ++naechsteNr
  const antwort = new Promise<TexteEreignis>((fertig, fehler) => {
    warten.get(tab)?.fehler(new Error('abgeloest'))
    warten.set(tab, { nr, fertig, fehler })
    setTimeout(() => {
      if (warten.get(tab)?.nr !== nr) return
      warten.delete(tab)
      fehler(new Error('seite'))
    }, FRIST_MS)
  })
  await nativ.tabUebersetzen(tab, url, nr, schritt, texte)
  return antwort
}

interface Zustand {
  eintraege: Record<string, Eintrag>
  /** Öffnet die Leiste und erfragt die Sprache; sind die Modelle da, geht es gleich los. */
  oeffnen: (tab: Pick<Tab, 'id' | 'url'>, appSprache: string) => Promise<void>
  waehlen: (tab: string, teil: { von?: string; nach?: string }) => Promise<void>
  starten: (tab: string) => Promise<void>
  /** Stellt das Original her; die Leiste bleibt. */
  original: (tab: string) => void
  /** Leiste zu; ein laufender Lauf endet und nimmt zurück, was er schon übersetzt hat. */
  schliessen: (tab: string) => void
  /** Die Seite ist weg (neue Seite, Tab zu): nichts mehr an sie schicken. */
  seiteWeg: (tab: string) => void
  texte: (e: TexteEreignis) => void
}

export const useUebersetzung = create<Zustand>()((set, get) => {
  /** Ändert den Eintrag nur, wenn `lauf` noch gilt; sagt, ob er gilt. */
  const gilt = (tab: string, lauf: number, teil?: Partial<Eintrag>): boolean => {
    const e = get().eintraege[tab]
    if (!e || e.lauf !== lauf) return false
    if (teil) set((s) => ({ eintraege: { ...s.eintraege, [tab]: { ...e, ...teil } } }))
    return true
  }
  const neuerLauf = (tab: string, teil: Partial<Eintrag>): number => {
    aufhoeren(tab)
    const lauf = ++naechsterLauf
    set((s) => ({ eintraege: { ...s.eintraege, [tab]: { ...(s.eintraege[tab] as Eintrag), ...teil, lauf } } }))
    return lauf
  }
  const fehlt = async (tab: string, lauf: number) => {
    const e = get().eintraege[tab]
    const { weg, fehlendeGroesse } = await modelle()
    const paare = e && weg(e.von, e.nach)
    const groesse = paare ? await fehlendeGroesse(paare).catch(() => null) : null
    gilt(tab, lauf, { stand: { art: 'wahl', fehlt: groesse } })
    return groesse
  }

  return {
    eintraege: {},

    oeffnen: async ({ id, url }, appSprache) => {
      if (!istWebseite(url)) return
      const lauf = neuerLauf(id, { url, von: '', nach: appSprache, stand: { art: 'erkennen' } })
      let von = ''
      try {
        const { sprache } = await anfragen(id, url, 'start')
        von = (await modelle()).spracheVon(sprache)
      } catch {
        // Ohne Antwort wählt der Nutzer selbst.
      }
      const nach = von === appSprache ? (appSprache === 'en' ? 'de' : 'en') : appSprache
      if (!gilt(id, lauf, { von, nach })) return
      if ((await fehlt(id, lauf)) === 0 && gilt(id, lauf)) await get().starten(id)
    },

    waehlen: async (tab, teil) => {
      if (!get().eintraege[tab]) return
      await fehlt(tab, neuerLauf(tab, teil))
    },

    starten: async (tab) => {
      const { weg, fehlendeGroesse } = await modelle()
      const { bereitmachen, uebersetzen } = await engine()
      const e = get().eintraege[tab]
      const paare = e && weg(e.von, e.nach)
      if (!e || !paare) return
      const lauf = neuerLauf(tab, { stand: { art: 'laden', geladen: 0, gesamt: 0 } })
      const abbruch = new AbortController()
      abbrueche.set(tab, abbruch)
      try {
        const gesamt = await fehlendeGroesse(paare)
        let geladen = 0
        if (!gilt(tab, lauf, { stand: { art: 'laden', geladen, gesamt } })) return
        await bereitmachen(paare, (bytes) => {
          geladen += bytes
          gilt(tab, lauf, { stand: { art: 'laden', geladen, gesamt } })
        }, abbruch.signal)
        if (!gilt(tab, lauf, { stand: { art: 'uebersetzen', texte: 0 } })) return
        let stueck = await anfragen(tab, e.url, 'start')
        let fertig = 0
        while (stueck.texte.length) {
          const uebersetzt = await uebersetzen(paare, stueck.texte)
          fertig += uebersetzt.length
          if (!gilt(tab, lauf, { stand: { art: 'uebersetzen', texte: fertig } })) return
          stueck = await anfragen(tab, e.url, 'weiter', uebersetzt)
          if (!gilt(tab, lauf)) return
        }
        gilt(tab, lauf, { stand: { art: 'fertig' } })
      } catch (f) {
        const grund = f instanceof Error && (GRUENDE as readonly string[]).includes(f.message) ? f.message : 'engine'
        gilt(tab, lauf, { stand: { art: 'fehler', grund } })
      } finally {
        if (abbrueche.get(tab) === abbruch) abbrueche.delete(tab)
      }
    },

    original: (tab) => {
      const e = get().eintraege[tab]
      if (!e) return
      neuerLauf(tab, { stand: { art: 'wahl', fehlt: 0 } })
      void nativ.tabUebersetzen(tab, e.url, ++naechsteNr, 'original').catch(() => null)
    },

    schliessen: (tab) => {
      const e = get().eintraege[tab]
      if (!e) return
      if (e.stand.art !== 'fertig') void nativ.tabUebersetzen(tab, e.url, ++naechsteNr, 'original').catch(() => null)
      get().seiteWeg(tab)
    },

    seiteWeg: (tab) => {
      aufhoeren(tab)
      if (!get().eintraege[tab]) return
      set((s) => {
        const rest = { ...s.eintraege }
        delete rest[tab]
        return { eintraege: rest }
      })
    },

    texte: (e) => {
      const w = warten.get(e.id)
      if (w?.nr !== e.nr) return
      warten.delete(e.id)
      w.fertig(e)
    },
  }
})

/** „Übersetzen“ für diesen Tab, oder `null`, wo es nichts zu übersetzen gibt. */
export function useUebersetzen(tab: Tab | undefined): (() => void) | null {
  const { i18n } = useTranslation()
  if (!tab || !istWebseite(tab.url) || tab.fehler || tab.abgestuerzt) return null
  const sprache = normalizePanelLanguage(i18n.language)
  return () => void useUebersetzung.getState().oeffnen(tab, sprache)
}
