/**
 * Typisierte Brücke zu den Rust-Commands — die einzige Stelle, an der
 * `invoke` für Gerätefunktionen aufgerufen wird. Komponenten importieren
 * Funktionen, keine Command-Namen: ein umbenanntes Command fällt hier auf,
 * nicht irgendwo in der UI. (Ausnahme: die Token-Commands, die gehören zum
 * Transport und liegen in `transport.ts`.)
 */
import { invoke } from '@tauri-apps/api/core'
import { setRuntimeApiUrl } from '@/config/api'

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
  /** Ob Computer-Use (Maus, Tastatur, Bildschirmsteuerung) durch die KI erlaubt ist. */
  computer_use_aktiv: boolean
  /** Ob Artefakt-Installationen (Software, Mods, Installer) durch die KI erlaubt sind. */
  artifact_install_aktiv: boolean
  /** Maximales Download-Limit pro Artefakt in Bytes (Standard 10 GiB, max 100 GiB). */
  max_download_bytes: number
  /** Vom Benutzer freigegebene Suchwurzeln für Spiele und Software. */
  search_roots: string[]
  /** Ob der Splashscreen beim Erststart bereits gesehen wurde. */
  splash_gesehen?: boolean
  /** Ob der Autostart beim Systemstart (Windows / Android) aktiviert ist. */
  autostart_aktiv?: boolean
}

export async function konfigLaden(): Promise<AppKonfig> {
  const konfig = await invoke<AppKonfig>('konfig_laden')
  if (konfig.backend_url) {
    setRuntimeApiUrl(konfig.backend_url)
  }
  return konfig
}

export async function konfigSpeichern(konfig: AppKonfig): Promise<void> {
  if (konfig.backend_url) {
    setRuntimeApiUrl(konfig.backend_url)
  }
  await invoke('konfig_speichern', { konfig })
}

