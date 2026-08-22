/**
 * Typisierte Brücke zu den Rust-Commands — die einzige Stelle, an der
 * `invoke` für Gerätefunktionen aufgerufen wird. Komponenten importieren
 * Funktionen, keine Command-Namen: ein umbenanntes Command fällt hier auf,
 * nicht irgendwo in der UI. (Ausnahme: die Token-Commands, die gehören zum
 * Transport und liegen in `transport.ts`.)
 */
import { invoke } from '@tauri-apps/api/core'

export type AgentStatus = 'bereit' | 'hoert' | 'denkt' | 'spricht'

// ── App-Konfiguration (keine Geheimnisse — Tokens liegen im OS-Tresor) ───

export interface AppKonfig {
  backend_url: string | null
  sandbox_pfad: string | null
  eingerichtet: boolean
  /** Globaler Hotkey fürs Hauptfenster — `null` heißt bewusst deaktiviert. */
  hotkey_fenster: string | null
  /** Globaler Hotkey für die Sprachsitzung im Overlay. */
  hotkey_sprache: string | null
  /** Ob das Wake-Word-Lauschen laufen soll — der eine, persistente Schalter. */
  wakeword_aktiv: boolean
  /** Auf welches Wort das Modell trainiert wurde. */
  wakeword_wort: string | null
  /** Bevorzugtes Eingabegerät (Name) — `null` folgt dem Windows-Standard. */
  audio_eingabe: string | null
  /** Bevorzugtes Ausgabegerät für die Stimme der KI. */
  audio_ausgabe: string | null
  /** Wake-Word-Empfindlichkeit (rustpotter-Schwelle, geklemmt auf 0,30–0,60). */
  wakeword_schwelle: number
  /** Echounterdrückung der Sprachsitzung (Chromium-Verarbeitung, lokal). */
  audio_echo: boolean
  /** Rauschunterdrückung der Sprachsitzung (Chromium-Verarbeitung, lokal). */
  audio_rauschen: boolean
  /** Automatische Pegelanpassung der Sprachsitzung (Chromium, lokal). */
  audio_autogain: boolean
  /** Software-Eingangsverstärkung der Sprachsitzung, 1 = neutral. */
  audio_verstaerkung: number
}

export async function konfigLaden(): Promise<AppKonfig> {
  return await invoke<AppKonfig>('konfig_laden')
}

export async function konfigSpeichern(konfig: AppKonfig): Promise<void> {
  await invoke('konfig_speichern', { konfig })
}

/** Setzt den Tray-Status (Icon-Farbe + Tooltip). */
export async function setzeStatus(status: AgentStatus): Promise<void> {
  await invoke('setze_status', { status })
}

/** Zeigt oder versteckt das Overlay-Fenster (Sprachblase). */
export async function overlaySichtbar(sichtbar: boolean): Promise<void> {
  await invoke('overlay_sichtbar', { sichtbar })
}

/**
 * Der Overlay-Testknopf: startet die Sprachsitzung im Overlay bzw. beendet
 * sie — exakt derselbe Weg wie der Sprach-Hotkey und das Wake-Word.
 */
export async function overlayTesten(): Promise<void> {
  await invoke('overlay_testen')
}

// ── Audiogeräte ──────────────────────────────────────────────────────────

export interface AudioGeraete {
  eingaenge: string[]
  ausgaenge: string[]
  /** Der aktuelle Windows-Standard — zur Anzeige hinter „Windows-Standard". */
  standard_eingang: string | null
  standard_ausgang: string | null
}

export async function audioGeraete(): Promise<AudioGeraete> {
  return await invoke<AudioGeraete>('audio_geraete')
}

/** Senkt Hintergrundton um 60 % ab (an=true) bzw. stellt ihn wieder her. */
export async function duckingSetzen(an: boolean): Promise<void> {
  await invoke('ducking', { an })
}

/**
 * Stellt beide globalen Hotkeys um und speichert sie; `null` = deaktiviert.
 * Eine belegte oder ungültige Kombination kommt als Fehler zurück, und der
 * alte Stand bleibt registriert.
 */
