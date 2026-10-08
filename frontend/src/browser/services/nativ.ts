/**
 * Die Brücke zur nativen Seite des Browsers (`browser/src-tauri`).
 *
 * Ein Befehl je Funktion, ein Ereignis für alles, was aus den Tabs kommt
 * (`msb:tab`). Außerhalb von Tauri (Tests, Vite im normalen Browser) tun die
 * Befehle nichts: die Oberfläche bleibt bedienbar, nur ohne Seiteninhalt.
 */
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'

export function istTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

async function rufen<T = void>(befehl: string, args?: Record<string, unknown>): Promise<T | null> {
  if (!istTauri()) return null
  try {
    return await invoke<T>(befehl, args)
  } catch (fehler) {
    console.warn(`[MSB] ${befehl} fehlgeschlagen:`, fehler)
    throw fehler
  }
}

/**
 * Befehle, die festlegen, welcher Tab wo zu sehen ist, laufen nacheinander.
 * Tauri führt `async`-Befehle nebeneinander aus; „verdecken“ und gleich danach
 * „zeigen“ kamen so in umgekehrter Reihenfolge an, und die Seite blieb weg,
 * bis man den Tab anklickte (bis 10/2026).
 */
let kette: Promise<unknown> = Promise.resolve()

function nacheinander<T = void>(befehl: string, args?: Record<string, unknown>): Promise<T | null> {
  const lauf = kette.then(() => rufen<T>(befehl, args))
  kette = lauf.catch(() => undefined)
  return lauf
}

export type DownloadStand = 'start' | 'fertig' | 'fehler'

/** Was Rust aus einem Tab meldet (`TabEreignis` in `tabs/mod.rs`). */
export type TabEreignis =
  | { art: 'laedt'; id: string; url: string }
  | { art: 'geladen'; id: string; url: string }
  | { art: 'adresse'; id: string; url: string; zurueck: boolean; vor: boolean }
  | { art: 'titel'; id: string; titel: string }
  | { art: 'favicon'; id: string; url: string | null }
  | { art: 'neuer_tab'; id: string; url: string }
  | { art: 'taste'; id: string; taste: string }
  | { art: 'download'; id: string; stand: DownloadStand; url: string; datei: string | null }
  | { art: 'schild'; id: string; werbung: number; tracker: number }
  | { art: 'absturz'; id: string }
  | { art: 'vollbild'; id: string; an: boolean }
  | { art: 'formular'; id: string; url: string; meldung: FormularMeldung }
  | {
      art: 'kontextmenue'
      id: string
      nr: number
      x: number
      y: number
      eintraege: MenueEintrag[]
      link: string | null
      bild: string | null
      auswahl: string | null
      bearbeitbar: boolean
    }
  | { art: 'dialog'; id: string; nr: number; dialog: 'alert' | 'confirm' | 'prompt' | 'beforeunload'; herkunft: string; text: string; vorgabe: string }
  | { art: 'recht'; id: string; nr: number; recht: string; herkunft: string }
  | { art: 'anmeldung'; id: string; nr: number; herkunft: string; bereich: string }
  | { art: 'fehlerseite'; id: string; url: string; grund: string }
  | { art: 'status'; id: string; text: string }
  | { art: 'treffer'; id: string; aktuell: number; anzahl: number }
  /** Ein Ereignis des DevTools-Protokolls für die Entwicklerwerkzeuge (`desktop/entwickler.rs`). */
  | { art: 'protokoll'; id: string; methode: string; daten: Record<string, unknown> }

/** Was `seite.js` meldet (`Meldung` in `tabs/formular.rs`). */
export type FormularMeldung =
  | { t: 'feld'; passwort: boolean; neu: boolean; sicher?: boolean }
  | { t: 'absenden'; benutzer: string; passwort: string; neu: boolean }
  | { t: 'benutzer'; wert: string }
  /** Ein Feld für eine Zahlungskarte oder ein Bankkonto hat den Fokus. */
  | { t: 'zahlung'; art: 'karte' | 'konto' }

/** Was in die Seite kommt; `neu` füllt Passwort und Wiederholung. Zahlungsdaten nur nach Bestätigung. */
export interface Fuellen {
  benutzer: string | null
  passwort: string | null
  neu: string | null
  karte?: { nummer: string; inhaber: string | null; monat: number | null; jahr: number | null; pruefnummer: string | null }
  konto?: { iban: string; inhaber: string | null; bic: string | null }
}

