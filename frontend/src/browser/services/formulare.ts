/**
 * Anmeldefelder der Tabs (`seite.js` → `formular.rs`): was die Leiste über der
 * Seite anbietet. Einfügen, wenn ein Feld den Fokus hat; Speichern, nachdem
 * eine Anmeldung abgeschickt wurde. Nichts davon geschieht ohne Klick.
 *
 * Die Adresse kommt immer aus dem Tab, nie aus der Seite. Gefüllt wird nur,
 * wenn der Tab noch auf derselben Herkunft steht (`tab_fuellen`).
 */
import { create } from 'zustand'

import { hostVon } from '@/desktop/vault/hostVon'
import type { VaultItem } from '@/desktop/vault/vaultEintrag'

import type { TabEreignis } from './nativ'

type FormularEreignis = Extract<TabEreignis, { art: 'formular' }>

/** Ein Benutzer-, Passwort- oder Zahlungsfeld hat den Fokus. */
export interface Feld {
  url: string
  /** Neues Passwort (Registrierung, Wechsel): hier gibt es den Vorschlag. */
  neu: boolean
  /** Ein Feld für Karte oder Konto: angeboten werden Zahlungsmittel, nie Anmeldungen. */
  zahlung?: 'karte' | 'konto'
}

/** Abgeschickt; wartet auf „Speichern“ oder „Nicht jetzt“. */
export interface Abgeschickt {
  url: string
  benutzer: string
  passwort: string
}

/** So lange gilt der Benutzer aus dem ersten Schritt einer mehrstufigen Anmeldung. */
const SCHRITT_MS = 10 * 60 * 1000

interface FormulareZustand {
  feld: Record<string, Feld>
  abgeschickt: Record<string, Abgeschickt>
  /** Erster Schritt einer mehrstufigen Anmeldung: nur der Benutzer. */
  schritt: Record<string, { host: string; wert: string; seit: number }>
  ereignis: (e: FormularEreignis) => void
  /** Neue Seite im Tab: das Angebot zum Einfügen fällt, die Frage zum Speichern bleibt. */
  laedt: (tab: string) => void
  feldWeg: (tab: string) => void
  abgeschicktWeg: (tab: string) => void
  tabWeg: (tab: string) => void
}

function ohne<T>(liste: Record<string, T>, tab: string): Record<string, T> {
  if (!(tab in liste)) return liste
  const rest = { ...liste }
  delete rest[tab]
  return rest
}

export const useFormulare = create<FormulareZustand>()((set, get) => ({
  feld: {},
  abgeschickt: {},
  schritt: {},
  ereignis: ({ id, url, meldung }) => {
    if (meldung.t === 'feld') {
      set((s) => ({ feld: { ...s.feld, [id]: { url, neu: meldung.neu } } }))
    } else if (meldung.t === 'zahlung') {
      set((s) => ({ feld: { ...s.feld, [id]: { url, neu: false, zahlung: meldung.art } } }))
    } else if (meldung.t === 'benutzer') {
      set((s) => ({ schritt: { ...s.schritt, [id]: { host: hostVon(url), wert: meldung.wert, seit: Date.now() } } }))
    } else {
      const schritt = get().schritt[id]
      const frueher = schritt && schritt.host === hostVon(url) && Date.now() - schritt.seit < SCHRITT_MS ? schritt.wert : ''
      set((s) => ({
        abgeschickt: { ...s.abgeschickt, [id]: { url, benutzer: meldung.benutzer || frueher, passwort: meldung.passwort } },
        feld: ohne(s.feld, id),
        schritt: ohne(s.schritt, id),
      }))
    }
  },
  laedt: (tab) => set((s) => ({ feld: ohne(s.feld, tab) })),
  feldWeg: (tab) => set((s) => ({ feld: ohne(s.feld, tab) })),
  abgeschicktWeg: (tab) => set((s) => ({ abgeschickt: ohne(s.abgeschickt, tab) })),
  tabWeg: (tab) => set((s) => ({ feld: ohne(s.feld, tab), abgeschickt: ohne(s.abgeschickt, tab), schritt: ohne(s.schritt, tab) })),
}))

/** Anmeldungen im Tresor für die Seite: gleicher Host, ohne `www.`. */
export function anmeldungenFuer(items: VaultItem[], url: string): VaultItem[] {
  const host = hostVon(url)
  if (!host) return []
  return items
    .filter((i) => (i.category ?? 'login') === 'login' && !i.trashedAt && !i.archivedAt && hostVon(i.url) === host && i.password)
    .sort((a, b) => (b.lastUsedAt ?? 0) - (a.lastUsedAt ?? 0) || a.username.localeCompare(b.username))
}

/** Was nach dem Absenden zu fragen ist. */
export function speicherFrage(items: VaultItem[], a: Abgeschickt): { art: 'neu' } | { art: 'aendern'; eintrag: VaultItem } | null {
  const gleicher = anmeldungenFuer(items, a.url).find((i) => i.username === a.benutzer)
  if (!gleicher) return { art: 'neu' }
  return gleicher.password === a.passwort ? null : { art: 'aendern', eintrag: gleicher }
}