export async function hotkeysSetzen(
  fenster: string | null,
  sprache: string | null,
): Promise<void> {
  await invoke('hotkeys_setzen', { fenster, sprache })
}

/** Beendet die App wirklich — der eine Ausgang des Schließen-Dialogs. */
export async function appBeenden(): Promise<void> {
  await invoke('app_beenden')
}

/** Versteckt das Hauptfenster im Tray — der andere Ausgang. */
export async function hauptfensterVerstecken(): Promise<void> {
  await invoke('hauptfenster_verstecken')
}

// ── Wake-Word ────────────────────────────────────────────────────────────

export interface WakewordStand {
  aufnahmen: number
  trainiert: boolean
  lauscht: boolean
  /** Ob das Lauschen laufen soll (konfig.json) — der persistente Schalter. */
  aktiv?: boolean
  /** Auf welches Wort trainiert wurde — für den Neukalibrierungs-Hinweis. */
  wort?: string | null
  /** Name des Eingabegeräts — `null`, wenn keines gefunden wurde. */
  geraet?: string | null
}

export async function wakewordStand(): Promise<WakewordStand> {
  return await invoke<WakewordStand>('wakeword_stand')
}

/**
 * Nimmt Kalibrierungs-Aufnahme Nr. `nummer` auf. Blockiert, bis gesprochen
 * wurde (höchstens ~7,5 s) — eine stille Runde ist ein Fehler, keine Aufnahme.
 */
export async function wakewordAufnehmen(nummer: number): Promise<string> {
  return await invoke<string>('wakeword_aufnehmen', { nummer })
}

export async function wakewordTrainieren(wort: string): Promise<void> {
  await invoke('wakeword_trainieren', { wort })
}

export async function wakewordLauschen(an: boolean): Promise<void> {
  await invoke('wakeword_lauschen', { an })
}

export async function wakewordZuruecksetzen(): Promise<void> {
  await invoke('wakeword_zuruecksetzen')
}

// ── Aufträge vom Panel ───────────────────────────────────────────────────

/**
 * Führt einen Auftrag aus (Dateien, Programm, Übernahme).
 *
 * `null` heißt: das Ergebnis kommt später. Genau ein Fall — die Bitte um die
 * Übernahme, über die ein Mensch an der Bestätigungskarte entscheidet.
 */
export async function auftragAusfuehren(
  werkzeug: string,
  argumente: Record<string, unknown>,
): Promise<Record<string, unknown> | null> {
  return await invoke<Record<string, unknown> | null>('auftrag_ausfuehren', {
    werkzeug,
    argumente,
  })
}

// ── Übernahme von Maus und Tastatur ──────────────────────────────────────
//
// Die Freigabe liegt in Rust und nicht hier: eine Frist im Speicher des
// Prozesses, die nur ein Klick des Menschen setzt. Diese Funktionen bitten
// darum, sie halten sie nicht.

export async function uebernahmeFreigeben(minuten: number): Promise<void> {
  await invoke('uebernahme_freigeben', { minuten })
}

export async function uebernahmeWiderrufen(): Promise<void> {
  await invoke('uebernahme_widerrufen')
}

/** Restlaufzeit der Freigabe in Sekunden; 0 heißt: keine. */
export async function uebernahmeRest(): Promise<number> {
  return await invoke<number>('uebernahme_rest')
}

// ── Deinstallation ───────────────────────────────────────────────────────

export interface Aufraeumbericht {
  konfiguration_entfernt: boolean
  sprachdaten_entfernt: boolean
  tresor_geleert: boolean
  autostart_entfernt: boolean
  /** Der Ordner des Benutzers bleibt — er gehört ihm, nicht der App. */
  sandbox_bleibt: string | null
  fehler: string[]
}

export async function deinstallationAufraeumen(): Promise<Aufraeumbericht> {
  return await invoke<Aufraeumbericht>('deinstallation_aufraeumen')
}

/** Startet den Windows-Uninstaller und beendet die App. */
export async function deinstallationStarten(): Promise<void> {
  await invoke('deinstallation_starten')
}
