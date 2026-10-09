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

/** `pruefung`: geladen, der Virenschutz prüft; `blockiert`: er hat die Datei abgelehnt, sie ist gelöscht. */
export type DownloadStand = 'start' | 'pruefung' | 'fertig' | 'blockiert' | 'fehler' | 'speicher'

/** Was Rust aus einem Tab meldet (`TabEreignis` in `tabs/mod.rs`). */
export type TabEreignis =
  | { art: 'laedt'; id: string; url: string }
  | { art: 'geladen'; id: string; url: string }
  | { art: 'adresse'; id: string; url: string; zurueck: boolean; vor: boolean }
  | { art: 'titel'; id: string; titel: string }
  | { art: 'favicon'; id: string; url: string | null }
  | { art: 'neuer_tab'; id: string; url: string }
  | { art: 'taste'; id: string; taste: string }
  /** `datei`: der Name, bei `fertig` der volle Pfad. `nr` kennzeichnet den Download über alle Meldungen. */
  | { art: 'download'; id: string; nr: number; stand: DownloadStand; url: string; datei: string | null }
  | { art: 'schild'; id: string; werbung: number; tracker: number }
  | { art: 'absturz'; id: string }
  | { art: 'vollbild'; id: string; an: boolean }
  /** `rahmen`: das Zahlungsfeld liegt in einem Rahmen dieser Herkunft (Stripe, Adyen). */
  | { art: 'formular'; id: string; url: string; meldung: FormularMeldung; rahmen?: string }
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
  /** Der Jugend- und Suchtschutz sperrt die Seite; `grund` ist die Kategorie oder `eigene`. */
  | { art: 'gesperrt'; id: string; url: string; grund: string }
  | { art: 'status'; id: string; text: string }
  | { art: 'treffer'; id: string; aktuell: number; anzahl: number }
  | { art: 'ton'; id: string; spielt: boolean; stumm: boolean }
  | { art: 'schlaf'; id: string; schlaeft: boolean }
  /** Die Webview ist weg, um Speicher zu sparen; beim Zeigen lädt der Tab neu. */
  | { art: 'verworfen'; id: string }
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

/** Was eine Seite erlaubt oder verboten bekam (`browserdaten.rs`). */
export interface Seitenrecht {
  art: string
  herkunft: string
  erlaubt: boolean
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
  /** Mit `rahmen` gehen Zahlungsdaten nur an Rahmen dieser Herkunft (`formular::Zahlrahmen`). */
  tabFuellen: (id: string, fuer: string, werte: Fuellen, rahmen?: string) => rufen('tab_fuellen', { id, fuer, werte, ...(rahmen && { rahmen }) }),
  oberflaecheFokussieren: () => rufen('oberflaeche_fokussieren'),
  tabStumm: (id: string, stumm: boolean) => rufen('tab_stumm', { id, stumm }),
  /** `schlafenMinuten: null`: Tabs schlafen nie (`tabs/ruhe.rs`). */
  tabsLeistung: (schlafenMinuten: number | null, verwerfen: boolean, ausnahmen: string[]) =>
    rufen('tabs_leistung', { schlafenMinuten, verwerfen, ausnahmen }),
  fensterAktion: (aktion: 'minimieren' | 'maximieren' | 'schliessen') => rufen('fenster_aktion', { aktion }),
  /** Namensblase über der Seite (`kurzinfo.rs`); `null` verbirgt sie. */
  kurzinfo: (blase: Record<string, unknown> | null) => rufen('kurzinfo', { blase }),
  downloadZeigen: (pfad: string) => rufen('download_zeigen', { pfad }),
  downloadOrdner: () => rufen<string>('download_ordner'),
  /** `seit` in Millisekunden seit 1970; ohne: alles. */
  seitendatenLoeschen: (seit?: number) => rufen('seitendaten_loeschen', { seit: seit ?? null }),
  seitenrechte: () => rufen<Seitenrecht[]>('seitenrechte'),
  seitenrechtZuruecksetzen: (art: string, herkunft: string) => rufen('seitenrecht_zuruecksetzen', { art, herkunft }),
  schildStand: () =>
    rufen<{ aktiv: boolean; listen: { name: string; alter_sekunden: number | null }[] }>('schild_stand'),
  /** Seit der Installation geblockt, nur diese zwei Zahlen (`schild/gesamt.rs`). */
  schildGesamt: () => rufen<{ werbung: number; tracker: number }>('schild_gesamt'),
  schutzStand: () => rufen<SchutzStand>('schutz_stand'),
  /** Strengeres gilt sofort, Lockeres wird ein Antrag (`schild/schutz.rs`). Wirft `ohne_netz`, `gebunden`, `abkuehlen`. */
  schutzAendern: (regeln: SchutzRegeln) => rufen<SchutzStand>('schutz_aendern', { regeln }),
  /** Bindet den Schutz für `tage` Tage oder verlängert. */
  schutzBinden: (tage: number) => rufen<SchutzStand>('schutz_binden', { tage }),
  schutzAbbrechen: () => rufen<SchutzStand>('schutz_abbrechen'),
  /** Wirft `ohne_netz`, `zu_frueh`, `verfallen` oder `kein_antrag`. */
  schutzBestaetigen: () => rufen<SchutzStand>('schutz_bestaetigen'),
  /** Was das Such-Widget oder ein Link aus einer anderen App angestoßen hat, einmal (`widget.rs`). */
  widgetStart: () => rufen<WidgetStart | null>('widget_start'),
  widgetStand: (bildsuche: boolean) => rufen('widget_stand', { bildsuche }),
  widgetLage: () => rufen<'liegt' | 'anheftbar' | 'nein'>('widget_lage'),
  /** Der Startbildschirm fragt danach selbst; `false`, wenn er die Bitte nicht annimmt. */
  widgetAnheften: () => rufen<boolean>('widget_anheften'),
  tastaturZeigen: () => rufen('tastatur_zeigen'),
  /** Ist der Browser der Standardbrowser? Android und Windows, sonst `false`. */
  standardbrowser: () => rufen<boolean>('standardbrowser'),
  /** Android fragt selbst, Windows zeigt seine Standard-Apps; danach der Stand. */
  standardbrowserWerden: () => rufen<boolean>('standardbrowser_werden'),
  /** Die Standard-Apps in den Einstellungen des Systems. */
  standardbrowserEinstellungen: () => rufen<boolean>('standardbrowser_einstellungen'),
  /** Schickt das Foto des Widgets im Tab `id` an die Bildsuche. */
  bildsuche: (id: string, privat: boolean, bild: { url: string; feld: string; base64?: boolean }) =>
    nacheinander('bildsuche', { id, privat, url: bild.url, feld: bild.feld, base64: bild.base64 ?? false }),
  /** Die neuere Version aus dem jüngsten Release, sonst `null` (`aktualisieren.rs`). */
  updatePruefen: () => rufen<string | null>('update_pruefen'),
  /** Lädt, prüft die Signatur und startet den Installer; der Browser endet dabei. */
  updateInstallieren: () => rufen('update_installieren'),
}

