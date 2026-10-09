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
  /** Das Zahlungsfeld liegt in einem Rahmen dieser Herkunft; nur dorthin wird gefüllt. */
  rahmen?: string
  /** Das Feld mit dem Fokus ist ein Passwortfeld. */
  passwort?: boolean
  /** Eindeutig ein neues Passwort: hier erzeugt der Browser eines von selbst. */
  sicher?: boolean
}

/**
 * Ein vom Browser erzeugtes Passwort für eine Seite in diesem Tab. Es liegt nur
 * im Speicher der Oberfläche, bis es abgeschickt und gespeichert ist; `null`
 * heißt, der Nutzer wollte ein eigenes.
 */
export interface Erzeugt {
  host: string
  passwort: string | null
  /** Auf dieser Seite schon eingesetzt; eine neue Seite setzt es neu ein (Fehlversuch, zweiter Schritt). */
  eingesetzt: boolean
  /**
   * Einmal ohne Rückfrage zum Speichern abgeschickt. Danach fragt der Browser
   * wieder: die Seite kennt das Passwort und könnte es sonst mit immer neuen
   * Benutzernamen abschicken, und jedes Mal entstünde ein Eintrag.
   */
  verbraucht?: boolean
  seit: number
}

/** Abgeschickt; wartet auf „Speichern“ oder „Nicht jetzt“. */
export interface Abgeschickt {
  url: string
  benutzer: string
  passwort: string
  /** Genau das Passwort, das der Browser hier erzeugt hat: es wird ohne Rückfrage gespeichert. */
  erzeugt?: boolean
}

/** So lange gilt der Benutzer aus dem ersten Schritt einer mehrstufigen Anmeldung. */
const SCHRITT_MS = 10 * 60 * 1000

interface FormulareZustand {
  feld: Record<string, Feld>
  abgeschickt: Record<string, Abgeschickt>
  /** Erster Schritt einer mehrstufigen Anmeldung: nur der Benutzer. */
  schritt: Record<string, { host: string; wert: string; seit: number }>
  erzeugt: Record<string, Erzeugt>
  ereignis: (e: FormularEreignis) => void
  /** Merkt sich ein erzeugtes Passwort (oder `null`: ein eigenes) für die Seite im Tab. */
  erzeugtMerken: (tab: string, url: string, passwort: string | null) => void
  /** Ein erzeugtes Passwort, das noch gilt, für diese Seite. */
  erzeugtFuer: (tab: string, url: string) => Erzeugt | null
  /** Nimmt die abgeschickte Anmeldung heraus; wer sie bekommt, kümmert sich allein darum. */
  abgeschicktNehmen: (tab: string) => Abgeschickt | null
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
  erzeugt: {},
  ereignis: ({ id, url, meldung, rahmen }) => {
    if (meldung.t === 'feld') {
      set((s) => ({ feld: { ...s.feld, [id]: { url, neu: meldung.neu, passwort: meldung.passwort, sicher: !!meldung.sicher } } }))
    } else if (meldung.t === 'zahlung') {
      set((s) => ({ feld: { ...s.feld, [id]: { url, neu: false, zahlung: meldung.art, ...(rahmen && { rahmen }) } } }))
    } else if (meldung.t === 'benutzer') {
      set((s) => ({ schritt: { ...s.schritt, [id]: { host: hostVon(url), wert: meldung.wert, seit: Date.now() } } }))
    } else {
      const schritt = get().schritt[id]
      const frueher = schritt && schritt.host === hostVon(url) && Date.now() - schritt.seit < SCHRITT_MS ? schritt.wert : ''
      const e = get().erzeugtFuer(id, url)
      const erzeugt = !!e && !e.verbraucht && e.passwort === meldung.passwort
      set((s) => ({
        abgeschickt: { ...s.abgeschickt, [id]: { url, benutzer: meldung.benutzer || frueher, passwort: meldung.passwort, ...(erzeugt && { erzeugt }) } },
        feld: ohne(s.feld, id),
        schritt: ohne(s.schritt, id),
        ...(erzeugt && e && { erzeugt: { ...s.erzeugt, [id]: { ...e, verbraucht: true } } }),
      }))
    }
  },
  erzeugtMerken: (tab, url, passwort) =>
    set((s) => ({ erzeugt: { ...s.erzeugt, [tab]: { host: hostVon(url), passwort, eingesetzt: true, seit: Date.now() } } })),
  erzeugtFuer: (tab, url) => {
    const e = get().erzeugt[tab]
    return e && e.host === hostVon(url) && Date.now() - e.seit < SCHRITT_MS ? e : null
  },
  abgeschicktNehmen: (tab) => {
    const a = get().abgeschickt[tab] ?? null
    if (a) set((s) => ({ abgeschickt: ohne(s.abgeschickt, tab) }))
    return a
  },
  laedt: (tab) =>
    set((s) => {
      const e = s.erzeugt[tab]
      return { feld: ohne(s.feld, tab), erzeugt: e ? { ...s.erzeugt, [tab]: { ...e, eingesetzt: false } } : s.erzeugt }
    }),
  feldWeg: (tab) => set((s) => ({ feld: ohne(s.feld, tab) })),
  abgeschicktWeg: (tab) => set((s) => ({ abgeschickt: ohne(s.abgeschickt, tab) })),
  tabWeg: (tab) =>
    set((s) => ({ feld: ohne(s.feld, tab), abgeschickt: ohne(s.abgeschickt, tab), schritt: ohne(s.schritt, tab), erzeugt: ohne(s.erzeugt, tab) })),
}))

/** Anmeldungen im Tresor für die Seite: gleicher Host, ohne `www.`, und auf http nur http-Einträge. */
export function anmeldungenFuer(items: VaultItem[], url: string): VaultItem[] {
  const host = hostVon(url)
  if (!host) return []
  // Eine Seite ohne HTTPS bekommt nur, was ausdrücklich für http gespeichert
  // wurde; sonst liefert jemand im Netz die http-Seite aus und liest mit.
  const unsicher = /^http:/i.test(url)
  return items
    .filter(
      (i) =>
        (i.category ?? 'login') === 'login' &&
        !i.trashedAt &&
        !i.archivedAt &&
        hostVon(i.url) === host &&
        (!unsicher || /^http:/i.test(i.url ?? '')) &&
        i.password,
    )
    .sort((a, b) => (b.lastUsedAt ?? 0) - (a.lastUsedAt ?? 0) || a.username.localeCompare(b.username))
}

/** Was nach dem Absenden zu fragen ist. */
export function speicherFrage(items: VaultItem[], a: Abgeschickt): { art: 'neu' } | { art: 'aendern'; eintrag: VaultItem } | null {
  const gleicher = anmeldungenFuer(items, a.url).find((i) => i.username === a.benutzer)
  if (!gleicher) return { art: 'neu' }
  return gleicher.password === a.passwort ? null : { art: 'aendern', eintrag: gleicher }
}