/** Ein Befehl des Kontextmenüs der WebView2 (`copy`, `paste`, `saveImageAs`, …). */
export interface MenueEintrag {
  befehl: number
  name: string
}

/** Antwort auf eine Rückfrage der Seite (`Antwort` in `tabs/mod.rs`). */
export type Antwort =
  | { art: 'menue'; befehl: number | null }
  | { art: 'dialog'; ok: boolean; text: string | null }
  | { art: 'recht'; erlauben: boolean }
  | { art: 'anmeldung'; benutzer: string | null; passwort: string | null }

export async function tabEreignisse(rueckruf: (e: TabEreignis) => void): Promise<() => void> {
  if (!istTauri()) return () => {}
  return listen<TabEreignis>('msb:tab', (e) => rueckruf(e.payload))
}

export const nativ = {
  tabLaden: (id: string, url: string, privat: boolean) => nacheinander('tab_laden', { id, url, privat }),
  tabAktivieren: (id: string | null) => nacheinander('tab_aktivieren', { id }),
  tabSchliessen: (id: string) => nacheinander('tab_schliessen', { id }),
  tabAktion: (id: string, aktion: 'zurueck' | 'vor' | 'neu_laden' | 'anhalten') =>
    rufen('tab_aktion', { id, aktion }),
  tabsRahmen: (rahmen: { x: number; y: number; breite: number; hoehe: number }) =>
    nacheinander('tabs_rahmen', { rahmen }),
  tabsVerdecken: (verdeckt: boolean) => nacheinander('tabs_verdecken', { verdeckt }),
  /** JPEG des vorderen Tabs. */
  tabStandbild: () => nacheinander<ArrayBuffer>('tab_standbild'),
  tabsZuruecksetzen: () => nacheinander('tabs_zuruecksetzen'),
  tabAntworten: (nr: number, antwort: Antwort) => rufen('tab_antworten', { nr, antwort }),
  /** Eine Methode des DevTools-Protokolls; was erlaubt ist, entscheidet Rust. */
  tabProtokoll: (id: string, methode: string, parameter: Record<string, unknown>) =>
    rufen<Record<string, unknown>>('tab_protokoll', { id, methode, parameter }),
  tabSuchen: (id: string, richtung: 'start' | 'weiter' | 'zurueck' | 'ende', begriff = '') =>
    rufen('tab_suchen', { id, richtung, begriff }),
  tabDrucken: (id: string) => rufen('tab_drucken', { id }),
  /** Nur, wenn der Tab noch auf der Herkunft von `fuer` steht; sonst lehnt Rust ab. */
  tabFuellen: (id: string, fuer: string, werte: Fuellen) => rufen('tab_fuellen', { id, fuer, werte }),
  oberflaecheFokussieren: () => rufen('oberflaeche_fokussieren'),
  fensterAktion: (aktion: 'minimieren' | 'maximieren' | 'schliessen') => rufen('fenster_aktion', { aktion }),
  /** Namensblase über der Seite (`kurzinfo.rs`); `null` verbirgt sie. */
  kurzinfo: (blase: Record<string, unknown> | null) => rufen('kurzinfo', { blase }),
  downloadZeigen: (pfad: string) => rufen('download_zeigen', { pfad }),
  downloadOrdner: () => rufen<string>('download_ordner'),
  seitendatenLoeschen: () => rufen('seitendaten_loeschen'),
  schildStand: () =>
    rufen<{ aktiv: boolean; listen: { name: string; alter_sekunden: number | null }[] }>('schild_stand'),
}

/** Die Felder der Gerätekonfiguration (`konfig.rs`). */
export interface BrowserKonfig {
  backend_url: string | null
  eingerichtet: boolean
  schild_aktiv: boolean
  schild_ausnahmen: string[]
  download_ordner: string | null
  vergessen_beim_schliessen: boolean
}

export const konfig = {
  laden: () => rufen<BrowserKonfig>('konfig_laden'),
  aendern: (felder: Partial<BrowserKonfig>) => rufen<BrowserKonfig>('konfig_aendern', { felder }),
}
