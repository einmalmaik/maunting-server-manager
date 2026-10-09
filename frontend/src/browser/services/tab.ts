/**
 * Ein Tab, wie ihn die Oberfläche führt (`tabsStore`). Ein Tab ohne Adresse
 * ist die Startseite; eine Webview hat er erst, wenn er eine Seite öffnet.
 */
import { istIntern } from './intern'

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
  /** Suche in der Seite; `aktuell` zählt ab 1, 0 heißt kein Treffer. */
  treffer: { aktuell: number; anzahl: number } | null
  /** Rust hat für diesen Tab eine Webview. */
  nativDa: boolean
  /** Die Seite spielt Ton. */
  ton: boolean
  stumm: boolean
  /** Eingefroren (`schlaf`) oder ohne Webview, um Speicher zu sparen (`verworfen`). */
  ruhe: 'schlaf' | 'verworfen' | null
}

/**
 * Ob die Webview des Tabs zu sehen ist. Startseite, eigene Seiten, Absturz
 * und Fehlerseite zeichnet die Oberfläche; die Webview käme sonst darüber.
 */
export function webviewZeigen(tab: Tab): boolean {
  return tab.nativDa && !!tab.url && !istIntern(tab.url) && !tab.abgestuerzt && !tab.fehler
}

export function neueTabId(): string {
  return `tab-${crypto.randomUUID()}`
}

export function leererTab(id: string, privat = false): Tab {
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
