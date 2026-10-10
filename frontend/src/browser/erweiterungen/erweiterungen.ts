/**
 * Erweiterungen (nur Windows). Was installiert ist und läuft, entscheidet
 * Rust (`src-tauri/src/erweiterungen/`); hier wird angezeigt und gewünscht.
 * Rust meldet `msb:erweiterungen`, wenn sich etwas ändert (auch durch den
 * Jugendschutz), und `msb:erweiterung-popup`, wenn das Popup zugeht oder
 * einen Tab öffnen will.
 */
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import type { TFunction } from 'i18next'
import { create } from 'zustand'

import { istTauri } from '../services/nativ'
import { istAndroid } from '../services/plattform'
import { useTabsStore } from '../services/tabsStore'

export interface Angaben {
  name: string
  version: string
  beschreibung: string
  rechte: string[]
  seiten: string[]
  optional: string[]
  /** Data-URL. */
  symbol: string | null
  popup: string | null
  optionen: string | null
}

export type Herkunft = 'store' | 'entpackt'

export interface Erweiterung {
  id: string
  herkunft: Herkunft
  an: boolean
  /** An, und weder Jugendschutz noch ausgeschalteter Entwicklermodus halten sie an. */
  laeuft: boolean
  angeheftet: boolean
  angaben: Angaben
}

export interface ErweiterungenStand {
  entwicklermodus: boolean
  jugendschutz: boolean
  eintraege: Erweiterung[]
}

/** Ein geprüftes Paket, das auf die Rückfrage wartet. */
export interface Vorschau {
  vorgang: string
  id: string | null
  herkunft: Herkunft
  angaben: Angaben
}

/** Erweiterungen gibt es nur in der Windows-App. */
export const erweiterungenMoeglich = () => istTauri() && !istAndroid()

interface Zustand {
  stand: ErweiterungenStand | null
  /** Kennung der Erweiterung, deren Popup offen ist. */
  popup: string | null
  /** Welches Popup zuletzt zuging und wann: ein Klick auf denselben Knopf schließt es nur. */
  zuletztZu: { id: string; zeit: number } | null
  laden: () => Promise<void>
  setzen: (stand: ErweiterungenStand) => void
}

export const useErweiterungen = create<Zustand>((set) => ({
  stand: null,
  popup: null,
  zuletztZu: null,
  laden: async () => {
    if (!erweiterungenMoeglich()) return
    const stand = await invoke<ErweiterungenStand>('erweiterungen_liste').catch(() => null)
    if (stand) set({ stand })
  },
  setzen: (stand) => set({ stand }),
}))

/** Ein Befehl, der den neuen Stand zurückgibt. */
async function aendern(befehl: string, args: Record<string, unknown>) {
  useErweiterungen.getState().setzen(await invoke<ErweiterungenStand>(befehl, args))
}

export const erweiterungen = {
  pruefen: (quelle: string, entpackt: boolean, sprache: string) => invoke<Vorschau>('erweiterung_pruefen', { quelle, entpackt, sprache }),
  installieren: (vorgang: string) => aendern('erweiterung_installieren', { vorgang }),
  verwerfen: (vorgang: string) => invoke('erweiterung_verwerfen', { vorgang }).catch(() => undefined),
  schalten: (id: string, an: boolean) => aendern('erweiterung_schalten', { id, an }),
  anheften: (id: string, an: boolean) => aendern('erweiterung_anheften', { id, an }),
  entfernen: (id: string) => aendern('erweiterung_entfernen', { id }),
  entwicklermodus: (an: boolean) => aendern('erweiterungen_entwicklermodus', { an }),
  /**
   * Öffnet das Popup unter dem Knopf `anker`. Der Klick auf den Knopf nimmt
   * dem offenen Popup den Fokus, und es geht zu, bevor der Klick ankommt;
   * dann bleibt es zu.
   */
  popup: async (id: string, anker: HTMLElement) => {
    const { popup, zuletztZu } = useErweiterungen.getState()
    if (popup === id || (zuletztZu?.id === id && Date.now() - zuletztZu.zeit < 400)) return
    const r = anker.getBoundingClientRect()
    useErweiterungen.setState({ popup: id })
    await invoke('erweiterung_popup', { id, rechts: r.right, oben: r.bottom + 4 }).catch((e) => {
      useErweiterungen.setState({ popup: null })
      throw e
    })
  },
  popupSchliessen: () => invoke('erweiterung_popup_schliessen').catch(() => undefined),
  optionen: async (id: string) => {
    const url = await invoke<string>('erweiterung_optionen', { id })
    useTabsStore.getState().neuerTab(url)
  },
}

/** Hört auf Rust; einmal beim Start der Oberfläche. */
export async function erweiterungenStarten(): Promise<() => void> {
  if (!erweiterungenMoeglich()) return () => {}
  void useErweiterungen.getState().laden()
  const aus = await Promise.all([
    listen('msb:erweiterungen', () => void useErweiterungen.getState().laden()),
    listen<{ zu?: boolean; tab?: string }>('msb:erweiterung-popup', ({ payload }) => {
      if (payload.zu) useErweiterungen.setState((s) => ({ popup: null, zuletztZu: s.popup ? { id: s.popup, zeit: Date.now() } : s.zuletztZu }))
      if (payload.tab) useTabsStore.getState().neuerTab(payload.tab)
    }),
  ])
  return () => aus.forEach((f) => f())
}

/** Fehlercodes aus Rust (`fehler.*`); Unbekanntes heißt allgemein „ging nicht“. */
export function fehlerText(t: TFunction, fehler: unknown): string {
  const code = String(fehler)
  return t(`browser.erweiterungen.fehler.${code}`, { defaultValue: t('browser.erweiterungen.fehler.allgemein') })
}

/** Rechte, die wir beim Namen nennen; andere stehen so da, wie das Manifest sie nennt. */
export const BEKANNTE_RECHTE = [
  'activeTab',
  'alarms',
  'bookmarks',
  'clipboardRead',
  'clipboardWrite',
  'contextMenus',
  'cookies',
  'debugger',
  'declarativeNetRequest',
  'declarativeNetRequestFeedback',
  'declarativeNetRequestWithHostAccess',
  'downloads',
  'history',
  'identity',
  'management',
  'nativeMessaging',
  'notifications',
  'offscreen',
  'privacy',
  'proxy',
  'scripting',
  'sidePanel',
  'storage',
  'tabs',
  'unlimitedStorage',
  'webNavigation',
  'webRequest',
] as const

/** Muster, die jede Seite treffen. */
export function alleSeiten(muster: string): boolean {
  return ['<all_urls>', '*://*/*', 'http://*/*', 'https://*/*'].includes(muster)
}