export type WidgetStart =
  | { art: 'suche' }
  | { art: 'text'; text: string }
  | { art: 'bild' }
  /** Ein Link aus einer anderen App (`WidgetPlugin.linkAufnehmen`). */
  | { art: 'link'; url: string }

/** Ein neuer Anstoß des Such-Widgets, während der Browser läuft. */
export async function widgetAnstoesse(rueckruf: () => void): Promise<() => void> {
  if (!istTauri()) return () => {}
  return listen('msb:widget', () => rueckruf())
}

export type SchutzKategorie = 'erwachsene' | 'gluecksspiel' | 'sozial' | 'spiele' | 'shopping'

/** Die Regeln des Jugend- und Suchtschutzes (`Regeln` in `schild/schutz.rs`). */
export interface SchutzRegeln {
  aktiv: boolean
  kategorien: SchutzKategorie[]
  eigene: string[]
  ausnahmen: string[]
  /** 24, 72 oder 168 (`WARTEZEITEN` in `schutz.rs`). */
  wartezeit_stunden: number
}

export interface SchutzStand {
  regeln: SchutzRegeln
  /** Eine Lockerung: bis `rest_sekunden` wartet sie, danach lässt sie sich `fenster_sekunden` lang bestätigen. */
  antrag: { ziel: SchutzRegeln; rest_sekunden: number; fenster_sekunden: number } | null
  /** So lange lässt sich nichts lockern. */
  gebunden_sekunden: number
  /** So lange kein neuer Antrag (nach Abbruch oder Verfall). */
  abkuehlen_sekunden: number
  /** Tage ohne gesperrte Seite; bleibt auf dem Gerät. */
  serie: { tage: number; rekord: number }
  /** Die Schutzdatei war nicht zu lesen; es gilt alles gesperrt. */
  beschaedigt: boolean
  listen: { kategorie: SchutzKategorie; nachgeladen: boolean; alter_sekunden: number | null }[]
}

/** Rust meldet `msb:schutz`, wenn sich die geltenden Regeln ändern. */
export async function schutzEreignisse(rueckruf: () => void): Promise<() => void> {
  if (!istTauri()) return () => {}
  return listen('msb:schutz', () => rueckruf())
}

/** Die Felder der Gerätekonfiguration (`konfig.rs`). */
export interface BrowserKonfig {
  backend_url: string | null
  eingerichtet: boolean
  schild_aktiv: boolean
  schild_ausnahmen: string[]
  download_ordner: string | null
  download_fragen: boolean
  vergessen_beim_schliessen: boolean
}

export const konfig = {
  laden: () => rufen<BrowserKonfig>('konfig_laden'),
  aendern: (felder: Partial<BrowserKonfig>) => rufen<BrowserKonfig>('konfig_aendern', { felder }),
}