export async function sandboxVerfuegbar(): Promise<boolean> {
  return await invoke<boolean>('sandbox_verfuegbar')
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
  /**
   * Ob die vorhandene Kalibrierung aus einem älteren Schnittverfahren
   * stammt. Bis zum 23.08.2026 war jede Aufnahme fest 2,5 s lang und
   * bestand damit überwiegend aus Raumton; daran ändert keine Einstellung
   * etwas, es hilft nur neu einsprechen.
   */
  veraltet?: boolean
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
 * Führt einen Auftrag aus (Dateien, Programm, Übernahme, Aufräumen).
 *
 * `null` heißt: das Ergebnis kommt später, weil ein Mensch an einer
 * Bestätigungskarte entscheidet — die Bitte um die Übernahme, und Aufräumen
 * bei ausgeschaltetem autonomem Modus.
 *
 * `auftragId` wird nur durchgereicht: Rust legt sie in die Nutzlast genau
 * dieser Karten, damit die Antwort des Menschen zu dem Auftrag gehört, der
 * gefragt hat — und nicht zu dem, den das Fenster sich gerade gemerkt hat.
 */
export async function auftragAusfuehren(
  werkzeug: string,
  argumente: Record<string, unknown>,
  auftragId?: string,
): Promise<Record<string, unknown> | null> {
  return await invoke<Record<string, unknown> | null>('auftrag_ausfuehren', {
    werkzeug,
    argumente,
    // camelCase, obwohl der Parameter in Rust `auftrag_id` heißt: Tauri bildet
    // Command-Argumente standardmäßig auf camelCase ab und sucht exakt diesen
    // Schlüssel. Ein `auftrag_id` an dieser Stelle käme drüben als `None` an —
    // ohne Fehler, denn das Argument ist freiwillig.
    auftragId,
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

// ── Aufräumen außerhalb der Sandbox ──────────────────────────────────────
//
// Wie bei der Übernahme liegt der Vorgang in Rust und nicht hier: der Plan
// wartet dort, diese Funktionen entscheiden nur über ihn. Die Liste, die die
// Karte zeigt, ist deshalb reine Anzeige — bestätigt wird der Plan, den Rust
// hält, nicht der, den das Fenster gerade darstellt.

/** Ein einzelner Posten auf der Aufräumkarte. */
export interface Aufraeumposten {
  pfad: string
  /** Größe in Bytes; `null`, wenn sie nicht ermittelt werden konnte. */
  bytes: number | null
  /**
   * Ob `bytes` nur eine Untergrenze ist. Die Messung in Rust hat eine
   * Zeitgrenze (`aufraeumen::MESSFRIST`); bei einem sehr tiefen Baum oder
   * einem langsamen Netzpfad bricht sie ab und meldet, was sie bis dahin
   * gezaehlt hat. Die Karte schreibt dann „mindestens" davor.
   */
  ungefaehr?: boolean
  /** `frei` | `muell` | `system` — nur `system` ist heikel. */
  zone: string
}

/** Die Nutzlast von `mss:aufraeumen-anfrage` — was die Karte zeigt. */
export interface Aufraeumplan {
  /** `papierkorb` | `endgueltig` | `papierkorb_leeren`. */
  aktion: string
  grund: string
  posten: Aufraeumposten[]
  /**
   * Der Auftrag, aus dem die Frage stammt. Fehlt bei einer App, deren
   * Rust-Hälfte die Kennung noch nicht mitschickt — dann bleibt der Karte
   * nur die Kennung, die die Auftragsschleife gerade hält.
   */
  auftrag_id?: string | null
}

/** Führt den wartenden Plan aus und gibt sein Ergebnis zurück. */
export async function aufraeumenBestaetigen(): Promise<Record<string, unknown>> {
  return await invoke<Record<string, unknown>>('aufraeumen_bestaetigen')
}

/** Verwirft den wartenden Plan. Es wird nichts angefasst. */
export async function aufraeumenAblehnen(): Promise<void> {
  await invoke('aufraeumen_ablehnen')
}

// ── Allgemeine Desktop-Aktionen (Nicht-Autonom) ──────────────────────────

export interface DesktopAktionAnfrage {
  auftrag_id: string
  werkzeug: string
  titel: string
  beschreibung: string
  argumente: Record<string, unknown>
}

/** Bestätigt eine wartende allgemeine Desktop-Aktion und liefert das Ergebnis. */
export async function desktopAktionBestaetigen(
  auftragId: string,
): Promise<Record<string, unknown>> {
  return await invoke<Record<string, unknown>>('desktop_aktion_bestaetigen', {
    auftragId,
  })
}

/** Lehnt eine wartende allgemeine Desktop-Aktion ab. */
export async function desktopAktionAblehnen(auftragId: string): Promise<void> {
  await invoke('desktop_aktion_ablehnen', { auftragId })
}

/** Öffnet eine externe URL sicher im Standard-Browser des Benutzers. */
export async function oeffneBrowser(url: string): Promise<void> {
  await invoke('oeffne_browser', { url })
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

/**
 * Human Error Guard: Aktiviert oder deaktiviert den Windows-Hardware- und
 * Software-Schutz, um das Passwort-Manager-Fenster vor KI-Screenshots
 * (Computer-Use) zu verbergen bzw. zu schwärzen.
 */
export async function setzeTresorSchutz(aktiv: boolean): Promise<void> {
  try {
    await invoke('setze_tresor_schutz', { aktiv })
  } catch {
    // Stiller No-Op im Web oder wenn Tauri-Befehl nicht bereitsteht
  }
}

/**
 * Prüft, ob Windows Hello oder eine native biometrische Authentifizierung
 * auf dem Endgerät verfügbar und eingerichtet ist.
 */
export async function pruefeBiometrieVerfuegbar(): Promise<boolean> {
  try {
    return await invoke<boolean>('biometrie_verfuegbar')
  } catch {
    return false
  }
}

/**
 * Öffnet den nativen Windows Hello Bestätigungsdialog (Fingerabdruck / Gesicht / PIN).
 */
export async function verifiziereBiometrie(nachricht?: string): Promise<boolean> {
  try {
    return await invoke<boolean>('biometrie_verifizieren', { nachricht })
  } catch {
    return false
  }
}

/**
 * Speichert das biometrisch geschützte Schlüsselgeheimnis im Windows Credential Manager.
 */
export async function biometrieSpeichern(geheimnis: string): Promise<void> {
  await invoke('biometrie_speichern', { geheimnis })
}

/**
 * Fordert Windows Hello an und liefert das Schlüsselgeheimnis aus dem Windows Credential Store
 * erst nach erfolgreicher Authentifizierung zurück.
 */
export async function biometrieEntsperren(nachricht?: string): Promise<string> {
  return await invoke<string>('biometrie_entsperren', { nachricht })
}

/**
 * Löscht das biometrische Schlüsselgeheimnis aus dem Windows Credential Manager.
 */
export async function biometrieLoeschen(): Promise<void> {
  try {
    await invoke('biometrie_loeschen')
  } catch {}
}

// ── Automatischer Updater ────────────────────────────────────────────────

export interface UpdateInfo {
  verfuegbar: boolean
  aktuelle_version: string
  neue_version: string | null
  download_url: string | null
  notizen: string | null
  ist_android: boolean
}

export interface UpdateStatusEvent {
  status: 'laedt' | 'bereit' | 'installiert_android' | 'fehler'
  prozent?: number
  fehler?: string
}

export async function updatePruefen(): Promise<UpdateInfo> {
  return await invoke<UpdateInfo>('update_pruefen')
}

export async function updateInstallieren(): Promise<void> {
  await invoke('update_installieren')
}

export async function appNeuStarten(): Promise<void> {
  await invoke('app_neu_starten')
}

